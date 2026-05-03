"""Drop the legacy handover_je column from Cheque.

The Hand Over JE was abandoned in v3.0 — the only GL posting in the
cheque lifecycle is now the Clearance JE. If any cheque still
references a handover_je, skip the drop and log loudly so an operator
can rebuild the data manually before re-running.
"""

import frappe


def execute():
    if not frappe.db.has_column("Cheque", "handover_je"):
        return

    non_null = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabCheque`
        WHERE handover_je IS NOT NULL AND handover_je != ''
    """)[0][0]
    if non_null > 0:
        frappe.log_error(
            f"drop_handover_je_field: {non_null} cheques still reference handover_je. "
            f"Cancel and rebuild manually before re-running.",
            "Cheque Tracker migration",
        )
        return

    frappe.db.sql("ALTER TABLE `tabCheque` DROP COLUMN `handover_je`")
    frappe.db.commit()
    frappe.clear_cache(doctype="Cheque")
