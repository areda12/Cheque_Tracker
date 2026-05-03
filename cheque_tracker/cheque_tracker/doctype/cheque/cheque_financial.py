# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Selling-side accounting helpers for Cheque Tracker.

Model: JE-only, symmetric, Cheque-doc-driven.

  Hand Over JE (on Cheque submit, Draft → Received):
      Dr  PDC Receivable                 (party=Customer)
      Cr  Debtors  OR  Advance Received  (party=Customer; ref=SI or SO)

  Clearance JE (on Mark Cleared):
      Dr  Bank GL                        (no party)
      Cr  PDC Receivable                 (party=Customer)

  Bounce: cancel the Hand Over JE — ERPNext's cancel emits the reversal
  GL rows automatically and reverts SI status / SO advance_paid.

  Cancel Cheque: cancel clearance_je (if any), then cancel handover_je.

Buying side / Outgoing cheques are out of scope here — these helpers
only fire for cheque_type == "Incoming".
"""

import frappe
from frappe import _


def get_settings():
    return frappe.get_single("Cheque Tracker Settings")


def _resolve_credit_account(cheque):
    """For selling-side Hand Over JE: choose the Cr account based on reference."""
    if cheque.reference_doctype == "Sales Order":
        adv = frappe.get_cached_value(
            "Company", cheque.company, "default_advance_received_account"
        )
        if not adv:
            frappe.throw(_("Company has no default Advance Received account configured."))
        return adv
    # SI or no reference → Debtors
    return frappe.get_cached_value(
        "Company", cheque.company, "default_receivable_account"
    )


def _pdc_receivable_account(cheque):
    settings = get_settings()
    if not settings.pdc_receivable_account:
        frappe.throw(_("Cheque Tracker Settings: PDC Receivable Account is not configured."))
    return settings.pdc_receivable_account


def _bank_gl_account(cheque):
    """Resolve the bank's GL account from the Bank Account link on the cheque."""
    if not cheque.bank_account:
        frappe.throw(_("Cheque has no Bank Account set; cannot post Clearance JE."))
    bank_gl = frappe.db.get_value("Bank Account", cheque.bank_account, "account")
    if not bank_gl:
        frappe.throw(_("Bank Account {0} has no GL account linked.").format(cheque.bank_account))
    return bank_gl


# ─────────────────────────────────────────────────────────────────────────────
# Selling side — Hand Over JE
# ─────────────────────────────────────────────────────────────────────────────

def make_handover_je(cheque):
    """Create and submit the Hand Over JE for an Incoming cheque.
    Called from Cheque.before_submit (Draft → Received)."""
    if cheque.cheque_type != "Incoming":
        return  # buying side out of scope for this PR
    if cheque.handover_je:
        frappe.throw(_("Hand Over JE already exists: {0}").format(cheque.handover_je))

    pdc = _pdc_receivable_account(cheque)
    credit_acc = _resolve_credit_account(cheque)
    is_so_advance = cheque.reference_doctype == "Sales Order"

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.posting_date = cheque.received_date or cheque.issue_date or frappe.utils.today()
    je.company = cheque.company
    je.cheque_no = cheque.cheque_no
    je.cheque_date = cheque.issue_date
    je.user_remark = (
        f"Cheque #{cheque.cheque_no} received from {cheque.party} "
        f"for {cheque.reference_doctype or 'no reference'} "
        f"{cheque.reference_name or ''} (Cheque doc: {cheque.name})"
    ).strip()

    je.append("accounts", {
        "account": pdc,
        "party_type": cheque.party_type,
        "party": cheque.party,
        "debit_in_account_currency": cheque.amount,
        "credit_in_account_currency": 0,
    })

    cr_row = {
        "account": credit_acc,
        "party_type": cheque.party_type,
        "party": cheque.party,
        "debit_in_account_currency": 0,
        "credit_in_account_currency": cheque.amount,
    }
    if cheque.reference_doctype and cheque.reference_name:
        cr_row["reference_type"] = cheque.reference_doctype
        cr_row["reference_name"] = cheque.reference_name
    if is_so_advance:
        cr_row["is_advance"] = "Yes"
    je.append("accounts", cr_row)

    je.flags.ignore_permissions = True
    je.insert()
    je.submit()

    cheque.db_set("handover_je", je.name, update_modified=False)
    return je.name


def cancel_handover_je(cheque):
    """Cancel the Hand Over JE — used for both Bounce and Cheque cancellation."""
    if not cheque.handover_je:
        return
    je = frappe.get_doc("Journal Entry", cheque.handover_je)
    if je.docstatus != 1:
        return  # already cancelled
    je.flags.ignore_permissions = True
    # Workflow-driven cancellations (Bounce/Return/Cancel Cheque) leave the
    # Cheque at docstatus=1 with handover_je still set. That live back-link
    # would trip Frappe's check_no_back_links_exist. The link is our audit
    # trail to a cancelled JE — bypass the check.
    je.flags.ignore_links = True
    je.cancel()


# ─────────────────────────────────────────────────────────────────────────────
# Selling side — Clearance JE
# ─────────────────────────────────────────────────────────────────────────────

def make_clearance_je(cheque):
    """Create and submit the Clearance JE for an Incoming cheque.
    Called from the 'Mark Cleared' action."""
    if cheque.cheque_type != "Incoming":
        return
    if not cheque.handover_je:
        frappe.throw(_("Cannot clear a cheque without a Hand Over JE on file."))
    if cheque.clearance_je:
        frappe.throw(_("Clearance JE already exists: {0}").format(cheque.clearance_je))

    pdc = _pdc_receivable_account(cheque)
    bank_gl = _bank_gl_account(cheque)

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Bank Entry"
    je.posting_date = cheque.cleared_date or frappe.utils.today()
    je.company = cheque.company
    je.cheque_no = cheque.cheque_no
    je.cheque_date = cheque.issue_date
    je.user_remark = (
        f"Cheque #{cheque.cheque_no} cleared from {cheque.party} "
        f"(Hand Over: {cheque.handover_je}; "
        f"ref: {cheque.reference_doctype or '-'} {cheque.reference_name or ''}; "
        f"Cheque doc: {cheque.name})"
    ).strip()

    je.append("accounts", {
        "account": bank_gl,
        "debit_in_account_currency": cheque.amount,
        "credit_in_account_currency": 0,
    })
    je.append("accounts", {
        "account": pdc,
        "party_type": cheque.party_type,
        "party": cheque.party,
        "debit_in_account_currency": 0,
        "credit_in_account_currency": cheque.amount,
    })

    je.flags.ignore_permissions = True
    je.insert()
    je.submit()

    cheque.db_set("clearance_je", je.name, update_modified=False)
    return je.name


def cancel_clearance_je(cheque):
    if not cheque.clearance_je:
        return
    je = frappe.get_doc("Journal Entry", cheque.clearance_je)
    if je.docstatus != 1:
        return
    je.flags.ignore_permissions = True
    # Same back-link bypass as cancel_handover_je — see comment there.
    je.flags.ignore_links = True
    je.cancel()
