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

# Status → Cheque Event type. Shared by both transition paths (workflow saves
# land in on_update_after_submit; the UI endpoint goes through
# log_status_change) so an event row looks identical whichever fired.
STATUS_EVENT_MAP = {
    "Received":    "Received",
    "Issued":      "Issued",
    "In Safe":     "In Safe",
    "Deposited":   "Deposited",
    "Endorsed":    "Endorsed",
    "Handed Over": "Handed Over",
    "Presented":   "Presented",
    "Cleared":     "Cleared",
    "Bounced":     "Bounced",
    "Returned":    "Returned",
    "Cancelled":   "Cancelled",
    "Replaced":    "Replaced",
}

# §4.1 — the two lifecycles. Incoming cheques arrive from a customer; outgoing
# ones are drawn by us. Forcing outgoing cheques through incoming language is
# what put an issued cheque in "Received" and broke the dashboard filters.
INCOMING_STATUSES = {
    "Draft", "Received", "In Safe", "Deposited", "Endorsed",
    "Cleared", "Bounced", "Returned", "Cancelled", "Replaced",
}
OUTGOING_STATUSES = {
    "Draft", "Issued", "In Safe", "Handed Over", "Presented",
    "Cleared", "Bounced", "Returned", "Cancelled", "Replaced",
}

# Statuses at which the cheque has physically left the company. current_holder
# is a Link to User and cannot name an external payee, so it is cleared rather
# than left pointing at the employee who last held it (§3.2.7a).
EXTERNAL_CUSTODY_STATUSES = {"Handed Over", "Endorsed", "Presented"}


class Cheque(Document):
    # ------------------------------------------------------------------ #
    #  Life-cycle hooks (wired via doc_events in hooks.py)                #
    # ------------------------------------------------------------------ #

    def before_insert(self):
        # Initialise current_holder during insert so it lands in the same
        # row write as the rest of the doc — no post-insert UPDATE that
        # would bump `modified` and desync the in-memory doc.
        #
        # Outgoing cheques are drawn by us, so whoever creates the record does
        # hold the physical leaf. An Incoming cheque arrives from outside and
        # may be keyed in by someone who never touched it — defaulting the
        # holder there invents a custody record that did not happen (§3.2.7c).
        if not self.current_holder and self.cheque_type == "Outgoing":
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
        self._validate_outgoing_cheque_no()
        cheque_financial.validate_duplicate_cheque(self)
        cheque_financial.validate_payment_entry_link(self)

    def before_update_after_submit(self):
        """Gate submitted-state transitions BEFORE the row is written.

        A workflow action on a submitted document routes through doc.save(), so
        `validate()` never runs (frappe/model/document.py:1402-1411) — business
        rules for these transitions have to live here or they simply do not fire.
        """
        before = self.get_doc_before_save()
        if before and before.status != self.status:
            _validate_status_requirements(self, self.status)
        cheque_financial.validate_payment_entry_link(self)

    def before_submit(self):
        # Which bank the cheque is drawn on is required to track an incoming
        # cheque, but only from submit onward. It used to be enforced on every
        # save, which made a Draft cheque impossible to create from a Payment
        # Entry (§4.5.1) — the PE records the cheque number in `reference_no`
        # but has nowhere to say which bank issued it. A clerk fills it in before
        # submitting; the guarantee is unchanged for any cheque that matters.
        if self.cheque_type == "Incoming" and not self.drawee_bank:
            frappe.throw(
                _("Drawee Bank is required for Incoming cheques."),
                frappe.ValidationError,
            )

        # Draft → Received (Incoming) / Issued (Outgoing), with no GL effect.
        #
        # This must set a status that IS a workflow state with doc_status 1.
        # frappe.model.workflow.set_workflow_state_on_action force-overwrites the
        # state field on any submit — but it returns early when the document is
        # already in a state matching the target docstatus, and before_submit runs
        # before _validate (frappe/model/document.py:479-480), so this wins.
        self.status = "Issued" if self.cheque_type == "Outgoing" else "Received"

    def on_submit(self):
        if self.cheque_type == "Outgoing":
            self._mark_leaf_issued_on_submit()

        # before_submit moves BOTH directions to "Received", so both log that
        # transition. Outgoing used to log a second "Created" here, duplicating
        # the one after_insert already wrote and leaving the real Draft →
        # Received transition unrecorded (§3.2.6).
        self._append_event(
            STATUS_EVENT_MAP[self.status],
            notes="Cheque submitted.",
        )
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

        # Replace-chain partners point at each other via replacement_cheque /
        # original_cheque. Frappe's check_no_back_links_exist would block the
        # cancel — bypass it; we handle audit-chain integrity below.
        self.flags.ignore_links = True

        # Proactively clear the partner's pointer so reports don't show a
        # dangling link to a cancelled cheque. db_set bypasses docstatus
        # checks for submitted partners.
        if self.replacement_cheque:
            try:
                replacement = frappe.get_doc("Cheque", self.replacement_cheque)
                if replacement.original_cheque == self.name:
                    if replacement.docstatus == 0:
                        replacement.original_cheque = None
                        replacement.flags.ignore_permissions = True
                        replacement.save()
                    else:
                        replacement.db_set("original_cheque", None, update_modified=False)
            except frappe.DoesNotExistError:
                pass

        if self.original_cheque:
            try:
                original = frappe.get_doc("Cheque", self.original_cheque)
                if original.replacement_cheque == self.name:
                    original.db_set("replacement_cheque", None, update_modified=False)
            except frappe.DoesNotExistError:
                pass

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

        # ---------------------------------------------------------------- #
        #  Status transitions (§3.2.6)                                      #
        # ---------------------------------------------------------------- #
        # apply_workflow never uses db_set: it sets the state field in memory
        # (frappe/model/workflow.py:141) and calls doc.save(), which for an
        # already-submitted doc is classified as "update_after_submit" and
        # dispatches ONLY this hook (frappe/model/document.py:1437-1466).
        # Workflow-driven transitions therefore never reached log_status_change
        # and left no timeline row at all.
        #
        # The other path, change_cheque_status → log_status_change, writes with
        # frappe.db.set_value, which bypasses the ORM entirely and so never
        # reaches this hook. The two paths are disjoint: exactly one event row
        # is written per real transition, whichever fired.
        status_changed = self.has_value_changed("status")
        if status_changed:
            self._append_event(
                STATUS_EVENT_MAP.get(self.status, "Note"),
                notes=(
                    f"Status changed from {before.status} to {self.status} "
                    f"via workflow — by {frappe.session.user}."
                ),
            )
            self._apply_custody_side_effects(self.status)

        self._flush_events()

        if not status_changed:
            return

        # Settlement (§4.5.2). The tracker posts no GL of its own in v1.2 — the
        # linked Payment Entry is the only posting document, and clearing the
        # cheque is what submits it. Every other transition is status-only.
        if self.status == "Cleared":
            self._settle_on_clear()


    def _settle_on_clear(self):
        """The cheque was collected: stamp the date and settle the Payment Entry."""
        if not self.cleared_date:
            self.db_set("cleared_date", today(), update_modified=False)

        outcome = cheque_financial.settle_linked_payment_entry(self)

        notes = {
            "submitted": f"Payment Entry {self.reference_name} submitted on clearance.",
            "already":   f"Payment Entry {self.reference_name} was already submitted; clearance date stamped.",
            "pending":   f"Payment Entry {self.reference_name} could not be submitted by {frappe.session.user}; a ToDo was raised for the approver.",
            "none":      "Cleared with no linked Payment Entry — nothing was posted to the ledger.",
        }.get(outcome)

        if notes:
            self._append_event(
                "Note",
                notes=notes,
                reference_doctype="Payment Entry" if outcome != "none" else None,
                reference_name=self.reference_name if outcome != "none" else None,
            )
            self._flush_events()

        if outcome == "submitted" and self.pe_pending_submission:
            self.db_set("pe_pending_submission", 0, update_modified=False)

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

        # Clear the internal custodian in the SAME write as the status, so the
        # two can never disagree (§3.2.7a). The workflow path does the
        # equivalent through _apply_custody_side_effects.
        released_holder = None
        if new_status in EXTERNAL_CUSTODY_STATUSES and self.current_holder:
            released_holder = self.current_holder
            updates["current_holder"] = None

        frappe.db.set_value("Cheque", self.name, updates)
        self.status = new_status
        if released_holder:
            self.current_holder = None

        self._append_event(
            STATUS_EVENT_MAP.get(new_status, "Note"),
            notes=notes or f"Status changed from {old_status} to {new_status}.",
        )
        if released_holder:
            self._append_event(
                "Note",
                from_holder=released_holder,
                notes=(
                    f"Custody left the company on {new_status}; internal holder "
                    f"{released_holder} cleared."
                ),
            )
        self._flush_events()

    def _apply_custody_side_effects(self, new_status: str):
        """Drop the internal custodian once the cheque has left the company.

        current_holder is a Link to User; an external payee (a courier, the
        electricity company's collector) cannot be represented by it, and
        leaving the last employee there reads as if they still hold the cheque.
        Record the external party in `external_holder` instead (§3.2.7a/b).
        """
        if new_status not in EXTERNAL_CUSTODY_STATUSES or not self.current_holder:
            return

        released_holder = self.current_holder
        self.db_set("current_holder", None, update_modified=False)
        self._append_event(
            "Note",
            from_holder=released_holder,
            notes=(
                f"Custody left the company on {new_status}; internal holder "
                f"{released_holder} cleared."
            ),
        )

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
    #  Replacement (Bounced → Replaced)                                    #
    # ------------------------------------------------------------------ #

    @frappe.whitelist()
    def link_replacement(self, replacement_name):
        """Link an existing Draft Cheque as the replacement for this Bounced
        cheque, then transition this cheque's status to Replaced via the
        workflow."""
        self._validate_can_be_replaced()

        replacement = frappe.get_doc("Cheque", replacement_name)
        cheque_financial.validate_replacement_candidate(self, replacement)

        replacement.original_cheque = self.name
        replacement.flags.ignore_permissions = True
        replacement.save()

        self.replacement_cheque = replacement.name
        self.flags.ignore_validate_update_after_submit = True
        self.save(ignore_permissions=True)

        from frappe.model.workflow import apply_workflow
        apply_workflow(self, "Replace")
        return replacement.name

    @frappe.whitelist()
    def create_replacement(self, cheque_no, issue_date, due_date,
                           drawee_bank=None, cheque_book=None,
                           cheque_leaf=None, amount=None):
        """Create a new Draft Cheque pre-filled from this Bounced cheque,
        link it bidirectionally, and apply the Replace workflow action.
        Returns the new draft's name."""
        self._validate_can_be_replaced()

        replacement = frappe.new_doc("Cheque")
        replacement.cheque_type = self.cheque_type
        replacement.company = self.company
        replacement.party_type = self.party_type
        replacement.party = self.party
        replacement.amount = amount if amount is not None else self.amount
        replacement.currency = self.currency
        replacement.cheque_no = cheque_no
        replacement.issue_date = issue_date
        replacement.received_date = issue_date
        replacement.due_date = due_date
        replacement.drawee_bank = drawee_bank or self.drawee_bank
        replacement.drawer_name = self.drawer_name
        replacement.bank_account = self.bank_account
        replacement.reference_doctype = self.reference_doctype
        replacement.reference_name = self.reference_name
        replacement.original_cheque = self.name

        if self.cheque_type == "Outgoing":
            if not cheque_book or not cheque_leaf:
                frappe.throw(_(
                    "Cheque Book and Leaf are required for Outgoing replacements."
                ))
            replacement.cheque_book = cheque_book
            replacement.cheque_leaf = cheque_leaf

        replacement.flags.ignore_permissions = True
        replacement.insert()

        self.replacement_cheque = replacement.name
        self.flags.ignore_validate_update_after_submit = True
        self.save(ignore_permissions=True)

        from frappe.model.workflow import apply_workflow
        apply_workflow(self, "Replace")
        return replacement.name

    def _validate_can_be_replaced(self):
        if self.docstatus != 1:
            frappe.throw(_("Cheque must be submitted to be replaced."))
        if self.status != "Bounced":
            frappe.throw(_(
                "Only Bounced cheques can be replaced. Current status: {0}"
            ).format(self.status))
        if self.replacement_cheque:
            frappe.throw(_(
                "Cheque {0} has already been replaced by {1}."
            ).format(self.name, self.replacement_cheque))

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
        reference_doctype=None,
        reference_name=None,
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
                "reference_doctype": reference_doctype,
                "reference_name":    reference_name,
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
            self._resync_after_flush(frappe.get_doc("Cheque", self.name))
            return

        for ev in new_events:
            persisted.append("events", ev.as_dict())
        persisted.flags.ignore_permissions = True
        persisted.flags.ignore_validate_update_after_submit = True
        persisted.save()

        self._resync_after_flush(persisted)

    def _resync_after_flush(self, persisted):
        """Realign this in-memory doc with the row _flush_events just wrote.

        Two staleness bugs are closed here:

        1. `persisted.save()` bumps `modified`. check_if_latest compares the DB
           value against `self._original_modified`
           (frappe/model/document.py:1101), so any later save()/cancel() on this
           same object raised TimestampMismatchError — which is precisely what a
           submit()-then-cancel() flow does, and what the Cheque Batch cascade
           will do.
        2. The freshly appended rows only ever got names on `persisted`; the
           local copies stayed nameless, so a second _flush_events() on the same
           object would have written them a second time. Adopting the persisted
           child rows makes the method idempotent.
        """
        self.events = persisted.get("events")
        self.modified = persisted.modified
        self._original_modified = persisted.modified


# ------------------------------------------------------------------ #
#  Whitelisted API                                                     #
# ------------------------------------------------------------------ #

# Workflow-role gating for change_cheque_status. Pairs (from_status,
# to_status) not present here fall through to the per-status validation
# logic in _validate_transition.
_TREASURY = {"Treasury User", "System Manager"}
_ACCOUNTS = {"Accounts User", "System Manager"}

# Role gating for change_cheque_status, mirroring fixtures/workflow.json.
# Treasury moves custody; Accounts clears (the clearing action is what submits
# the Payment Entry, i.e. what posts to the ledger).
_TRANSITION_ROLES = {
    # ---- Incoming ----------------------------------------------------
    ("Draft", "Received"):         _TREASURY,
    ("Received", "In Safe"):       _TREASURY,
    ("Received", "Deposited"):     _TREASURY,
    ("In Safe", "Deposited"):      _TREASURY,
    ("Received", "Endorsed"):      _TREASURY,
    ("In Safe", "Endorsed"):       _TREASURY,
    ("Received", "Cleared"):       _ACCOUNTS,   # cash clearance (§4.2)
    ("In Safe", "Cleared"):        _ACCOUNTS,   # cash clearance (§4.2)
    ("Deposited", "Cleared"):      _ACCOUNTS,
    ("Deposited", "Bounced"):      _TREASURY,
    ("Deposited", "Returned"):     _TREASURY,
    ("Endorsed", "Cleared"):       _ACCOUNTS,
    ("Endorsed", "Bounced"):       _TREASURY,
    ("Bounced", "Deposited"):      _TREASURY,   # re-deposit (§4.4)
    # ---- Outgoing ----------------------------------------------------
    ("Draft", "Issued"):           _TREASURY,
    ("Issued", "Handed Over"):     _TREASURY,
    ("In Safe", "Handed Over"):    _TREASURY,
    ("Handed Over", "Presented"):  _TREASURY,
    ("Handed Over", "Cleared"):    _ACCOUNTS,
    ("Presented", "Cleared"):      _ACCOUNTS,
    ("Handed Over", "Bounced"):    _TREASURY,
    ("Presented", "Bounced"):      _TREASURY,
    ("Handed Over", "Returned"):   _TREASURY,
    ("Presented", "Returned"):     _TREASURY,
    # ---- Shared ------------------------------------------------------
    ("Received", "Returned"):      _TREASURY,
    ("Received", "Cancelled"):     _TREASURY,
    ("In Safe", "Returned"):       _TREASURY,
    ("In Safe", "Cancelled"):      _TREASURY,
    ("Issued", "Returned"):        _TREASURY,
    ("Issued", "Cancelled"):       _TREASURY,
    ("Bounced", "Replaced"):       _TREASURY,
}


def _validate_status_requirements(doc, new_status: str):
    """Preconditions for landing in `new_status`, shared by both paths.

    Called from `before_update_after_submit` (workflow saves) and from
    `_validate_transition` (the whitelisted UI endpoint), so a rule cannot be
    satisfied on one route and skipped on the other.
    """
    if new_status in INCOMING_STATUSES and new_status not in OUTGOING_STATUSES:
        if doc.cheque_type != "Incoming":
            frappe.throw(
                _("{0} is an Incoming-only status.").format(new_status),
                frappe.ValidationError,
            )
    if new_status in OUTGOING_STATUSES and new_status not in INCOMING_STATUSES:
        if doc.cheque_type != "Outgoing":
            frappe.throw(
                _("{0} is an Outgoing-only status.").format(new_status),
                frappe.ValidationError,
            )

    if new_status in ("In Safe", "Deposited", "Handed Over", "Endorsed", "Presented"):
        if not doc.company or not doc.party or not doc.amount:
            frappe.throw(
                _("Company, Party, and Amount are required before moving to {0}.").format(
                    new_status
                ),
                frappe.ValidationError,
            )

    if new_status == "Deposited":
        if doc.clearance_type == "Cash":
            frappe.throw(
                _(
                    "Cannot deposit a cheque whose Clearance Type is Cash. "
                    "Cash cheques clear directly via the Cash Clear action."
                ),
                frappe.ValidationError,
            )
        if not doc.bank_account:
            frappe.throw(
                _("Bank Account is required before marking as Deposited."),
                frappe.ValidationError,
            )

    if new_status == "Cleared":
        if doc.clearance_type == "Cash":
            if not doc.cash_account:
                frappe.throw(
                    _("Cash Account is required to clear a cash cheque."),
                    frappe.ValidationError,
                )
        elif not doc.bank_account:
            frappe.throw(
                _("Bank Account is required before marking as Cleared."),
                frappe.ValidationError,
            )

    if new_status == "Endorsed":
        cheque_financial.validate_endorsement(_with_status(doc, "Endorsed"))

    if new_status == "Bounced":
        cheque_financial.validate_bounce(_with_status(doc, "Bounced"))


def _with_status(doc, status):
    """A view of `doc` as if it were already in `status`.

    The validators read `doc.status` so they work equally on a saved document;
    on the UI path the status has not been written yet.
    """
    if doc.status == status:
        return doc
    shadow = frappe._dict(doc.as_dict())
    shadow.status = status
    return shadow


@frappe.whitelist()
def change_cheque_status(cheque_name: str, new_status: str, notes: str = ""):
    """Workflow / UI transition endpoint (non-financial status changes)."""
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=doc, throw=True)
    _validate_transition(doc, new_status, notes)
    doc.log_status_change(new_status, notes=notes)

    # Clearing is what settles the Payment Entry. This endpoint writes with
    # frappe.db.set_value, which never reaches on_update_after_submit, so the
    # settlement has to be invoked explicitly — otherwise a cheque could be
    # marked Cleared through the UI with its PE left sitting in draft.
    if new_status == "Cleared":
        doc.reload()
        doc._settle_on_clear()

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

    _validate_status_requirements(doc, new_status)


@frappe.whitelist()
def hand_over_cheque(
    cheque_name: str, to_user: str, location: str = "", notes: str = ""
):
    """Transfer physical custody."""
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=doc, throw=True)
    doc.hand_over(to_user=to_user, location=location, notes=notes)
    return {"status": "ok"}
