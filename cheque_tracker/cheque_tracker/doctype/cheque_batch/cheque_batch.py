# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""Cheque Batch — bulk deposit, clearance and bounce of incoming cheques.

§5.2. Every batch action cascades to its member cheques through
``change_cheque_status`` — the same endpoint the desk uses for a single cheque.
That matters for three reasons: each member gets its own Cheque Event, the role
gating and per-status preconditions are identical to the single-cheque path, and
clearing a member settles its Payment Entry exactly as it would one at a time.
Doing the transition here by hand would have quietly bypassed all three.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

from cheque_tracker.cheque_tracker.doctype.cheque.cheque import change_cheque_status

# A cheque may join a batch only from these states.
DEPOSITABLE_STATUSES = ("Received", "In Safe")

# Batch states that still hold a claim on their member cheques. A cheque already
# sitting in one of these cannot be added to a second batch.
OPEN_BATCH_STATUSES = ("Draft", "Deposited")

# Status → the noun used in user-facing messages. "Partial Batch Deposit" reads
# like something that happened; "Partial Batch Deposited" does not.
_ACTION_NOUN = {"Deposited": "Deposit", "Cleared": "Clearance", "Bounced": "Bounce"}


class ChequeBatch(Document):
    def validate(self):
        self._check_duplicates()
        if self.docstatus == 0:
            self._validate_members()
        self._sync_item_snapshots()
        self._compute_totals()

    def on_submit(self):
        self.db_set("status", "Deposited", update_modified=False)
        self._cascade(
            "Deposited",
            f"Batch deposited via {self.name}.",
            before_each=self._stamp_bank_account,
        )

    def _stamp_bank_account(self, row):
        """Depositing *is* naming the account the cheque goes into.

        A member with no bank account of its own inherits the batch's — otherwise
        the per-cheque Deposit precondition would reject it, which would be a
        confusing way to say "this batch has no bank account".
        """
        if not self.bank_account:
            return
        if frappe.db.get_value("Cheque", row.cheque, "bank_account"):
            return
        frappe.db.set_value(
            "Cheque", row.cheque, "bank_account", self.bank_account, update_modified=False
        )

    def on_cancel(self):
        self.db_set("status", "Cancelled", update_modified=False)

    # ------------------------------------------------------------------ #
    #  Validation                                                          #
    # ------------------------------------------------------------------ #

    def _check_duplicates(self):
        seen = set()
        for row in self.items:
            if row.cheque in seen:
                frappe.throw(
                    _("Cheque {0} is listed more than once.").format(row.cheque),
                    frappe.ValidationError,
                )
            seen.add(row.cheque)

    def _validate_members(self):
        """§5.2 — a member must be an incoming cheque, in a depositable state,
        and not already claimed by another open batch."""
        for row in self.items:
            cheque = frappe.db.get_value(
                "Cheque",
                row.cheque,
                ["cheque_type", "status", "docstatus", "company", "clearance_type"],
                as_dict=True,
            )
            if not cheque:
                frappe.throw(
                    _("Cheque {0} does not exist.").format(row.cheque), frappe.ValidationError
                )

            if cheque.cheque_type != "Incoming":
                frappe.throw(
                    _("Cheque {0} is Outgoing — only incoming cheques can be deposited in a batch.").format(
                        row.cheque
                    ),
                    frappe.ValidationError,
                )

            if cheque.docstatus != 1:
                frappe.throw(
                    _("Cheque {0} is not submitted.").format(row.cheque), frappe.ValidationError
                )

            if cheque.clearance_type == "Cash":
                frappe.throw(
                    _(
                        "Cheque {0} is set to clear in cash, so it cannot be deposited in a batch."
                    ).format(row.cheque),
                    frappe.ValidationError,
                )

            if cheque.status not in DEPOSITABLE_STATUSES:
                frappe.throw(
                    _("Cheque {0} is {1}; only {2} cheques can be batched.").format(
                        row.cheque, cheque.status, " or ".join(DEPOSITABLE_STATUSES)
                    ),
                    frappe.ValidationError,
                )

            if self.company and cheque.company != self.company:
                frappe.throw(
                    _("Cheque {0} belongs to {1}, not {2}.").format(
                        row.cheque, cheque.company, self.company
                    ),
                    frappe.ValidationError,
                )

            other = self._other_open_batch(row.cheque)
            if other:
                frappe.throw(
                    _("Cheque {0} is already in batch {1}.").format(row.cheque, other),
                    frappe.ValidationError,
                )

    def _other_open_batch(self, cheque):
        rows = frappe.db.sql(
            """
            SELECT b.name
            FROM   `tabCheque Batch Item` i
            JOIN   `tabCheque Batch` b ON b.name = i.parent
            WHERE  i.cheque = %(cheque)s
              AND  i.parenttype = 'Cheque Batch'
              AND  b.name != %(self_name)s
              AND  b.docstatus < 2
              AND  b.status IN %(open_statuses)s
            LIMIT  1
            """,
            {
                "cheque": cheque,
                "self_name": self.name or "",
                "open_statuses": OPEN_BATCH_STATUSES,
            },
        )
        return rows[0][0] if rows else None

    # ------------------------------------------------------------------ #
    #  Totals                                                              #
    # ------------------------------------------------------------------ #

    def _sync_item_snapshots(self):
        """Denormalised member columns, refreshed on every save.

        They are what the Deposit Slip prints, so a stale snapshot is a wrong
        document handed to a bank teller.
        """
        for row in self.items:
            cheque = frappe.db.get_value(
                "Cheque",
                row.cheque,
                ["cheque_no", "party_type", "party", "amount", "due_date", "status"],
                as_dict=True,
            )
            if not cheque:
                continue
            row.cheque_no = cheque.cheque_no
            row.party_type = cheque.party_type
            row.party = cheque.party
            row.amount = cheque.amount
            row.due_date = cheque.due_date
            row.status = cheque.status

    def _compute_totals(self):
        self.total_amount = sum(flt(r.amount) for r in self.items)
        self.total_cheques = len(self.items)

    # ------------------------------------------------------------------ #
    #  Cascade                                                             #
    # ------------------------------------------------------------------ #

    def _cascade(self, new_status, notes, before_each=None):
        """Move every member to `new_status` through the single-cheque endpoint.

        Failures are accumulated rather than raised one at a time (the C3 fix):
        a partial batch must tell the user exactly which cheques still need
        attention instead of stopping at the first one. If *every* member fails
        there is no useful work to commit, so that raises.

        Members already at or past the target are skipped silently — a re-run on
        a partially applied batch should be a no-op for the rows that landed.
        """
        failed = []
        moved = 0

        for row in self.items:
            try:
                current = frappe.db.get_value("Cheque", row.cheque, "status")
                if current == new_status or current in ("Cleared", "Cancelled"):
                    continue

                if before_each:
                    before_each(row)

                change_cheque_status(row.cheque, new_status, notes=notes)
                moved += 1
            except Exception as exc:
                failed.append((row.cheque, str(exc)[:300]))
                frappe.log_error(
                    title=f"ChequeBatch: failed to move {row.cheque} to {new_status}",
                    message=frappe.get_traceback(with_context=True),
                )

        self._sync_item_snapshots()
        self._report_cascade(new_status, failed, moved)
        return {"moved": moved, "failed": failed}

    def _report_cascade(self, new_status, failed, moved):
        if not failed:
            return

        details = "<br/>".join(f"• {name}: {err}" for name, err in failed)
        noun = _ACTION_NOUN.get(new_status, new_status)

        if moved == 0:
            frappe.throw(
                _("All {0} cheques failed to transition to {1}:<br/>{2}").format(
                    len(failed), new_status, details
                ),
                title=_("Batch {0} Failed").format(noun),
            )

        frappe.msgprint(
            _(
                "<b>{0} of {1} cheques failed to transition to {2}:</b>"
                "<br/>{3}<br/><br/>"
                "The remaining cheques moved successfully. Fix the failed ones individually."
            ).format(len(failed), len(self.items), new_status, details),
            title=_("Partial Batch {0}").format(noun),
            indicator="orange",
        )

    # ------------------------------------------------------------------ #
    #  Whitelisted batch actions (§5.2)                                    #
    # ------------------------------------------------------------------ #

    @frappe.whitelist()
    def clear_batch(self, cleared_date=None):
        """The bank confirmed the whole deposit cleared.

        Each member goes through `change_cheque_status`, so each one settles its
        own Payment Entry — the batch has no accounting effect of its own.
        """
        self._require_submitted()
        if self.status not in ("Deposited",):
            frappe.throw(
                _("Only a Deposited batch can be cleared (this one is {0}).").format(self.status),
                frappe.ValidationError,
            )

        cleared_date = cleared_date or today()

        def stamp(row):
            frappe.db.set_value("Cheque", row.cheque, "cleared_date", cleared_date, update_modified=False)

        result = self._cascade("Cleared", f"Batch cleared via {self.name}.", before_each=stamp)
        self.db_set("status", "Cleared", update_modified=False)
        return result

    @frappe.whitelist()
    def bounce_batch(self, bounce_reason):
        """The bank returned the whole deposit."""
        self._require_submitted()
        if self.status not in ("Deposited",):
            frappe.throw(
                _("Only a Deposited batch can be bounced (this one is {0}).").format(self.status),
                frappe.ValidationError,
            )
        if not bounce_reason:
            frappe.throw(_("Bounce Reason is required."), frappe.ValidationError)

        def stamp(row):
            frappe.db.set_value(
                "Cheque", row.cheque, "bounce_reason", bounce_reason, update_modified=False
            )

        result = self._cascade("Bounced", f"Batch bounced via {self.name}.", before_each=stamp)
        self.db_set("status", "Bounced", update_modified=False)
        return result

    def _require_submitted(self):
        if self.docstatus != 1:
            frappe.throw(
                _("Cheque Batch must be submitted before it can be cleared or bounced."),
                frappe.ValidationError,
            )
        frappe.has_permission("Cheque Batch", "write", doc=self, throw=True)
