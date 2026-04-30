# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class ChequeBatch(Document):
    def validate(self):
        self._check_duplicates()
        self._compute_totals()

    def on_submit(self):
        self.db_set("status", "Deposited", update_modified=False)
        self._mark_cheques_deposited()

    def on_cancel(self):
        self.db_set("status", "Cancelled", update_modified=False)

    def _check_duplicates(self):
        seen = set()
        for row in self.items:
            if row.cheque in seen:
                frappe.throw(
                    _("Cheque {0} is listed more than once.").format(row.cheque),
                    frappe.ValidationError,
                )
            seen.add(row.cheque)

    def _compute_totals(self):
        self.total_amount  = sum(float(r.amount or 0) for r in self.items)
        self.total_cheques = len(self.items)

    def _mark_cheques_deposited(self):
        """
        Transition each cheque in self.items to Deposited via
        log_status_change. Failures are accumulated and surfaced to
        the user via msgprint instead of being silently swallowed
        (C3 fix). If every cheque fails, raise — no point in a
        "successful" batch with zero effective work.

        Cheques already in Deposited / Cleared / Cancelled are
        skipped silently (not counted as failures): the batch is a
        bulk action and a re-run on a partially-applied set should
        be a no-op for the already-transitioned rows.
        """
        failed = []
        for row in self.items:
            try:
                doc = frappe.get_doc("Cheque", row.cheque)
                if doc.status not in ("Deposited", "Cleared", "Cancelled"):
                    doc.log_status_change(
                        "Deposited",
                        notes=f"Batch deposited via {self.name}.",
                    )
            except Exception as e:
                failed.append((row.cheque, str(e)[:300]))
                frappe.log_error(
                    title=f"ChequeBatch: failed to mark {row.cheque} as Deposited",
                    message=frappe.get_traceback(),
                )

        if not failed:
            return

        if len(failed) == len(self.items):
            # All cheques failed — fail loudly rather than commit a
            # zero-effective-work batch.
            details = "<br/>".join(
                f"• {name}: {err}" for name, err in failed
            )
            frappe.throw(
                _("All {0} cheques failed to transition to Deposited:<br/>{1}").format(
                    len(self.items), details
                ),
                title=_("Batch Deposit Failed"),
            )

        # Partial failure — let the successful transitions commit,
        # but show the user exactly which ones to fix.
        details = "<br/>".join(
            f"• {name}: {err}" for name, err in failed
        )
        frappe.msgprint(
            _(
                "<b>{0} of {1} cheques failed to transition to Deposited:</b>"
                "<br/>{2}<br/><br/>"
                "The remaining cheques were marked Deposited successfully. "
                "Fix the failed cheques individually."
            ).format(len(failed), len(self.items), details),
            title=_("Partial Batch Deposit"),
            indicator="orange",
        )
