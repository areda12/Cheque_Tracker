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
        for row in self.items:
            try:
                doc = frappe.get_doc("Cheque", row.cheque)
                if doc.status not in ("Deposited", "Cleared", "Cancelled"):
                    doc.log_status_change(
                        "Deposited",
                        notes=f"Batch deposited via {self.name}.",
                    )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"ChequeBatch: failed to mark {row.cheque} as Deposited",
                )
