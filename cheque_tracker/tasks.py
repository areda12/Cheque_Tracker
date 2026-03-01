# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""
Scheduled tasks for Cheque Tracker.
Registered in hooks.py under scheduler_events.
"""

import frappe
from frappe.utils import today


def auto_update_cheque_statuses():
    """
    Daily job:
    1. Log a warning for every Deposited/Presented cheque whose due_date has passed.
    2. Refresh leaf counters on Active Cheque Books.

    Does NOT auto-transition statuses – that requires human confirmation.
    """
    logger = frappe.logger("cheque_tracker", allow_site=True)

    overdue = frappe.get_all(
        "Cheque",
        filters={
            "status": ["in", ["Deposited", "Presented"]],
            "due_date": ["<", today()],
            "docstatus": 1,
        },
        fields=["name", "due_date", "status", "party", "amount"],
    )
    for row in overdue:
        logger.warning(
            "[ChequeTracker] OVERDUE  %s | party=%s | amount=%s | "
            "status=%s | due=%s",
            row.name, row.party, row.amount, row.status, row.due_date,
        )

    # Refresh counters
    active_books = frappe.get_all(
        "Cheque Book",
        filters={"status": "Active", "docstatus": 1},
        pluck="name",
    )
    for book_name in active_books:
        try:
            book = frappe.get_doc("Cheque Book", book_name)
            book._refresh_counters()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"ChequeTracker: counter refresh failed for {book_name}",
            )
