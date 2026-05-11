# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Cheque Tracker accounting helpers — both directions.

Cheque is the source of truth for in-flight cheques. The ONLY GL
posting in the lifecycle is the Clearance JE.

Incoming (selling side):
  Clearance JE on workflow Clear (Deposited → Cleared):
      Dr  Bank GL                          (no party)
      Cr  Debtors  OR  Advance Received    (party=Customer; ref=SI or SO)

Outgoing (buying side):
  Clearance JE on workflow Clear (Handed Over → Cleared):
      Dr  Creditors  OR  Advances Paid     (party=Supplier; ref=PI or PO)
      Cr  Bank GL                          (no party)

Submit, Deposit, Hand Over, Bounce, Return, Cancel Cheque are pure
status changes with no GL effect.
"""

import frappe
from frappe import _


def _resolve_credit_account(cheque):
    """For Incoming Clearance JE — Cr side."""
    if cheque.reference_doctype == "Sales Order":
        adv = frappe.get_cached_value(
            "Company", cheque.company, "default_advance_received_account"
        )
        if not adv:
            frappe.throw(_("Company has no default Advance Received account configured."))
        return adv
    return frappe.get_cached_value(
        "Company", cheque.company, "default_receivable_account"
    )


def _resolve_debit_account(cheque):
    """For Outgoing Clearance JE — Dr side."""
    if cheque.reference_doctype == "Purchase Order":
        adv = frappe.get_cached_value(
            "Company", cheque.company, "default_advance_paid_account"
        )
        if not adv:
            frappe.throw(_("Company has no default Advance Paid account configured."))
        return adv
    return frappe.get_cached_value(
        "Company", cheque.company, "default_payable_account"
    )


def _bank_gl_account(cheque):
    """Resolve the bank's GL account from the Bank Account link on the cheque."""
    if not cheque.bank_account:
        frappe.throw(_("Cheque has no Bank Account set; cannot post Clearance JE."))
    bank_gl = frappe.db.get_value("Bank Account", cheque.bank_account, "account")
    if not bank_gl:
        frappe.throw(_("Bank Account {0} has no GL account linked.").format(cheque.bank_account))
    return bank_gl


def make_clearance_je(cheque):
    """Submit the Clearance JE. Direction-aware.
    This is the ONLY GL posting in either cheque lifecycle."""
    if cheque.clearance_je:
        frappe.throw(_("Clearance JE already exists: {0}").format(cheque.clearance_je))

    bank_gl = _bank_gl_account(cheque)

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Bank Entry"
    je.posting_date = cheque.cleared_date or frappe.utils.today()
    je.company = cheque.company
    je.cheque_no = cheque.cheque_no
    je.cheque_date = cheque.issue_date
    direction = "received from" if cheque.cheque_type == "Incoming" else "issued to"
    je.user_remark = (
        f"Cheque #{cheque.cheque_no} cleared, {direction} {cheque.party} "
        f"for {cheque.reference_doctype or 'no reference'} "
        f"{cheque.reference_name or ''} (Cheque doc: {cheque.name})"
    ).strip()

    if cheque.cheque_type == "Incoming":
        # Dr Bank / Cr Debtors|Advance Received
        credit_acc = _resolve_credit_account(cheque)
        is_so_advance = cheque.reference_doctype == "Sales Order"

        je.append("accounts", {
            "account": bank_gl,
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

    else:  # Outgoing
        # Dr Creditors|Advances Paid / Cr Bank
        debit_acc = _resolve_debit_account(cheque)
        is_po_advance = cheque.reference_doctype == "Purchase Order"

        dr_row = {
            "account": debit_acc,
            "party_type": cheque.party_type,
            "party": cheque.party,
            "debit_in_account_currency": cheque.amount,
            "credit_in_account_currency": 0,
        }
        if cheque.reference_doctype and cheque.reference_name:
            dr_row["reference_type"] = cheque.reference_doctype
            dr_row["reference_name"] = cheque.reference_name
        if is_po_advance:
            dr_row["is_advance"] = "Yes"
        je.append("accounts", dr_row)
        je.append("accounts", {
            "account": bank_gl,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": cheque.amount,
        })

    je.flags.ignore_permissions = True
    je.insert()
    je.submit()

    cheque.db_set("clearance_je", je.name, update_modified=False)
    return je.name


def validate_replacement_candidate(original, replacement):
    """Validate that ``replacement`` is a legitimate replacement for
    ``original``. Throws frappe.ValidationError on hard failures; emits
    frappe.msgprint warnings on soft failures."""

    if replacement.name == original.name:
        frappe.throw(_("A cheque cannot replace itself."))

    if replacement.docstatus != 0:
        frappe.throw(_("Replacement cheque must be in Draft state, not {0}.").format(
            {0: "Draft", 1: "Submitted", 2: "Cancelled"}[replacement.docstatus]
        ))

    if replacement.cheque_type != original.cheque_type:
        frappe.throw(_("Replacement must be the same cheque type ({0}).").format(
            original.cheque_type
        ))

    if (replacement.party_type != original.party_type
            or replacement.party != original.party):
        frappe.throw(_("Replacement must be from/to the same party: {0} {1}.").format(
            original.party_type, original.party
        ))

    if (replacement.reference_doctype != original.reference_doctype
            or replacement.reference_name != original.reference_name):
        frappe.throw(_("Replacement must reference the same {0}: {1}.").format(
            original.reference_doctype or "(no reference)",
            original.reference_name or "(no reference)",
        ))

    if replacement.original_cheque:
        frappe.throw(_("Cheque {0} is already marked as a replacement for {1}.").format(
            replacement.name, replacement.original_cheque
        ))

    # Soft warning: amount mismatch is allowed (bank fees, partial replacement)
    # but worth surfacing.
    if replacement.amount != original.amount:
        frappe.msgprint(
            _("Note: replacement amount ({0}) differs from original ({1}). "
              "Continuing — verify this is intentional.").format(
                replacement.amount, original.amount
            ),
            indicator="orange",
            alert=True,
        )


def cancel_clearance_je(cheque):
    if not cheque.clearance_je:
        return
    je = frappe.get_doc("Journal Entry", cheque.clearance_je)
    if je.docstatus != 1:
        return
    je.flags.ignore_permissions = True
    # The Cheque still holds clearance_je as a back-link audit trail; bypass
    # Frappe's check_no_back_links_exist since the link is intentional.
    je.flags.ignore_links = True
    je.cancel()
