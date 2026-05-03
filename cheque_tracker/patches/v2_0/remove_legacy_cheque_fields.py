import frappe


def execute():
    """Drop seven legacy Cheque fields. Verified zero usage on dev site
    before this patch was authored — see PR description for evidence."""
    legacy_fieldnames = [
        "payment_entry",
        "journal_entry",
        "recording_payment_entry",
        "clearance_journal_entry",
        "reversal_journal_entry",
        "pre_bounce_status",
        "pdc_account",
    ]
    for fname in legacy_fieldnames:
        if frappe.db.has_column("Cheque", fname):
            non_null = frappe.db.sql(
                f"""SELECT COUNT(*) FROM `tabCheque`
                    WHERE `{fname}` IS NOT NULL AND `{fname}` != ''"""
            )[0][0]
            if non_null > 0:
                frappe.log_error(
                    f"remove_legacy_cheque_fields: skipping `{fname}` "
                    f"({non_null} populated rows). Resolve manually.",
                    "Cheque Tracker migration"
                )
                continue
            frappe.db.sql(f"ALTER TABLE `tabCheque` DROP COLUMN `{fname}`")
    frappe.db.commit()
    frappe.clear_cache(doctype="Cheque")
