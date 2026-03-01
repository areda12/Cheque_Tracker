# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Financial posting helpers for the Cheque Tracker app.

Accounting model (Incoming cheques / Notes Receivable):
  A) Recording payment entry (Receive):
       Dr  PDC Receivable (asset)      [paid_to]
       Cr  Accounts Receivable         [paid_from / party]
     → AR decreases; PDC asset increases.

  B) Clearance journal entry:
       Dr  Bank GL Account
       Cr  PDC Receivable (asset)
     → PDC asset removed; cash in bank.

  C) Bounce reversal journal entry:
       Dr  PDC Receivable (asset)  ← reverses the clearance movement
       then either cancel the Recording PE, OR
       Dr  Accounts Receivable           [if recording PE already submitted]
       Cr  PDC Receivable (asset)
     → AR restored; PDC removed.

Rules:
  • Cheque / Cheque Event NEVER post directly to GL.
  • Status on Cheque changes ONLY when accounting doc is Submitted.
  • Creating a draft doc does NOT change cheque status.
  • Idempotent: if the linked doc already exists in Draft, update; if Submitted, raise.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, today, flt


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_pdc_account(cheque_doc):
    """Resolve PDC Receivable Account for this cheque (doc override → settings)."""
    if cheque_doc.pdc_account:
        return cheque_doc.pdc_account
    settings = frappe.get_cached_doc("Cheque Tracker Settings")
    if settings.pdc_receivable_account:
        return settings.pdc_receivable_account
    frappe.throw(
        _("PDC Receivable Account is not configured. "
          "Please set it in Cheque Tracker Settings or on the Cheque itself."),
        frappe.ValidationError,
    )


def _get_receivable_account(cheque_doc):
    """Get the default receivable GL account for the company."""
    account = frappe.db.get_value(
        "Company", cheque_doc.company, "default_receivable_account"
    )
    if not account:
        frappe.throw(
            _("Default Receivable Account is not set for company {0}.").format(
                cheque_doc.company
            ),
            frappe.ValidationError,
        )
    return account


def _get_bank_gl_account(cheque_doc):
    """
    Resolve the bank GL account for clearance:
      1. bank_account field on Cheque → linked Bank Account → account
      2. Cheque Tracker Settings → default_bank_gl_account
    """
    if cheque_doc.bank_account:
        gl = frappe.db.get_value("Bank Account", cheque_doc.bank_account, "account")
        if gl:
            return gl
    settings = frappe.get_cached_doc("Cheque Tracker Settings")
    if settings.default_bank_gl_account:
        return settings.default_bank_gl_account
    frappe.throw(
        _("Bank GL Account could not be resolved. "
          "Set the Bank Account on the Cheque or configure Cheque Tracker Settings."),
        frappe.ValidationError,
    )


def _append_cheque_event(cheque_name, event_type, ref_doctype=None, ref_name=None, notes=None):
    """Append an event row to the Cheque and save."""
    cheque = frappe.get_doc("Cheque", cheque_name)
    cheque.append("events", {
        "event_type":       event_type,
        "event_datetime":   now_datetime(),
        "to_holder":        frappe.session.user,
        "reference_doctype": ref_doctype,
        "reference_name":   ref_name,
        "notes":            notes,
    })
    cheque.flags.ignore_permissions = True
    cheque.save()


def _set_cheque_fields(cheque_name, updates: dict):
    """Safely update multiple cheque fields via db.set_value."""
    frappe.db.set_value("Cheque", cheque_name, updates)


# ---------------------------------------------------------------------------
# A) Recording Payment Entry
# ---------------------------------------------------------------------------

@frappe.whitelist()
def make_recording_payment_entry(cheque_name: str) -> str:
    """
    Create (or return existing) a DRAFT Payment Entry that records cheque receipt.

    Payment Entry type: Receive
      paid_from  = Accounts Receivable (party account)
      paid_to    = PDC Receivable Account
      party_type = Customer
      party      = cheque.party

    Returns the Payment Entry name.
    """
    cheque = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=cheque, throw=True)

    # --- validations ---
    if cheque.cheque_type != "Incoming":
        frappe.throw(_("Recording Payment Entry is only applicable for Incoming cheques."))
    if cheque.party_type != "Customer":
        frappe.throw(_("Recording Payment Entry requires Party Type = Customer."))
    if not cheque.party:
        frappe.throw(_("Party (Customer) is required to create a Recording Payment Entry."))
    if not cheque.company:
        frappe.throw(_("Company is required."))
    if flt(cheque.amount) <= 0:
        frappe.throw(_("Cheque Amount must be greater than zero."))

    pdc_account  = _get_pdc_account(cheque)
    ar_account   = _get_receivable_account(cheque)

    # --- idempotency ---
    existing = cheque.recording_payment_entry
    if existing:
        status = frappe.db.get_value("Payment Entry", existing, "docstatus")
        if status == 0:
            # Draft — update key amounts and return
            _update_recording_pe(existing, cheque, pdc_account, ar_account)
            return existing
        elif status == 1:
            # Already submitted — do not touch
            frappe.msgprint(
                _("Recording Payment Entry {0} is already submitted.").format(existing),
                alert=True,
            )
            return existing
        # status == 2 (cancelled) → create fresh one below

    # --- create new PE ---
    pe = frappe.new_doc("Payment Entry")
    pe.payment_type          = "Receive"
    pe.company               = cheque.company
    pe.posting_date          = today()
    pe.party_type            = "Customer"
    pe.party                 = cheque.party
    pe.paid_from             = ar_account
    pe.paid_to               = pdc_account
    pe.paid_amount           = flt(cheque.amount)
    pe.received_amount       = flt(cheque.amount)
    pe.source_exchange_rate  = 1
    pe.target_exchange_rate  = 1
    pe.reference_no          = cheque.cheque_no
    pe.reference_date        = cheque.issue_date or today()
    pe.remarks               = (
        f"PDC Recording for Cheque {cheque_name} | "
        f"Cheque No: {cheque.cheque_no} | Party: {cheque.party}"
    )

    # Resolve currencies
    company_currency = frappe.db.get_value("Company", cheque.company, "default_currency")
    pe.paid_from_account_currency = cheque.currency or company_currency
    pe.paid_to_account_currency   = company_currency

    # Allocate to Sales Invoice if referenced
    if cheque.reference_doctype == "Sales Invoice" and cheque.reference_name:
        si_outstanding = frappe.db.get_value(
            "Sales Invoice", cheque.reference_name, "outstanding_amount"
        ) or 0
        pe.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name":    cheque.reference_name,
            "allocated_amount":  min(flt(cheque.amount), flt(si_outstanding)),
        })

    pe.flags.ignore_permissions = True
    pe.insert()

    # Link back to cheque
    _set_cheque_fields(cheque_name, {"recording_payment_entry": pe.name})

    frappe.msgprint(
        _("Recording Payment Entry {0} created in Draft. Submit it to finalise.").format(pe.name),
        alert=True,
    )
    return pe.name


def _update_recording_pe(pe_name, cheque_doc, pdc_account, ar_account):
    """Update an existing Draft Recording PE with current cheque values."""
    pe = frappe.get_doc("Payment Entry", pe_name)
    pe.paid_amount     = flt(cheque_doc.amount)
    pe.received_amount = flt(cheque_doc.amount)
    pe.paid_from       = ar_account
    pe.paid_to         = pdc_account
    pe.reference_no    = cheque_doc.cheque_no
    pe.flags.ignore_permissions = True
    pe.save()


# ---------------------------------------------------------------------------
# B) Clearance Journal Entry
# ---------------------------------------------------------------------------

@frappe.whitelist()
def make_clearance_journal_entry(cheque_name: str) -> str:
    """
    Create (or return existing) a DRAFT Journal Entry that clears the cheque.

    JE accounts:
      Dr  Bank GL Account         (debit)
      Cr  PDC Receivable Account  (credit)

    Returns the Journal Entry name.
    """
    cheque = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=cheque, throw=True)

    if cheque.cheque_type != "Incoming":
        frappe.throw(_("Clearance Journal Entry is only applicable for Incoming cheques."))
    if flt(cheque.amount) <= 0:
        frappe.throw(_("Cheque Amount must be greater than zero."))

    pdc_account  = _get_pdc_account(cheque)
    bank_account = _get_bank_gl_account(cheque)

    # --- idempotency ---
    existing = cheque.clearance_journal_entry
    if existing:
        status = frappe.db.get_value("Journal Entry", existing, "docstatus")
        if status == 0:
            _update_clearance_je(existing, cheque, pdc_account, bank_account)
            return existing
        elif status == 1:
            frappe.msgprint(
                _("Clearance Journal Entry {0} is already submitted.").format(existing),
                alert=True,
            )
            return existing

    # --- create new JE ---
    je = frappe.new_doc("Journal Entry")
    je.voucher_type  = "Journal Entry"
    je.company       = cheque.company
    je.posting_date  = today()
    je.cheque_no     = cheque.cheque_no
    je.cheque_date   = cheque.due_date
    je.user_remark   = (
        f"Cheque Clearance: {cheque_name} | "
        f"Cheque No: {cheque.cheque_no} | Party: {cheque.party}"
    )

    currency = cheque.currency or frappe.db.get_value(
        "Company", cheque.company, "default_currency"
    )
    amt = flt(cheque.amount)

    je.append("accounts", {
        "account":                    bank_account,
        "debit_in_account_currency":  amt,
        "credit_in_account_currency": 0,
        "cost_center":                cheque.cost_center,
        "project":                    cheque.project,
    })
    je.append("accounts", {
        "account":                    pdc_account,
        "debit_in_account_currency":  0,
        "credit_in_account_currency": amt,
    })

    je.flags.ignore_permissions = True
    je.insert()

    _set_cheque_fields(cheque_name, {"clearance_journal_entry": je.name})

    frappe.msgprint(
        _("Clearance Journal Entry {0} created in Draft. Submit it to mark cheque as Cleared.").format(je.name),
        alert=True,
    )
    return je.name


def _update_clearance_je(je_name, cheque_doc, pdc_account, bank_account):
    """Update an existing Draft Clearance JE."""
    je = frappe.get_doc("Journal Entry", je_name)
    amt = flt(cheque_doc.amount)
    for row in je.accounts:
        if row.account == bank_account:
            row.debit_in_account_currency  = amt
            row.credit_in_account_currency = 0
        elif row.account == pdc_account:
            row.debit_in_account_currency  = 0
            row.credit_in_account_currency = amt
    je.cheque_no   = cheque_doc.cheque_no
    je.cheque_date = cheque_doc.due_date
    je.flags.ignore_permissions = True
    je.save()


# ---------------------------------------------------------------------------
# C) Bounce / Reversal
# ---------------------------------------------------------------------------

@frappe.whitelist()
def process_bounce(cheque_name: str, notes: str = "") -> str:
    """
    Handle a bounced cheque.

    Strategy:
      1. If recording_payment_entry exists and is Draft  → cancel it (cheapest reversal).
      2. If recording_payment_entry is Submitted         → create a reversing Journal Entry
           Dr  Accounts Receivable  (party account)
           Cr  PDC Receivable Account
      3. If no recording PE exists                       → just mark status Bounced (no GL).

    Returns the reversal JE name (or '' if step 1 or 3).
    """
    cheque = frappe.get_doc("Cheque", cheque_name)
    frappe.has_permission("Cheque", "write", doc=cheque, throw=True)

    if cheque.status in ("Cleared", "Cancelled"):
        frappe.throw(
            _("Cannot process bounce for a cheque in status: {0}.").format(cheque.status)
        )

    # Idempotency: reversal JE already exists
    if cheque.reversal_journal_entry:
        rev_status = frappe.db.get_value(
            "Journal Entry", cheque.reversal_journal_entry, "docstatus"
        )
        if rev_status == 1:
            frappe.msgprint(
                _("Reversal Journal Entry {0} already submitted. Cheque should already be Bounced.").format(
                    cheque.reversal_journal_entry
                ),
                alert=True,
            )
            return cheque.reversal_journal_entry
        elif rev_status == 0:
            return cheque.reversal_journal_entry  # draft, return for user to submit

    rec_pe = cheque.recording_payment_entry
    reversal_je_name = ""

    if rec_pe:
        rec_status = frappe.db.get_value("Payment Entry", rec_pe, "docstatus")

        if rec_status == 0:
            # --- strategy 1: cancel the draft PE ---
            pe_doc = frappe.get_doc("Payment Entry", rec_pe)
            pe_doc.flags.ignore_permissions = True
            pe_doc.cancel()
            _set_cheque_fields(cheque_name, {"recording_payment_entry": None})
            # No JE needed; status will be set by caller after confirmation
            frappe.msgprint(
                _("Draft Recording Payment Entry {0} has been cancelled.").format(rec_pe),
                alert=True,
            )
            # Directly update status here since PE cancel hook won't fire for bounce
            _finalize_bounce(cheque_name, notes)
            return ""

        elif rec_status == 1:
            # --- strategy 2: create reversing JE ---
            pdc_account = _get_pdc_account(cheque)
            ar_account  = _get_receivable_account(cheque)
            amt         = flt(cheque.amount)

            je = frappe.new_doc("Journal Entry")
            je.voucher_type  = "Journal Entry"
            je.company       = cheque.company
            je.posting_date  = today()
            je.cheque_no     = cheque.cheque_no
            je.cheque_date   = cheque.due_date
            je.user_remark   = (
                f"Cheque Bounce Reversal: {cheque_name} | "
                f"Cheque No: {cheque.cheque_no} | Party: {cheque.party} | "
                f"Notes: {notes}"
            )

            # Dr AR (restore receivable), Cr PDC (remove asset)
            je.append("accounts", {
                "account":                    ar_account,
                "party_type":                 "Customer",
                "party":                      cheque.party,
                "debit_in_account_currency":  amt,
                "credit_in_account_currency": 0,
            })
            je.append("accounts", {
                "account":                    pdc_account,
                "debit_in_account_currency":  0,
                "credit_in_account_currency": amt,
            })

            je.flags.ignore_permissions = True
            je.insert()
            reversal_je_name = je.name

            _set_cheque_fields(cheque_name, {
                "reversal_journal_entry": je.name,
                "pre_bounce_status":       cheque.status,
            })

            frappe.msgprint(
                _("Reversal Journal Entry {0} created in Draft. Submit it to mark cheque as Bounced.").format(
                    je.name
                ),
                alert=True,
            )
            return je.name

    # --- strategy 3: no recording PE → just mark Bounced ---
    _finalize_bounce(cheque_name, notes)
    return ""


def _finalize_bounce(cheque_name: str, notes: str = ""):
    """Set cheque status to Bounced and log event (used when no GL reversal needed)."""
    cheque = frappe.get_doc("Cheque", cheque_name)
    old_status = cheque.status
    _set_cheque_fields(cheque_name, {"status": "Bounced"})
    cheque.reload()
    cheque.append("events", {
        "event_type":       "Bounced",
        "event_datetime":   now_datetime(),
        "to_holder":        frappe.session.user,
        "notes":            notes or f"Cheque bounced (status was: {old_status}).",
    })
    cheque.flags.ignore_permissions = True
    cheque.save()
