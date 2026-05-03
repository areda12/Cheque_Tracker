# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Test helpers for the selling-side cheque accounting flow.

The legacy PE-based accounting tests have been removed; the selling
side is now JE-only and tested at the Cheque doctype level. The helpers
here are imported by other test modules (e.g. test_cheque_batch.py) to
build incoming cheques against a properly-configured site.
"""

import frappe
from frappe.utils import add_days, today


def _get_or_create_pdc_account(company):
    """Return (or create) a simple asset account to act as the PDC Receivable
    account. Created with account_type="" — party-tagged JE rows are accepted
    on plain accounts, no need for account_type='Receivable'."""
    existing = frappe.get_all(
        "Account",
        filters={
            "account_name": "PDC Receivable - Test",
            "company": company,
            "is_group": 0,
        },
        limit=1,
    )
    if existing:
        return existing[0].name

    parent_options = frappe.get_all(
        "Account",
        filters={
            "account_type": "Receivable",
            "company": company,
            "is_group": 1,
        },
        fields=["name"],
        limit=1,
    )
    if not parent_options:
        parent_options = frappe.get_all(
            "Account",
            filters={
                "root_type": "Asset",
                "company": company,
                "is_group": 1,
                "parent_account": ["like", "Current Assets%"],
            },
            fields=["name"],
            limit=1,
        )
    if not parent_options:
        return None

    acc = frappe.new_doc("Account")
    acc.account_name    = "PDC Receivable - Test"
    acc.company         = company
    acc.parent_account  = parent_options[0].name
    acc.account_type    = ""
    acc.flags.ignore_permissions = True
    acc.insert()
    return acc.name


def _get_test_bank_gl_account(company):
    """Get any bank/cash GL account for testing clearance."""
    results = frappe.get_all(
        "Account",
        filters={
            "account_type": ["in", ["Bank", "Cash"]],
            "company": company,
            "is_group": 0,
        },
        fields=["name"],
        limit=1,
    )
    return results[0].name if results else None


def _get_or_create_test_bank():
    """Return a stable test Bank record name. Idempotent."""
    bank_name = "Test Bank - Cheque Tracker"
    if frappe.db.exists("Bank", bank_name):
        return bank_name
    bank = frappe.new_doc("Bank")
    bank.bank_name = bank_name
    bank.flags.ignore_permissions = True
    bank.insert()
    return bank.name


def _env():
    companies = frappe.get_all("Company", limit=1)
    if not companies:
        return None, None, None
    company  = companies[0].name
    customers = frappe.get_all("Customer", limit=1)
    customer  = customers[0].name if customers else None
    currency  = frappe.db.get_value("Company", company, "default_currency") or "USD"
    return company, customer, currency


def _make_incoming_cheque(company, customer, currency, pdc_account=None):
    """Create and submit an Incoming cheque. The pdc_account argument is
    accepted for backwards-compat with old call sites — it is ignored here
    because the PDC account is now sourced exclusively from Cheque Tracker
    Settings (caller must configure it via _configure_settings)."""
    chq = frappe.new_doc("Cheque")
    chq.cheque_type  = "Incoming"
    chq.company      = company
    chq.party_type   = "Customer"
    chq.party        = customer
    chq.amount       = 1000
    chq.currency     = currency
    chq.due_date     = add_days(today(), 30)
    chq.issue_date   = today()
    chq.cheque_no    = f"TEST-{frappe.generate_hash(length=6)}"
    chq.drawer_name  = "Test Drawer"
    chq.drawee_bank  = _get_or_create_test_bank()
    chq.flags.ignore_permissions = True
    chq.insert()
    chq = frappe.get_doc("Cheque", chq.name)
    chq.flags.ignore_permissions = True
    chq.submit()
    chq.reload()
    return chq


def _configure_settings(company, pdc_account, bank_gl_account):
    """Configure Cheque Tracker Settings for tests."""
    settings = frappe.get_doc("Cheque Tracker Settings")
    settings.pdc_receivable_account = pdc_account
    settings.default_bank_gl_account = bank_gl_account
    settings.flags.ignore_permissions = True
    settings.save()
