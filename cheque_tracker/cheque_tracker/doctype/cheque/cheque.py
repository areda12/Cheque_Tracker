# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today

from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import (
    mark_leaf_issued,
    release_leaf,
    reserve_leaf,
)

# Fields that may not be edited once any submitted accounting doc exists
# bank_account is intentionally excluded: it may be set/changed on submitted cheques
# (the bank is often unknown at receipt time). Changes are audit-logged via
# on_update_after_submit instead.
_PROTECTED_FIELDS = {"amount", "party", "party_type", "company", "cheque_no"}


class Cheque(Document):
    # ------------------------------------------------------------------ #
    #  Life-cycle hooks (wired via doc_events in hooks.py)                #
    # ------------------------------------------------------------------ #

    def after_insert(self):
        self._append_event("Created", notes=f"Cheque {self.name} created.")
        frappe.db.set_value("Cheque", self.name, "current_holder", frappe.session.user)
        self._flush_events()

    def before_save(self):
        if self.cheque_type == "Outgoing":
            self._handle_outgoing_leaf_reservation()
        if self.cheque_type == "Incoming" and not self.drawee_bank:
            frappe.throw(
                _("Drawee Bank is required for Incoming cheques."),
                frappe.ValidationError,
            )
        self._validate_outgoing_cheque_no()
        self._protect_fields_if_submitted_accounting_docs()

    def on_submit(self):
        if self.cheque_type == "Outgoing":
            self._mark_leaf_issued_on_submit()
        if self.cheque_type == "Incoming":
            # For incoming cheques, submitting = physically received.
            # Actual AR posting happens only via Recording Payment Entry.
            if self.status in ("Draft",):
                frappe.db.set_value("Cheque", self.name, "status", "Received")
                self.status = "Received"
        event_type = "Received" if self.cheque_type == "Incoming" else "Created"
        self._append_event(event_type, notes="Cheque submitted.")
        self._flush_events()

    def on_cancel(self):
        # Block if any submitted accounting docs exist
        if self._has_submitted_accounting_docs():
            frappe.throw(
                _(
                    "Cannot cancel Cheque {0}: one or more linked accounting documents "
                    "(Payment Entry / Journal Entry) are still submitted. "
                    "Cancel those first."
                ).format(self.name),
                frappe.ValidationError,
            )
        if self.status == "Cleared":
            frappe.throw(_(
                "Cannot cancel a Cleared cheque."
            ), frappe.ValidationError)
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
        """
        Fires whenever a submitted Cheque is saved (workflow transitions,
        field edits allowed via allow_on_submit).

        Governance rule: any change to bank_account or cash_account on a
        live cheque must be logged as an immutable Cheque Event so there
        is a full audit trail of when and by whom the account was assigned.
        """
        before = self.get_doc_before_save()
        if not before:
            return

        # Audit-log bank_account changes
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

        # Audit-log cash_account changes
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

        # Audit-log clearance_type changes
        old_ct = before.get("clearance_type")
        new_ct = self.clearance_type
        if old_ct != new_ct:
            self._append_event(
                "Note",
                notes=f"Clearance Type changed from {old_ct or 'unset'} to {new_ct} — by {frappe.session.user}.",
            )

        self._flush_events()

    # ------------------------------------------------------------------ #
    #  Field protection                                                    #
    # ------------------------------------------------------------------ #

    def _protect_fields_if_submitted_accounting_docs(self):
        """Prevent editing core fields when submitted accounting docs are linked."""
        if not self.is_new() and self._has_submitted_accounting_docs():
            changed = self.get_doc_before_save()
            if not changed:
                return
            for field in _PROTECTED_FIELDS:
                old_val = changed.get(field)
                new_val = self.get(field)
                if old_val != new_val:
                    frappe.throw(
                        _(
                            "Cannot modify field '{0}' on Cheque {1} because a submitted "
                            "accounting document (Payment Entry or Journal Entry) already "
                            "references it. Cancel the accounting doc first."
                        ).format(field, self.name),
                        frappe.ValidationError,
                    )

    def _has_submitted_accounting_docs(self):
        """Return True if any submitted PE or JE is linked to this cheque."""
        checks = [
            ("Payment Entry", self.recording_payment_entry),
            ("Journal Entry", self.clearance_journal_entry),
            ("Journal Entry", self.reversal_journal_entry),
        ]
        for doctype, docname in checks:
            if docname:
                status = frappe.db.get_value(doctype, docname, "docstatus")
                if status == 1:
                    return True
        return False

    # ------------------------------------------------------------------ #
    #  Leaf reservation                                                    #
    # ------------------------------------------------------------------ #

    def _handle_outgoing_leaf_reservation(self):
        if not self.cheque_book:
            frappe.throw(
                _("Cheque Book is required for Outgoing cheques."),
                frappe.ValidationError,
            )
        # Already reserved for THIS cheque — nothing to do
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

        # Atomically reserve inside an explicit transaction
        try:
            frappe.db.begin()
            result = reserve_leaf(self.cheque_book, self.name, frappe.session.user)
            frappe.db.commit()
        except frappe.ValidationError:
            frappe.db.rollback()
            raise

        self.cheque_leaf = result["name"]
        self.cheque_no   = result["cheque_no"]

    def _validate_outgoing_cheque_no(self):
        """Block any manual override of cheque_no for Outgoing cheques."""
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
        """Transition status and append an audit event."""
        old_status = self.status
        updates = {"status": new_status}
        if new_status == "Cleared":
            updates["cleared_date"] = today()
        frappe.db.set_value("Cheque", self.name, updates)
        self.status = new_status

        EVENT_MAP = {
            "Received": "Received", "In Safe": "In Safe", "Deposited": "Deposited",
            "Presented": "Presented", "Cleared": "Cleared", "Bounced": "Bounced",
            "Returned": "Returned", "Cancelled": "Cancelled", "Replaced": "Replaced",
        }
        self._append_event(
            EVENT_MAP.get(new_status, "Note"),
            notes=notes or f"Status changed from {old_status} to {new_status}.",
        )
        self._flush_events()

    def hand_over(self, to_user: str, location: str = "", notes: str = ""):
        """Transfer physical custody and log a Handed Over event."""
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
        """
        Persist any in-memory events that have not yet been saved to DB.
        Re-fetches the doc so we only INSERT genuinely new rows.
        """
        new_events = [e for e in (self.events or []) if not e.get("name")]
        if not new_events:
            return
        persisted = frappe.get_doc("Cheque", self.name)
        for ev in new_events:
            persisted.append("events", ev)
        persisted.flags.ignore_permissions = True
        # Required for submitted docs: Frappe blocks child-table changes
        # after submission unless this flag is set.
        persisted.flags.ignore_validate_update_after_submit = True
        persisted.save()


# ------------------------------------------------------------------ #
#  Whitelisted API                                                     #
# ------------------------------------------------------------------ #

@frappe.whitelist()
def change_cheque_status(cheque_name: str, new_status: str, notes: str = ""):
    """Workflow / UI transition endpoint (non-financial status changes)."""
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=doc, throw=True)
    _validate_transition(doc, new_status, notes)
    doc.log_status_change(new_status, notes=notes)
    return {"status": "ok", "new_status": new_status}


def _validate_transition(doc, new_status: str, notes: str):
    if new_status in ("In Safe", "Deposited", "Presented"):
        if not doc.company or not doc.party or not doc.amount:
            frappe.throw(
                _("Company, Party, and Amount are required before moving to {0}.").format(
                    new_status
                ),
                frappe.ValidationError,
            )
    # Cash flow: block Deposited/Presented since these are deposit-only statuses
    if doc.clearance_type == "Cash" and new_status in ("Deposited", "Presented"):
        frappe.throw(
            _("Cannot mark as {0} when Clearance Type is Cash. "
              "Cash cheques go directly from In Safe → Cleared via the clearance entry.").format(
                new_status
            ),
            frappe.ValidationError,
        )
    if new_status == "Deposited" and not doc.bank_account:
        frappe.throw(
            _("Bank Account is required before marking as Deposited."),
            frappe.ValidationError,
        )
    if new_status == "Bounced" and not notes:
        frappe.throw(
            _("Notes / reason are required when marking a cheque as Bounced."),
            frappe.ValidationError,
        )
    if new_status == "Cleared":
        # Clearance must go through the JE hook
        frappe.throw(
            _("Cheque cannot be manually set to Cleared. "
              "Submit the Clearance Journal Entry instead."),
            frappe.ValidationError,
        )
    if new_status == "Cleared" and doc.status == "Cancelled":
        frappe.throw(_("Cannot clear a cancelled cheque."), frappe.ValidationError)
    if new_status == "Cancelled":
        if doc._has_submitted_accounting_docs():
            frappe.throw(
                _("Cannot cancel Cheque {0}: submitted accounting documents exist. "
                  "Cancel those first.").format(doc.name),
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
