# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Hooks for Payment Entry doc_events.

When a Payment Entry linked to a Cheque via ``recording_payment_entry`` is
submitted or cancelled, the cheque status and event log must be updated.

Mapping:
  recording_payment_entry submitted  → cheque status = "Received"
  recording_payment_entry cancelled  → cheque status = "Draft"
               (if status was Received / In Safe / Deposited / Presented
                roll back to Draft because the AR reversal is now voided)
"""

import frappe
from frappe.utils import now_datetime


def _find_cheque_by_pe(pe_name: str, field: str):
    """Return the first Cheque name that has pe_name in the given link field."""
    results = frappe.get_all(
        "Cheque",
        filters={field: pe_name},
        fields=["name", "status"],
        limit=1,
    )
    return results[0] if results else None


def _append_event_and_save(cheque_name, event_type, ref_doctype, ref_name, notes=""):
    doc = frappe.get_doc("Cheque", cheque_name)
    doc.append("events", {
        "event_type":        event_type,
        "event_datetime":    now_datetime(),
        "to_holder":         frappe.session.user,
        "reference_doctype": ref_doctype,
        "reference_name":    ref_name,
        "notes":             notes,
    })
    doc.flags.ignore_permissions = True
    doc.save()


# ---------------------------------------------------------------------------
# on_submit
# ---------------------------------------------------------------------------

def payment_entry_on_submit(doc, method=None):
    """Called after Payment Entry is submitted."""
    _handle_recording_pe_submit(doc)


def _handle_recording_pe_submit(pe_doc):
    row = _find_cheque_by_pe(pe_doc.name, "recording_payment_entry")
    if not row:
        return

    cheque_name = row["name"]
    current_status = row["status"]

    # Only transition to Received if the cheque hasn't progressed further
    safe_to_set = current_status in ("Draft", "Received")
    new_status = "Received"

    updates = {"status": new_status} if safe_to_set else {}
    if updates:
        frappe.db.set_value("Cheque", cheque_name, updates)

    _append_event_and_save(
        cheque_name,
        event_type="Received",
        ref_doctype="Payment Entry",
        ref_name=pe_doc.name,
        notes=(
            f"Recording Payment Entry {pe_doc.name} submitted. "
            f"AR decreased; PDC Receivable increased by {pe_doc.paid_amount}."
        ),
    )


# ---------------------------------------------------------------------------
# on_cancel
# ---------------------------------------------------------------------------

def payment_entry_on_cancel(doc, method=None):
    """Called after Payment Entry is cancelled."""
    _handle_recording_pe_cancel(doc)


def _handle_recording_pe_cancel(pe_doc):
    row = _find_cheque_by_pe(pe_doc.name, "recording_payment_entry")
    if not row:
        return

    cheque_name = row["name"]

    # Roll back cheque status to Draft (recording voided means AR not yet settled)
    rollback_status = "Draft"
    frappe.db.set_value("Cheque", cheque_name, {
        "status":                  rollback_status,
        "recording_payment_entry": None,
    })

    _append_event_and_save(
        cheque_name,
        event_type="Note",
        ref_doctype="Payment Entry",
        ref_name=pe_doc.name,
        notes=(
            f"Recording Payment Entry {pe_doc.name} was CANCELLED. "
            f"Cheque status rolled back to Draft. AR NOT settled."
        ),
    )
