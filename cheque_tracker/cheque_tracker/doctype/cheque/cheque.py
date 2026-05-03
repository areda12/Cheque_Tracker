# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today

from cheque_tracker.cheque_tracker.doctype.cheque import cheque_financial
from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import (
    mark_leaf_issued,
    release_leaf,
    reserve_leaf,
)


class Cheque(Document):
    # ------------------------------------------------------------------ #
    #  Life-cycle hooks (wired via doc_events in hooks.py)                #
    # ------------------------------------------------------------------ #

    def before_insert(self):
        # Initialise current_holder during insert so it lands in the same
        # row write as the rest of the doc — no post-insert UPDATE that
        # would bump `modified` and desync the in-memory doc.
        if not self.current_holder:
            self.current_holder = frappe.session.user

    def after_insert(self):
        # Append the Created event (needs the assigned name), then
        # reload to resync the in-memory `modified` with the row that
        # _flush_events just persisted. Without the reload, a subsequent
        # submit() in the same Python flow throws TimestampMismatchError.
        self._append_event("Created", notes=f"Cheque {self.name} created.")
        self._flush_events()
        self.reload()

    def before_save(self):
        if self.cheque_type == "Outgoing":
            self._handle_outgoing_leaf_reservation()
        if self.cheque_type == "Incoming" and not self.drawee_bank:
            frappe.throw(
                _("Drawee Bank is required for Incoming cheques."),
                frappe.ValidationError,
            )
        self._validate_outgoing_cheque_no()

    def before_submit(self):
        # Submit transitions Draft → Received for both directions with NO
        # GL effect. The Clearance JE on workflow Clear is the only GL
        # posting in the lifecycle.
        self.status = "Received"

    def on_submit(self):
        if self.cheque_type == "Outgoing":
            self._mark_leaf_issued_on_submit()
        event_type = "Received" if self.cheque_type == "Incoming" else "Created"
        self._append_event(event_type, notes="Cheque submitted.")
        self._flush_events()

    def on_update(self):
        # Workflow-driven status transitions on submitted Cheques are handled
        # in on_update_after_submit; Frappe routes saves to that hook for
        # docstatus=1, never to on_update. Drafts have no GL-bearing
        # transitions, so this method is intentionally a no-op.
        return

    def on_cancel(self):
        # docstatus 1→2 cancellation: only the Clearance JE may exist (for
        # either direction). Submit / Deposit / Hand Over / Bounce / Return /
        # workflow-Cancel never post GL, so there is nothing else to reverse.
        if self.clearance_je:
            cheque_financial.cancel_clearance_je(self)

        if self.cheque_type == "Outgoing" and self.cheque_leaf:
            leaf_status = frappe.db.get_value(
                "Cheque Leaf", self.cheque_leaf, "leaf_status"
            )
            if leaf_status in ("Reserved", "Issued"):
                release_leaf(
                    self.cheque_leaf,
                    status="Voided",
                    void_reason=f"Cheque {self.name} cancelled.",
                )

        self._append_event("Cancelled", notes="Cheque cancelled.")
        frappe.db.set_value("Cheque", self.name, "status", "Cancelled")
        self._flush_events()

    def on_update_after_submit(self):
        """Audit-log allow_on_submit field changes AND fire workflow-driven
        GL side effects. Runs on every save to a docstatus=1 doc."""
        before = self.get_doc_before_save()
        if not before:
            return

        old_ba = before.get("bank_account")
        new_ba = self.bank_account
        if old_ba != new_ba:
            if new_ba:
                notes = (
                    f"Bank Account assigned: {new_ba}"
                    + (f" (previously: {old_ba})" if old_ba else "")
                    + f" — by {frappe.session.user}."
                )
            else:
                notes = (
                    f"Bank Account cleared (was: {old_ba}) — by {frappe.session.user}."
                )
            self._append_event("Note", notes=notes)

        old_ca = before.get("cash_account")
        new_ca = self.cash_account
        if old_ca != new_ca:
            if new_ca:
                notes = (
                    f"Cash Account assigned: {new_ca}"
                    + (f" (previously: {old_ca})" if old_ca else "")
                    + f" — by {frappe.session.user}."
                )
            else:
                notes = (
                    f"Cash Account cleared (was: {old_ca}) — by {frappe.session.user}."
                )
            self._append_event("Note", notes=notes)

        old_ct = before.get("clearance_type")
        new_ct = self.clearance_type
        if old_ct != new_ct:
            self._append_event(
                "Note",
                notes=f"Clearance Type changed from {old_ct or 'unset'} to {new_ct} — by {frappe.session.user}.",
            )

        self._flush_events()

        # Workflow-driven GL side effects.
        # has_value_changed reads from _doc_before_save — set reliably by
        # the framework before this hook runs, even on workflow saves.
        if not self.has_value_changed("status"):
            return

        old, new = before.status, self.status

        # The only GL transition — direction-aware:
        #   Incoming: Deposited   → Cleared
        #   Outgoing: Handed Over → Cleared
        # All other transitions (Bounce, Return, Cancel Cheque) are status-only —
        # nothing was posted on Submit, so there is nothing to reverse.
        expected_pre_clear = "Deposited" if self.cheque_type == "Incoming" else "Handed Over"
        if new == "Cleared" and old == expected_pre_clear:
            if not self.cleared_date:
                frappe.throw(_("Set Cleared Date before marking as Cleared."))
            if not self.bank_account:
                frappe.throw(_("Set Bank Account before marking as Cleared."))
            cheque_financial.make_clearance_je(self)

    # ------------------------------------------------------------------ #
    #  Leaf reservation (Outgoing)                                         #
    # ------------------------------------------------------------------ #

    def _handle_outgoing_leaf_reservation(self):
        if not self.cheque_book:
            frappe.throw(
                _("Cheque Book is required for Outgoing cheques."),
                frappe.ValidationError,
            )
        if self.cheque_leaf:
            current = frappe.db.get_value("Cheque Leaf", self.cheque_leaf, "cheque")
            if current == self.name:
                return
            if current:
                frappe.throw(
                    _("Cheque Leaf {0} is already reserved for {1}.").format(
                        self.cheque_leaf, current
                    ),
                    frappe.ValidationError,
                )

        result = reserve_leaf(self.cheque_book, self.name, frappe.session.user)
        self.cheque_leaf = result["name"]
        self.cheque_no   = result["cheque_no"]

    def _validate_outgoing_cheque_no(self):
        if self.cheque_type != "Outgoing" or not self.cheque_leaf:
            return
        leaf_no = frappe.db.get_value("Cheque Leaf", self.cheque_leaf, "cheque_no")
        if leaf_no and self.cheque_no != leaf_no:
            frappe.throw(
                _(
                    "Cheque No for Outgoing cheques is system-controlled "
                    "(expected {0}, got {1})."
                ).format(leaf_no, self.cheque_no),
                frappe.ValidationError,
            )

    def _mark_leaf_issued_on_submit(self):
        if not self.cheque_leaf:
            frappe.throw(
                _("No leaf reserved. Save the cheque first to reserve a leaf."),
                frappe.ValidationError,
            )
        data = frappe.db.get_value(
            "Cheque Leaf", self.cheque_leaf, ["leaf_status", "cheque"], as_dict=True
        )
        if data.leaf_status != "Reserved":
            frappe.throw(
                _(
                    "Cheque Leaf {0} is not Reserved (currently: {1})."
                ).format(self.cheque_leaf, data.leaf_status),
                frappe.ValidationError,
            )
        if data.cheque != self.name:
            frappe.throw(
                _(
                    "Cheque Leaf {0} is reserved for {1}, not {2}."
                ).format(self.cheque_leaf, data.cheque, self.name),
                frappe.ValidationError,
            )
        mark_leaf_issued(self.cheque_leaf)

    # ------------------------------------------------------------------ #
    #  Status management (called by workflow / API)                        #
    # ------------------------------------------------------------------ #

    def log_status_change(self, new_status: str, notes: str = ""):
        old_status = self.status
        updates = {"status": new_status}
        if new_status == "Cleared":
            updates["cleared_date"] = today()
        frappe.db.set_value("Cheque", self.name, updates)
        self.status = new_status

        EVENT_MAP = {
            "Received": "Received", "In Safe": "In Safe", "Deposited": "Deposited",
            "Handed Over": "Handed Over",
            "Cleared": "Cleared", "Bounced": "Bounced",
            "Returned": "Returned", "Cancelled": "Cancelled", "Replaced": "Replaced",
        }
        self._append_event(
            EVENT_MAP.get(new_status, "Note"),
            notes=notes or f"Status changed from {old_status} to {new_status}.",
        )
        self._flush_events()

    def hand_over(self, to_user: str, location: str = "", notes: str = ""):
        old_holder = self.current_holder
        frappe.db.set_value(
            "Cheque", self.name,
            {"current_holder": to_user, "custody_location": location},
        )
        self._append_event(
            "Handed Over",
            from_holder=old_holder,
            to_holder=to_user,
            location=location,
            notes=notes,
        )
        self._flush_events()

    # ------------------------------------------------------------------ #
    #  Event helpers                                                       #
    # ------------------------------------------------------------------ #

    def _append_event(
        self,
        event_type: str,
        *,
        from_holder=None,
        to_holder=None,
        location=None,
        notes=None,
        attachment=None,
    ):
        if not isinstance(getattr(self, "events", None), list):
            self.events = []
        self.append(
            "events",
            {
                "event_type":     event_type,
                "event_datetime": now_datetime(),
                "from_holder":    from_holder,
                "to_holder":      to_holder or frappe.session.user,
                "location":       location,
                "notes":          notes,
                "attachment":     attachment,
            },
        )

    def _flush_events(self):
        new_events = [e for e in (self.events or []) if not e.get("name")]
        if not new_events:
            return

        persisted = frappe.get_doc("Cheque", self.name)

        if persisted.docstatus == 2:
            base_idx = (max((e.idx for e in persisted.events), default=0)) + 1
            for i, ev in enumerate(new_events):
                child = frappe.new_doc("Cheque Event")
                child.update({
                    "parent":            persisted.name,
                    "parenttype":        "Cheque",
                    "parentfield":       "events",
                    "idx":               base_idx + i,
                    "event_type":        ev.event_type,
                    "event_datetime":    ev.event_datetime or frappe.utils.now_datetime(),
                    "from_holder":       ev.from_holder,
                    "to_holder":         ev.to_holder,
                    "location":          ev.location,
                    "reference_doctype": ev.reference_doctype,
                    "reference_name":    ev.reference_name,
                    "notes":             ev.notes,
                })
                child.db_insert()
            return

        for ev in new_events:
            persisted.append("events", ev.as_dict())
        persisted.flags.ignore_permissions = True
        persisted.flags.ignore_validate_update_after_submit = True
        persisted.save()


# ------------------------------------------------------------------ #
#  Whitelisted API                                                     #
# ------------------------------------------------------------------ #

# Workflow-role gating for change_cheque_status. Pairs (from_status,
# to_status) not present here fall through to the per-status validation
# logic in _validate_transition.
_TRANSITION_ROLES = {
    ("Draft", "Received"):         {"Treasury User", "System Manager"},
    # Incoming-only forwards
    ("Received", "Deposited"):     {"Treasury User", "System Manager"},
    ("In Safe", "Deposited"):      {"Treasury User", "System Manager"},
    ("Deposited", "Cleared"):      {"Accounts User", "System Manager"},
    ("Deposited", "Bounced"):      {"Treasury User", "System Manager"},
    ("Deposited", "Returned"):     {"Treasury User", "System Manager"},
    # Outgoing-only forwards
    ("Received", "Handed Over"):   {"Treasury User", "System Manager"},
    ("In Safe", "Handed Over"):    {"Treasury User", "System Manager"},
    ("Handed Over", "Cleared"):    {"Accounts User", "System Manager"},
    ("Handed Over", "Bounced"):    {"Treasury User", "System Manager"},
    ("Handed Over", "Returned"):   {"Treasury User", "System Manager"},
    # Shared transitions
    ("Received", "In Safe"):       {"Treasury User", "System Manager"},
    ("Received", "Returned"):      {"Treasury User", "System Manager"},
    ("Received", "Cancelled"):     {"Treasury User", "System Manager"},
    ("In Safe", "Returned"):       {"Treasury User", "System Manager"},
    ("In Safe", "Cancelled"):      {"Treasury User", "System Manager"},
    ("Bounced", "Replaced"):       {"Treasury User", "System Manager"},
}


@frappe.whitelist()
def change_cheque_status(cheque_name: str, new_status: str, notes: str = ""):
    """Workflow / UI transition endpoint (non-financial status changes)."""
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=doc, throw=True)
    _validate_transition(doc, new_status, notes)
    doc.log_status_change(new_status, notes=notes)
    return {"status": "ok", "new_status": new_status}


def _validate_transition(doc, new_status: str, notes: str):
    allowed_roles = _TRANSITION_ROLES.get((doc.status, new_status))
    if allowed_roles is not None:
        user_roles = set(frappe.get_roles(frappe.session.user))
        if not (user_roles & allowed_roles):
            frappe.throw(
                _(
                    "You do not have permission to transition this cheque "
                    "from {0} to {1}. Allowed roles: {2}."
                ).format(
                    doc.status, new_status, ", ".join(sorted(allowed_roles))
                ),
                frappe.PermissionError,
                title=_("Insufficient role for cheque transition"),
            )

    if new_status in ("In Safe", "Deposited", "Handed Over"):
        if not doc.company or not doc.party or not doc.amount:
            frappe.throw(
                _("Company, Party, and Amount are required before moving to {0}.").format(
                    new_status
                ),
                frappe.ValidationError,
            )
    if doc.clearance_type == "Cash" and new_status == "Deposited":
        frappe.throw(
            _("Cannot mark as {0} when Clearance Type is Cash. "
              "Cash cheques go directly from In Safe → Cleared via the clearance entry.").format(
                new_status
            ),
            frappe.ValidationError,
        )
    if new_status == "Deposited":
        if doc.cheque_type != "Incoming":
            frappe.throw(
                _("Only Incoming cheques can be Deposited."),
                frappe.ValidationError,
            )
        if not doc.bank_account:
            frappe.throw(
                _("Bank Account is required before marking as Deposited."),
                frappe.ValidationError,
            )
    if new_status == "Handed Over" and doc.cheque_type != "Outgoing":
        frappe.throw(
            _("Only Outgoing cheques can be Handed Over."),
            frappe.ValidationError,
        )


@frappe.whitelist()
def hand_over_cheque(
    cheque_name: str, to_user: str, location: str = "", notes: str = ""
):
    """Transfer physical custody."""
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=doc, throw=True)
    doc.hand_over(to_user=to_user, location=location, notes=notes)
    return {"status": "ok"}
