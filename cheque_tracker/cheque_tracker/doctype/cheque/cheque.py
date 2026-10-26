# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import (
    add_to_date,
    convert_utc_to_system_timezone,
    format_datetime,
    get_datetime,
    getdate,
    now_datetime,
    today,
)

from cheque_tracker.cheque_tracker.doctype.cheque import cheque_financial
from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import (
    cancel_leaf,
    cheque_numbers_match,
    find_leaf_by_number,
    free_leaf,
    get_leaf,
    mark_leaf_issued,
    release_leaf,
    reserve_leaf,
    transfer_leaf,
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

# Who may move a submitted cheque onto a different leaf. The cheque number is
# part of what was paid; changing it after the fact is a correction, not an
# operation.
LEAF_REASSIGN_ROLE = "System Manager"

# How far ahead of the server clock an Effective Date may sit before it counts
# as "in the future". Absorbs browser/server clock skew when someone picks now.
EFFECTIVE_DATE_SKEW_MINUTES = 5


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

    def validate(self):
        # Leaf assignment runs here rather than in before_save: `validate` runs
        # for a save AND a submit (frappe/model/document.py
        # run_before_save_methods), while before_save only runs for a save — so
        # a draft whose number was edited and then submitted in one call
        # (doc.submit(), frappe.client.submit) never had it re-resolved.
        if self.cheque_type == "Outgoing":
            self._assign_outgoing_leaf()
        else:
            self._drop_leaf_from_incoming()
        self.effective_date = _parse_effective_date(self.get("effective_date"))

    def before_save(self):
        self._validate_outgoing_cheque_no()
        cheque_financial.validate_duplicate_cheque(self)
        cheque_financial.validate_payment_entry_link(self)
        cheque_financial.validate_clearance_override(self)

    def before_update_after_submit(self):
        """Gate submitted-state transitions BEFORE the row is written.

        A workflow action on a submitted document routes through doc.save(), so
        `validate()` never runs (frappe/model/document.py:1402-1411) — business
        rules for these transitions have to live here or they simply do not fire.
        """
        before = self.get_doc_before_save()
        if before and before.status != self.status:
            _validate_status_requirements(self, self.status, old_status=before.status)
        self.effective_date = _parse_effective_date(self.get("effective_date"))
        cheque_financial.validate_payment_entry_link(self)
        cheque_financial.validate_clearance_override(self)
        cheque_financial.validate_reference_edit_after_submit(self)

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
        effective = self._take_effective_date()
        self._append_event(
            STATUS_EVENT_MAP[self.status],
            notes="Cheque submitted." + _backdating_note(effective),
            event_datetime=effective,
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

        effective = self._take_effective_date()
        self._append_event(
            "Cancelled",
            notes="Cheque cancelled." + _backdating_note(effective),
            event_datetime=effective,
        )
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
        # The Effective Date belongs to the transition it was saved ahead of,
        # so it is consumed only when the status actually moves; saving it on
        # its own just parks it for the next action.
        effective = self._take_effective_date() if status_changed else None
        if status_changed:
            self._append_event(
                STATUS_EVENT_MAP.get(self.status, "Note"),
                notes=(
                    f"Status changed from {before.status} to {self.status} "
                    f"via workflow — by {frappe.session.user}."
                    + _backdating_note(effective)
                ),
                event_datetime=effective,
            )
            self._apply_custody_side_effects(self.status, effective)
            self._apply_unclear_side_effects(before.status, self.status, effective)
            self._apply_leaf_side_effects(self.status, effective)

        self._flush_events()

        if not status_changed:
            return

        # Settlement (§4.5.2). The tracker posts no GL of its own in v1.2 — the
        # linked Payment Entry is the only posting document, and clearing the
        # cheque is what submits it. Every other transition is status-only.
        if self.status == "Cleared":
            self._settle_on_clear(effective)


    def _settle_on_clear(self, effective=None):
        """The cheque was collected: stamp the date and settle the Payment Entry."""
        if not self.cleared_date:
            self.db_set(
                "cleared_date", getdate(effective) if effective else today(),
                update_modified=False,
            )

        outcome = cheque_financial.settle_linked_payment_entry(self)

        notes = {
            "submitted": f"Payment Entry {self.reference_name} submitted on clearance.",
            "already":   f"Payment Entry {self.reference_name} was already submitted; clearance date stamped.",
            "pending":   f"Payment Entry {self.reference_name} could not be submitted by {frappe.session.user}; a ToDo was raised for the approver.",
            # "none" is only reachable through the System Manager override — the
            # transition is refused otherwise — so name who took responsibility.
            "none": (
                "Cleared with NO accounting document. Override authorised by "
                f"{frappe.session.user}. Reason: "
                f"{(self.clearance_override_reason or '').strip() or '(none given)'}. "
                "Nothing was posted to the ledger; record the collection separately."
            ),
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

    def _assign_outgoing_leaf(self):
        """Hold the leaf this cheque was actually written on.

        The clerk names it by Cheque Leaf, by Cheque No, or both. That leaf is
        used when it is Unused and belongs to the selected Cheque Book; one that
        is already Reserved / Issued / Voided / Cancelled is refused with the
        reason. Only when BOTH are blank is the next Unused leaf picked.

        This used to pick the next Unused leaf unconditionally and overwrite
        cheque_no with its number — so CHQ-2026-00004, written on 528201382,
        was recorded as 528201380 without a word.
        """
        if not self.cheque_book:
            frappe.throw(
                _("Cheque Book is required for Outgoing cheques."),
                frappe.ValidationError,
            )
        self._validate_cheque_book_matches()

        typed_no = (self.cheque_no or "").strip()
        leaf = self._requested_leaf(typed_no)
        held = self._held_leaves()

        if leaf is None:
            for name in held:
                free_leaf(name, self.name)
            result = reserve_leaf(self.cheque_book, self.name, frappe.session.user)
            self.cheque_leaf = result["name"]
            self.cheque_no = result["cheque_no"]
            frappe.msgprint(
                _("Cheque Leaf {0} — the next Unused leaf of {1} — was assigned.").format(
                    result["cheque_no"], self.cheque_book
                ),
                alert=True,
                indicator="blue",
            )
            return

        if leaf.cheque_book != self.cheque_book:
            frappe.throw(
                _(
                    "Cheque Leaf {0} belongs to Cheque Book {1}, not {2}. Pick a leaf "
                    "of {2}, or clear Cheque Leaf and Cheque No to take its next Unused one."
                ).format(leaf.cheque_no, leaf.cheque_book, self.cheque_book),
                frappe.ValidationError,
                title=_("Cheque Leaf not in this book"),
            )

        if not (leaf.cheque == self.name and leaf.leaf_status == "Reserved"):
            # Throws, with the reason, unless the leaf is Unused.
            reserve_leaf(self.cheque_book, self.name, frappe.session.user, leaf_name=leaf.name)

        for name in held:
            if name != leaf.name:
                free_leaf(name, self.name)

        self.cheque_leaf = leaf.name
        if typed_no and typed_no != leaf.cheque_no:
            frappe.msgprint(
                _("Cheque No changed from {0} to {1} to match Cheque Leaf {2}.").format(
                    typed_no, leaf.cheque_no, leaf.name
                ),
                alert=True,
                indicator="orange",
            )
        # Always the leaf's own spelling — a stray space from an import would
        # otherwise trip _validate_outgoing_cheque_no's exact comparison.
        self.cheque_no = leaf.cheque_no

    def _validate_cheque_book_matches(self):
        """The book has to be one this cheque can be drawn on.

        The leaf picker can fill in the book from whichever leaf was picked, so
        the company and bank account are checked here rather than trusted to
        the Cheque Book picker's filter.
        """
        book = frappe.db.get_value(
            "Cheque Book", self.cheque_book, ["company", "bank_account"], as_dict=True
        )
        if not book:
            return
        if self.company and book.company != self.company:
            frappe.throw(
                _("Cheque Book {0} belongs to {1}, not {2}.").format(
                    self.cheque_book, book.company, self.company
                ),
                frappe.ValidationError,
            )
        if self.bank_account and book.bank_account and book.bank_account != self.bank_account:
            frappe.throw(
                _("Cheque Book {0} is for bank account {1}, but this cheque is drawn on {2}.").format(
                    self.cheque_book, book.bank_account, self.bank_account
                ),
                frappe.ValidationError,
            )

    def _requested_leaf(self, typed_no):
        """The leaf the user asked for, or None when they asked for none.

        When both fields are set and disagree, an existing draft trusts the
        field that was just edited — retyping the number moves to that number's
        leaf; picking a leaf carries its number. On a new cheque, or when both
        were edited at once, there is no way to tell which one is right, so it
        is refused rather than guessed.
        """
        if not self.cheque_leaf:
            return self._leaf_for_number(typed_no) if typed_no else None

        leaf = get_leaf(self.cheque_leaf)
        if not leaf:
            frappe.throw(
                _("Cheque Leaf {0} does not exist.").format(self.cheque_leaf),
                frappe.ValidationError,
            )
        if not typed_no or cheque_numbers_match(typed_no, leaf.cheque_no):
            return leaf

        before = self.get_doc_before_save()
        if before:
            leaf_edited = (before.cheque_leaf or None) != (self.cheque_leaf or None)
            no_edited = (before.cheque_no or "").strip() != typed_no
            if no_edited and not leaf_edited:
                return self._leaf_for_number(typed_no)
            if leaf_edited and not no_edited:
                return leaf

        frappe.throw(
            _(
                "Cheque No {0} does not match Cheque Leaf {1} (number {2}). Set one "
                "of them, or make them agree."
            ).format(typed_no, leaf.name, leaf.cheque_no),
            frappe.ValidationError,
            title=_("Cheque No and Cheque Leaf disagree"),
        )

    def _leaf_for_number(self, cheque_no):
        leaf = find_leaf_by_number(self.cheque_book, cheque_no)
        if leaf:
            return leaf

        message = [_("Cheque No {0} is not a leaf of Cheque Book {1}.").format(cheque_no, self.cheque_book)]
        elsewhere = frappe.db.get_value(
            "Cheque Leaf", {"cheque_no": cheque_no, "company": self.company}, "cheque_book"
        )
        if elsewhere and elsewhere != self.cheque_book:
            message.append(_("It is a leaf of Cheque Book {0}.").format(elsewhere))
        message.append(_("Leave Cheque No and Cheque Leaf blank to take the next Unused leaf."))
        frappe.throw(
            " ".join(message),
            frappe.ValidationError,
            title=_("Unknown cheque number"),
        )

    def _held_leaves(self):
        """Leaves currently Reserved for this cheque, whatever the form says."""
        if not self.name or not self.get_doc_before_save():
            return []
        return frappe.get_all(
            "Cheque Leaf",
            filters={"cheque": self.name, "leaf_status": "Reserved"},
            pluck="name",
        )

    def _drop_leaf_from_incoming(self):
        """An incoming cheque is not drawn on our books — let go of any leaf.

        Frees what the cheque actually holds, not just what the form says: the
        desk blanks Cheque Leaf the moment the type changes to Incoming, so by
        save time the field no longer names the reserved leaf.
        """
        # Not gated on the previous type: a draft switched under 1.3.0 is
        # already saved as Incoming while its leaf is still Reserved for it.
        names = set(self._held_leaves())
        if self.cheque_leaf:
            names.add(self.cheque_leaf)
        for name in names:
            free_leaf(name, self.name)
        self.cheque_leaf = None
        self.cheque_book = None

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
        # The date written on the cheque, not the day it was keyed in.
        mark_leaf_issued(self.cheque_leaf, issued_on=self.issue_date or today())

    # ------------------------------------------------------------------ #
    #  Status management (called by workflow / API)                        #
    # ------------------------------------------------------------------ #

    def log_status_change(self, new_status: str, notes: str = "", effective_date=None):
        old_status = self.status
        # An explicit effective_date (the API argument) wins over one parked on
        # the document; either way it is consumed by this transition.
        effective = _parse_effective_date(effective_date or self.get("effective_date"))

        updates = {"status": new_status}
        if self.get("effective_date"):
            updates["effective_date"] = None
        if new_status == "Cleared" and not self.cleared_date:
            # Only default it. A caller that already knows the real clearance
            # date — the batch cascade passes the bank's date — must not have it
            # silently overwritten with today's.
            updates["cleared_date"] = getdate(effective) if effective else today()

        # Clear the internal custodian in the SAME write as the status, so the
        # two can never disagree (§3.2.7a). The workflow path does the
        # equivalent through _apply_custody_side_effects.
        released_holder = None
        if new_status in EXTERNAL_CUSTODY_STATUSES and self.current_holder:
            released_holder = self.current_holder
            updates["current_holder"] = None

        frappe.db.set_value("Cheque", self.name, updates)
        self.status = new_status
        self.effective_date = None
        if released_holder:
            self.current_holder = None
        if "cleared_date" in updates:
            self.cleared_date = updates["cleared_date"]

        self._apply_unclear_side_effects(old_status, new_status, effective)

        self._append_event(
            STATUS_EVENT_MAP.get(new_status, "Note"),
            notes=(notes or f"Status changed from {old_status} to {new_status}.")
            + _backdating_note(effective),
            event_datetime=effective,
        )
        if released_holder:
            self._append_event(
                "Note",
                from_holder=released_holder,
                notes=(
                    f"Custody left the company on {new_status}; internal holder "
                    f"{released_holder} cleared."
                ),
                event_datetime=effective,
            )
        self._apply_leaf_side_effects(new_status, effective)
        self._flush_events()

    def _take_effective_date(self):
        """Read the parked Effective Date for the transition being recorded,
        then clear it so the next transition defaults to now again."""
        value = self.get("effective_date")
        if not value:
            return None
        frappe.db.set_value("Cheque", self.name, "effective_date", None, update_modified=False)
        self.effective_date = None
        return get_datetime(value)

    def _apply_leaf_side_effects(self, new_status: str, effective=None):
        """A returned outgoing cheque never gets paid: its leaf is dead paper.

        The leaf moves to Cancelled but stays linked to the cheque — the cheque
        is still the record of what was written on it.
        """
        if new_status != "Returned" or self.cheque_type != "Outgoing" or not self.cheque_leaf:
            return
        leaf_status = frappe.db.get_value("Cheque Leaf", self.cheque_leaf, "leaf_status")
        if leaf_status not in ("Reserved", "Issued"):
            return

        on = frappe.utils.format_date(getdate(effective) if effective else today())
        cancel_leaf(
            self.cheque_leaf,
            void_reason=f"Cheque {self.name} returned to the company on {on}.",
        )
        self._append_event(
            "Note",
            notes=(
                f"Cheque Leaf {self.cheque_no} ({self.cheque_leaf}) cancelled — the "
                "cheque was returned, so it will never be paid."
            ),
            event_datetime=effective,
        )

    def _apply_unclear_side_effects(self, old_status: str, new_status: str, effective=None):
        """Reverse the clearance stamp and say who did it, and why.

        Deliberately does NOT touch the Payment Entry. Clearing may have
        submitted one, and cancelling a submitted PE reverses GL entries and can
        break its own downstream links — that is an accounting decision with
        consequences the tracker cannot see, so it belongs to a human. We say so
        loudly instead of guessing.
        """
        if old_status != "Cleared" or new_status == "Cleared":
            return

        reason = (self.get("unclear_reason") or "").strip() or "(none given)"
        self.db_set("cleared_date", None, update_modified=False)

        self._append_event(
            "Note",
            notes=(
                f"Clearance reversed to {new_status} by {frappe.session.user}. "
                f"Reason: {reason}. Cleared Date cleared."
            ),
            event_datetime=effective,
        )

        posted = cheque_financial.linked_accounting_document(self)
        if posted and posted[0] == "Payment Entry":
            docstatus = frappe.db.get_value("Payment Entry", posted[1], "docstatus")
            if docstatus == 1:
                message = _(
                    "Payment Entry {0} is still submitted and has NOT been cancelled — "
                    "reversing a cheque clearance does not unwind the ledger. Cancel or "
                    "amend it yourself if this collection did not happen."
                ).format(posted[1])
                self._append_event(
                    "Note",
                    notes=(
                        f"Payment Entry {posted[1]} left submitted on un-clear; "
                        "it must be cancelled or amended manually."
                    ),
                    reference_doctype="Payment Entry",
                    reference_name=posted[1],
                )
                frappe.msgprint(
                    message, title=_("Payment Entry still posted"), indicator="orange"
                )

    def _apply_custody_side_effects(self, new_status: str, effective=None):
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
            event_datetime=effective,
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
    def create_replacement(self, cheque_no=None, issue_date=None, due_date=None,
                           drawee_bank=None, cheque_book=None,
                           cheque_leaf=None, amount=None):
        """Create a new Draft Cheque pre-filled from this Bounced cheque,
        link it bidirectionally, and apply the Replace workflow action.
        Returns the new draft's name.

        An outgoing replacement may leave cheque_no blank: its number is the
        chosen leaf's. The dialog omits a blank field from the call entirely,
        which is why every argument has a default."""
        self._validate_can_be_replaced()
        if not issue_date or not due_date:
            frappe.throw(_("Issue Date and Due Date are required."), frappe.ValidationError)
        cheque_no = (cheque_no or "").strip() or None
        if self.cheque_type != "Outgoing" and not cheque_no:
            frappe.throw(_("Cheque No is required for the replacement."), frappe.ValidationError)

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

    # ------------------------------------------------------------------ #
    #  Leaf reassignment (System Manager correction)                       #
    # ------------------------------------------------------------------ #

    def reassign_leaf(self, reason: str, new_leaf: str = None, cheque_no: str = None):
        """Move a submitted outgoing cheque onto another Unused leaf of its book.

        For the cheque that was recorded against the wrong leaf: the new leaf
        takes over (Issued — or Cancelled if the cheque was already returned),
        the old one goes back to Unused and unlinked, cheque_no follows, and the
        reason is logged with who did it. Name the target by leaf or by number.

        The linked Payment Entry is NOT touched; if it carries the old number as
        its reference, the user is told to correct it.
        """
        reason = (reason or "").strip()
        if LEAF_REASSIGN_ROLE not in frappe.get_roles(frappe.session.user):
            frappe.throw(
                _("Only a {0} can reassign the leaf of a submitted cheque.").format(
                    LEAF_REASSIGN_ROLE
                ),
                frappe.PermissionError,
            )
        if self.docstatus != 1:
            frappe.throw(
                _("Only a submitted cheque's leaf can be reassigned; a draft takes the leaf you pick."),
                frappe.ValidationError,
            )
        if self.cheque_type != "Outgoing" or not self.cheque_book or not self.cheque_leaf:
            frappe.throw(
                _("Only an outgoing cheque drawn on a Cheque Book has a leaf to reassign."),
                frappe.ValidationError,
            )
        if not reason:
            frappe.throw(
                _("Give a reason for reassigning the leaf of {0}.").format(self.name),
                frappe.ValidationError,
                title=_("Reason required"),
            )
        if not (new_leaf or (cheque_no or "").strip()):
            frappe.throw(_("Choose the leaf to move this cheque to."), frappe.ValidationError)

        # Lock the cheque before any leaf (the order a save takes them in), and
        # re-check everything this method decides on against the locked row: a
        # locking read sees the latest commit, so a concurrent reassignment,
        # Cancel or Return that landed since this copy was loaded is caught
        # instead of acted on with a stale leaf, docstatus or status.
        current = frappe.db.get_value(
            "Cheque", self.name, ["cheque_leaf", "docstatus", "status"], as_dict=True, for_update=True
        )
        if (current.cheque_leaf, current.docstatus, current.status) != (
            self.cheque_leaf,
            self.docstatus,
            self.status,
        ):
            frappe.throw(
                _("The leaf of {0} was changed by someone else just now. Reload and try again.").format(
                    self.name
                ),
                frappe.ValidationError,
            )

        if new_leaf:
            target = get_leaf(new_leaf)
            if not target:
                frappe.throw(_("Cheque Leaf {0} does not exist.").format(new_leaf), frappe.ValidationError)
            if cheque_no and not cheque_numbers_match(cheque_no, target.cheque_no):
                frappe.throw(
                    _("Cheque No {0} does not match Cheque Leaf {1} (number {2}).").format(
                        cheque_no, target.name, target.cheque_no
                    ),
                    frappe.ValidationError,
                )
        else:
            target = find_leaf_by_number(self.cheque_book, cheque_no)
            if not target:
                frappe.throw(
                    _("Cheque No {0} is not a leaf of Cheque Book {1}.").format(
                        cheque_no, self.cheque_book
                    ),
                    frappe.ValidationError,
                )

        if target.name == self.cheque_leaf:
            frappe.throw(
                _("{0} is already on Cheque Leaf {1}.").format(self.name, target.cheque_no),
                frappe.ValidationError,
            )
        if target.cheque_book != self.cheque_book:
            frappe.throw(
                _("Cheque Leaf {0} belongs to Cheque Book {1}, not {2}. A leaf can only be "
                  "reassigned within the same book.").format(
                    target.cheque_no, target.cheque_book, self.cheque_book
                ),
                frappe.ValidationError,
            )

        # The same one-physical-cheque guard a save runs (§4.5.4), against the
        # number the cheque is about to carry.
        cheque_financial.validate_duplicate_cheque(
            frappe._dict(
                name=self.name,
                cheque_no=target.cheque_no,
                cheque_type=self.cheque_type,
                drawee_bank=self.drawee_bank,
            )
        )

        old_leaf, old_no = self.cheque_leaf, self.cheque_no
        old = get_leaf(old_leaf) or frappe._dict()
        returned = self.status == "Returned"
        moved = transfer_leaf(
            old_leaf,
            target.name,
            self.name,
            frappe.session.user,
            leaf_status="Cancelled" if returned else "Issued",
            issued_on=self.issue_date or old.issued_on or today(),
            void_reason=(old.void_reason or f"Cheque {self.name} returned to the company.")
            if returned
            else None,
        )

        frappe.db.set_value(
            "Cheque", self.name, {"cheque_leaf": moved["name"], "cheque_no": moved["cheque_no"]}
        )
        self.cheque_leaf, self.cheque_no = moved["name"], moved["cheque_no"]

        self._append_event(
            "Note",
            notes=(
                f"Cheque Leaf reassigned from {old_no} ({old_leaf}) to "
                f"{moved['cheque_no']} ({moved['name']}) by {frappe.session.user}. "
                f"Reason: {reason}. "
                + (
                    f"Leaf {old_no} returned to Unused."
                    if moved["old_freed"]
                    else f"Leaf {old_no} was no longer linked to this cheque and was left as it was."
                )
            ),
        )
        self._flush_events()

        if (
            self.reference_doctype == "Payment Entry"
            and self.reference_name
            and cheque_numbers_match(
                frappe.db.get_value("Payment Entry", self.reference_name, "reference_no") or "",
                old_no,
            )
        ):
            frappe.msgprint(
                _(
                    "Payment Entry {0} still carries cheque number {1} as its reference. "
                    "It was not changed — correct it there if it should read {2}."
                ).format(self.reference_name, old_no, moved["cheque_no"]),
                title=_("Payment Entry not updated"),
                indicator="orange",
            )

        return moved

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
        event_datetime=None,
    ):
        if not isinstance(getattr(self, "events", None), list):
            self.events = []
        self.append(
            "events",
            {
                "event_type":     event_type,
                # When it happened — an Effective Date for a back-recorded
                # transition, otherwise now.
                "event_datetime": event_datetime or now_datetime(),
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
    # ---- Reversal (§v1.3.1) — System Manager only -----------------------
    ("Cleared", "Deposited"):      {"System Manager"},
    ("Cleared", "Handed Over"):    {"System Manager"},
}

# Reversing a clearance is not an ordinary transition: the money was recorded as
# collected and, under the v1.2 model, a Payment Entry may already have posted.
UNCLEAR_TARGETS = {"Incoming": "Deposited", "Outgoing": "Handed Over"}


def _validate_status_requirements(doc, new_status: str, old_status: str = None):
    """Preconditions for landing in `new_status`, shared by both paths.

    Called from `before_update_after_submit` (workflow saves) and from
    `_validate_transition` (the whitelisted UI endpoint), so a rule cannot be
    satisfied on one route and skipped on the other.

    `old_status` must be passed explicitly by the workflow path: apply_workflow
    sets the state field in memory before saving, so by the time this runs
    `doc.status` is already the NEW status and reading it would silently skip
    every rule about what a cheque is moving *away* from.
    """
    if old_status is None:
        old_status = doc.status
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
        # A clearance that posts nothing is refused outright (amended after
        # review): v1.2 moved all posting to the linked Payment Entry, so a
        # cheque with nothing linked would be recorded as collected while the
        # books never hear about it. A System Manager can still override.
        cheque_financial.validate_clearance_has_accounting_document(
            _with_status(doc, "Cleared")
        )
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

    if old_status == "Cleared" and new_status != "Cleared":
        expected = UNCLEAR_TARGETS.get(doc.cheque_type)
        if new_status != expected:
            frappe.throw(
                _("A cleared {0} cheque can only be reversed to {1}.").format(
                    doc.cheque_type, expected
                ),
                frappe.ValidationError,
            )
        if not (doc.get("unclear_reason") or "").strip():
            frappe.throw(
                _("Give a reason for reversing the clearance of {0}.").format(doc.name),
                frappe.ValidationError,
                title=_("Un-clear reason required"),
            )

    if new_status == "Endorsed":
        cheque_financial.validate_endorsement(_with_status(doc, "Endorsed"))

    if new_status == "Bounced":
        cheque_financial.validate_bounce(_with_status(doc, "Bounced"))


def _parse_effective_date(value):
    """The Effective Date as a naive datetime in the system time zone, or None.

    API callers send whatever their client produces — an ISO string with a `Z`
    or an offset compares as aware against the naive server clock and raised a
    TypeError — so it is normalised once here, and anything unparseable is a
    validation error rather than a 500 (or, worse, silently "now").
    """
    if not value:
        return None
    try:
        parsed = get_datetime(value)
    except Exception:
        parsed = None
    if not parsed:
        frappe.throw(
            _("Effective Date {0} is not a valid date and time.").format(value),
            frappe.ValidationError,
        )
    if parsed.tzinfo:
        parsed = convert_utc_to_system_timezone(parsed).replace(tzinfo=None)
    _validate_effective_date(parsed)
    return parsed


def _validate_effective_date(value):
    """An Effective Date says when a transition really happened, so it cannot
    be later than now (plus a few minutes of clock skew)."""
    if not value:
        return
    limit = add_to_date(now_datetime(), minutes=EFFECTIVE_DATE_SKEW_MINUTES)
    if get_datetime(value) > limit:
        frappe.throw(
            _("Effective Date {0} is in the future. It records when the action really happened.").format(
                format_datetime(value)
            ),
            frappe.ValidationError,
            title=_("Effective Date in the future"),
        )


def _backdating_note(effective):
    """Timeline suffix for a back-recorded transition: the event row carries
    the effective time, so say when and by whom it was actually keyed in."""
    if not effective:
        return ""
    return (
        f" Effective {format_datetime(effective)}; recorded {format_datetime(now_datetime())} "
        f"by {frappe.session.user}."
    )


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
def change_cheque_status(
    cheque_name: str, new_status: str, notes: str = "", effective_date: str | None = None
):
    """Workflow / UI transition endpoint (non-financial status changes).

    `effective_date` back-dates the transition's event (default: now, or the
    Effective Date saved on the cheque).
    """
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=doc, throw=True)
    _validate_transition(doc, new_status, notes)
    doc.log_status_change(new_status, notes=notes, effective_date=effective_date)

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
def reassign_cheque_leaf(
    cheque_name: str, reason: str, new_leaf: str | None = None, cheque_no: str | None = None
):
    """System Manager: move a submitted outgoing cheque to another Unused leaf
    of the same book. See Cheque.reassign_leaf."""
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "read", doc=doc, throw=True)
    moved = doc.reassign_leaf(reason=reason, new_leaf=new_leaf, cheque_no=cheque_no)
    return {"status": "ok", "cheque_leaf": moved["name"], "cheque_no": moved["cheque_no"]}


@frappe.whitelist()
def set_effective_date(cheque_name: str, effective_date: str | None = None):
    """Park (or clear) the Effective Date for a cheque's next action.

    The desk makes a submitted cheque read-only to anyone outside the workflow
    state's allow_edit role, so the Accounts User who clears a Deposited cheque
    could not type the date into the form. This writes that one field and
    nothing else; the next transition consumes it.
    """
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=doc, throw=True)
    if doc.docstatus == 2:
        frappe.throw(_("{0} is cancelled.").format(doc.name), frappe.ValidationError)
    value = _parse_effective_date(effective_date)
    previous = doc.effective_date

    # Bump `modified` and tell open forms: a stale form saved afterwards would
    # otherwise write its blank Effective Date straight over this one.
    doc.db_set("effective_date", value, notify=True)

    # Whoever runs the action is named on its event; who chose the date is
    # recorded here.
    doc._append_event(
        "Note",
        notes=(
            f"Effective Date for the next action set to {format_datetime(value)}"
            if value
            else "Effective Date for the next action cleared"
        )
        + (f" (was {format_datetime(previous)})" if previous else "")
        + f" — by {frappe.session.user}.",
    )
    doc._flush_events()
    return {"effective_date": value}


@frappe.whitelist()
def hand_over_cheque(
    cheque_name: str, to_user: str, location: str = "", notes: str = ""
):
    """Transfer physical custody."""
    doc = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=doc, throw=True)
    doc.hand_over(to_user=to_user, location=location, notes=notes)
    return {"status": "ok"}
