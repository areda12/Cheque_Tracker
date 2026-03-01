# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Hooks for Journal Entry doc_events.

Clearance JE (linked via ``clearance_journal_entry``):
  submitted → cheque status = "Cleared" + cleared_date = today()
  cancelled → cheque status rolled back to safe prior state (Received/Deposited/Presented)

Reversal / Bounce JE (linked via ``reversal_journal_entry``):
  submitted → cheque status = "Bounced"
  cancelled → cheque status restored from pre_bounce_status
"""

import frappe
from frappe.utils import now_datetime, today


def _find_cheque_by_je(je_name: str, field: str):
    results = frappe.get_all(
        "Cheque",
        filters={field: je_name},
        fields=["name", "status", "pre_bounce_status"],
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

def journal_entry_on_submit(doc, method=None):
    """Called after Journal Entry is submitted."""
    _handle_clearance_je_submit(doc)
    _handle_reversal_je_submit(doc)


def _handle_clearance_je_submit(je_doc):
    row = _find_cheque_by_je(je_doc.name, "clearance_journal_entry")
    if not row:
        return

    cheque_name = row["name"]
    frappe.db.set_value("Cheque", cheque_name, {
        "status":       "Cleared",
        "cleared_date": today(),
    })
    _append_event_and_save(
        cheque_name,
        event_type="Cleared",
        ref_doctype="Journal Entry",
        ref_name=je_doc.name,
        notes=(
            f"Clearance Journal Entry {je_doc.name} submitted. "
            "Funds moved from PDC Receivable to Bank."
        ),
    )


def _handle_reversal_je_submit(je_doc):
    row = _find_cheque_by_je(je_doc.name, "reversal_journal_entry")
    if not row:
        return

    cheque_name = row["name"]
    frappe.db.set_value("Cheque", cheque_name, {"status": "Bounced"})
    _append_event_and_save(
        cheque_name,
        event_type="Bounced",
        ref_doctype="Journal Entry",
        ref_name=je_doc.name,
        notes=(
            f"Reversal Journal Entry {je_doc.name} submitted. "
            "PDC Receivable reversed; AR restored. Cheque marked Bounced."
        ),
    )


# ---------------------------------------------------------------------------
# on_cancel
# ---------------------------------------------------------------------------

def journal_entry_on_cancel(doc, method=None):
    """Called after Journal Entry is cancelled."""
    _handle_clearance_je_cancel(doc)
    _handle_reversal_je_cancel(doc)


def _handle_clearance_je_cancel(je_doc):
    row = _find_cheque_by_je(je_doc.name, "clearance_journal_entry")
    if not row:
        return

    cheque_name = row["name"]
    # Roll back to the last meaningful status before clearing.
    # Safe fallback: Received (the recording PE was already submitted).
    rollback = "Received"
    frappe.db.set_value("Cheque", cheque_name, {
        "status":                    rollback,
        "cleared_date":              None,
        "clearance_journal_entry":   None,
    })
    _append_event_and_save(
        cheque_name,
        event_type="Note",
        ref_doctype="Journal Entry",
        ref_name=je_doc.name,
        notes=(
            f"Clearance Journal Entry {je_doc.name} was CANCELLED. "
            f"Cheque status rolled back to {rollback}."
        ),
    )


def _handle_reversal_je_cancel(je_doc):
    row = _find_cheque_by_je(je_doc.name, "reversal_journal_entry")
    if not row:
        return

    cheque_name  = row["name"]
    prior_status = row["pre_bounce_status"] or "Received"

    frappe.db.set_value("Cheque", cheque_name, {
        "status":                 prior_status,
        "reversal_journal_entry": None,
        "pre_bounce_status":      None,
    })
    _append_event_and_save(
        cheque_name,
        event_type="Note",
        ref_doctype="Journal Entry",
        ref_name=je_doc.name,
        notes=(
            f"Reversal Journal Entry {je_doc.name} was CANCELLED. "
            f"Cheque status restored to {prior_status}."
        ),
    )
