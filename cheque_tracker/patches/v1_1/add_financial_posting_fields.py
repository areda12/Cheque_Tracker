# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Patch v1.1.0: Add financial posting link fields to the Cheque DocType.

New columns added:
  - pdc_account
  - recording_payment_entry
  - clearance_journal_entry
  - reversal_journal_entry
  - pre_bounce_status
"""

import frappe


def execute():
    """Add new columns to tabCheque if they don't exist yet."""
    columns = {
        "pdc_account":              "varchar(140)",
        "recording_payment_entry":  "varchar(140)",
        "clearance_journal_entry":  "varchar(140)",
        "reversal_journal_entry":   "varchar(140)",
        "pre_bounce_status":        "varchar(140)",
    }

    existing_columns = frappe.db.get_table_columns("Cheque")

    for col_name, col_type in columns.items():
        if col_name not in existing_columns:
            frappe.db.sql(
                f"ALTER TABLE `tabCheque` ADD COLUMN `{col_name}` {col_type} DEFAULT NULL"
            )
            frappe.db.commit()

    # Also reload the DocType meta so new fields are visible
    frappe.reload_doc("cheque_tracker", "doctype", "cheque")
    frappe.reload_doc("cheque_tracker", "doctype", "cheque_tracker_settings")
