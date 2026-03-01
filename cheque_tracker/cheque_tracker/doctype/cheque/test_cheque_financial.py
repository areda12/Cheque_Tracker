# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Automated tests for Cheque Tracker financial posting logic.

Run with:
    bench run-tests --app cheque_tracker --module cheque_tracker.cheque_tracker.doctype.cheque.test_cheque_financial

These tests exercise:
  1. make_recording_payment_entry → cheque becomes Received on PE submit
  2. make_clearance_journal_entry → cheque becomes Cleared on JE submit
  3. process_bounce (after recording PE submitted) → reversal JE → Bounced
  4. process_bounce (recording PE still Draft) → cancels PE → Bounced
  5. Idempotency: calling make_recording_pe twice returns same PE
  6. Double-posting prevention: submitting PE twice is blocked by ERPNext
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today, nowdate


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _get_or_create_pdc_account(company):
    """
    Return (or create) a simple asset account to act as the PDC Receivable account.
    We look for any existing asset account that we can reuse in tests.
    """
    # Try to find an existing test PDC account
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

    # Find a suitable parent
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
    acc.account_type    = "Receivable"
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


def _get_receivable_account(company):
    return frappe.db.get_value("Company", company, "default_receivable_account")


def _env():
    companies = frappe.get_all("Company", limit=1)
    if not companies:
        return None, None, None, None
    company  = companies[0].name
    customers = frappe.get_all("Customer", limit=1)
    customer  = customers[0].name if customers else None
    currency  = frappe.db.get_value("Company", company, "default_currency") or "USD"
    return company, customer, currency


def _make_incoming_cheque(company, customer, currency, pdc_account=None):
    """Create and submit an Incoming cheque."""
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
    if pdc_account:
        chq.pdc_account = pdc_account
    chq.flags.ignore_permissions = True
    chq.insert()
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


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestChequeFinancial(FrappeTestCase):

    def setUp(self):
        company, customer, currency = _env()
        if not all([company, customer]):
            self.skipTest("Missing company or customer in test environment.")
        self.company  = company
        self.customer = customer
        self.currency = currency
        self.pdc_account = _get_or_create_pdc_account(company)
        self.bank_gl     = _get_test_bank_gl_account(company)
        self.ar_account  = _get_receivable_account(company)

        if not self.pdc_account:
            self.skipTest("Could not create/find PDC Receivable account.")
        if not self.bank_gl:
            self.skipTest("No Bank/Cash GL account found.")
        if not self.ar_account:
            self.skipTest("No default receivable account on company.")

        _configure_settings(self.company, self.pdc_account, self.bank_gl)

    # ------------------------------------------------------------------
    # 1. Recording Payment Entry → cheque Received on submit
    # ------------------------------------------------------------------

    def test_make_recording_pe_creates_draft(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)
        pe_name = make_recording_payment_entry(chq.name)

        self.assertIsNotNone(pe_name)
        pe = frappe.get_doc("Payment Entry", pe_name)
        self.assertEqual(pe.docstatus, 0)          # Draft
        self.assertEqual(pe.payment_type, "Receive")
        self.assertEqual(pe.party, self.customer)
        self.assertEqual(pe.paid_to, self.pdc_account)
        self.assertAlmostEqual(float(pe.paid_amount), 1000.0)

        # cheque should link back
        chq.reload()
        self.assertEqual(chq.recording_payment_entry, pe_name)

    def test_recording_pe_submit_sets_cheque_received(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)
        pe_name = make_recording_payment_entry(chq.name)

        pe = frappe.get_doc("Payment Entry", pe_name)
        pe.flags.ignore_permissions = True
        pe.submit()

        chq.reload()
        self.assertEqual(chq.status, "Received")

        # Event must be logged
        event_types = [e.event_type for e in chq.events]
        self.assertIn("Received", event_types)

        # The event referencing the PE
        pe_events = [e for e in chq.events if e.reference_name == pe_name]
        self.assertTrue(len(pe_events) >= 1)

    # ------------------------------------------------------------------
    # 2. Clearance Journal Entry → cheque Cleared on submit
    # ------------------------------------------------------------------

    def test_clearance_je_submit_sets_cheque_cleared(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
            make_clearance_journal_entry,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)

        # Step 1: record
        pe_name = make_recording_payment_entry(chq.name)
        pe = frappe.get_doc("Payment Entry", pe_name)
        pe.flags.ignore_permissions = True
        pe.submit()

        # Set bank account for clearance
        frappe.db.set_value("Cheque", chq.name, "bank_account", None)  # rely on settings

        # Step 2: clearance JE
        je_name = make_clearance_journal_entry(chq.name)
        self.assertIsNotNone(je_name)

        je = frappe.get_doc("Journal Entry", je_name)
        self.assertEqual(je.docstatus, 0)

        je.flags.ignore_permissions = True
        je.submit()

        chq.reload()
        self.assertEqual(chq.status, "Cleared")
        self.assertIsNotNone(chq.cleared_date)

        event_types = [e.event_type for e in chq.events]
        self.assertIn("Cleared", event_types)

    # ------------------------------------------------------------------
    # 3. Bounce after recording PE submitted → reversal JE → Bounced
    # ------------------------------------------------------------------

    def test_bounce_after_submitted_pe_creates_reversal_je(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
            process_bounce,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)
        pe_name = make_recording_payment_entry(chq.name)

        pe = frappe.get_doc("Payment Entry", pe_name)
        pe.flags.ignore_permissions = True
        pe.submit()

        chq.reload()
        self.assertEqual(chq.status, "Received")

        # Process bounce
        reversal_name = process_bounce(chq.name, notes="Test bounce reason")
        self.assertIsNotNone(reversal_name)

        # reversal JE should be Draft
        rev = frappe.get_doc("Journal Entry", reversal_name)
        self.assertEqual(rev.docstatus, 0)

        # Submit reversal JE → cheque becomes Bounced
        rev.flags.ignore_permissions = True
        rev.submit()

        chq.reload()
        self.assertEqual(chq.status, "Bounced")

        event_types = [e.event_type for e in chq.events]
        self.assertIn("Bounced", event_types)

    # ------------------------------------------------------------------
    # 4. Bounce when recording PE is still Draft → PE cancelled → Bounced
    # ------------------------------------------------------------------

    def test_bounce_cancels_draft_pe(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
            process_bounce,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)
        pe_name = make_recording_payment_entry(chq.name)

        # Do NOT submit the PE — it stays Draft
        pe = frappe.get_doc("Payment Entry", pe_name)
        self.assertEqual(pe.docstatus, 0)

        reversal_name = process_bounce(chq.name, notes="Bounce with draft PE")
        self.assertEqual(reversal_name, "")  # no JE created; PE was just cancelled

        # PE should be cancelled
        pe.reload()
        self.assertEqual(pe.docstatus, 2)

        # Cheque should be Bounced
        chq.reload()
        self.assertEqual(chq.status, "Bounced")

        event_types = [e.event_type for e in chq.events]
        self.assertIn("Bounced", event_types)

    # ------------------------------------------------------------------
    # 5. Idempotency: calling make_recording_pe twice returns same PE
    # ------------------------------------------------------------------

    def test_idempotent_recording_pe(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)

        pe_name_1 = make_recording_payment_entry(chq.name)
        pe_name_2 = make_recording_payment_entry(chq.name)

        # Same PE returned (idempotent)
        self.assertEqual(pe_name_1, pe_name_2)

        # Only one PE should exist for this cheque
        pes = frappe.get_all(
            "Payment Entry",
            filters={"name": ["in", [pe_name_1, pe_name_2]]},
        )
        self.assertEqual(len(pes), 1)

    # ------------------------------------------------------------------
    # 6. Cannot create second recording PE when first is Submitted
    # ------------------------------------------------------------------

    def test_no_new_pe_after_submit(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)
        pe_name = make_recording_payment_entry(chq.name)

        pe = frappe.get_doc("Payment Entry", pe_name)
        pe.flags.ignore_permissions = True
        pe.submit()

        # Calling again should return the same submitted PE (not create new)
        pe_name_2 = make_recording_payment_entry(chq.name)
        self.assertEqual(pe_name, pe_name_2)

    # ------------------------------------------------------------------
    # 7. Clearance JE cancel rolls back cheque status
    # ------------------------------------------------------------------

    def test_clearance_je_cancel_rolls_back_status(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
            make_clearance_journal_entry,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)

        pe_name = make_recording_payment_entry(chq.name)
        pe = frappe.get_doc("Payment Entry", pe_name)
        pe.flags.ignore_permissions = True
        pe.submit()

        je_name = make_clearance_journal_entry(chq.name)
        je = frappe.get_doc("Journal Entry", je_name)
        je.flags.ignore_permissions = True
        je.submit()

        chq.reload()
        self.assertEqual(chq.status, "Cleared")

        # Now cancel the JE
        je.reload()
        je.flags.ignore_permissions = True
        je.cancel()

        chq.reload()
        self.assertEqual(chq.status, "Received")  # rolled back

    # ------------------------------------------------------------------
    # 8. Protected fields cannot be edited after PE submitted
    # ------------------------------------------------------------------

    def test_protected_fields_blocked_after_pe_submit(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
        )

        chq = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc_account)
        pe_name = make_recording_payment_entry(chq.name)
        pe = frappe.get_doc("Payment Entry", pe_name)
        pe.flags.ignore_permissions = True
        pe.submit()

        chq.reload()
        chq.amount = 9999  # attempt to change amount
        chq.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            chq.save()
