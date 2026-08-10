# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Tests for Cheque Batch deposit-batch transitioning logic.

Covers C3 from AUDIT.md §8 Part B Should-fix #10:
  _mark_cheques_deposited previously swallowed per-cheque errors
  silently. The fix accumulates failures and surfaces them via
  frappe.msgprint, and raises when ALL cheques fail.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cheque_tracker.tests.utils import get_test_env
from frappe.utils import now_datetime, today

from cheque_tracker.cheque_tracker.doctype.cheque.cheque import Cheque
from cheque_tracker.cheque_tracker.doctype.cheque.test_cheque_financial import (
    _configure_settings,
    _env as _financial_env,
    _get_or_create_pdc_account,
    _get_test_bank_gl_account,
    _make_incoming_cheque,
)


class TestChequeBatch(FrappeTestCase):

    def setUp(self):
        company, customer, currency = _financial_env()
        if not all([company, customer]):
            self.skipTest("Missing company or customer in test environment.")
        pdc = _get_or_create_pdc_account(company)
        bank_gl = _get_test_bank_gl_account(company)
        if not pdc or not bank_gl:
            self.skipTest("Missing PDC / Bank GL account in test environment.")
        _configure_settings(company, pdc, bank_gl)
        self.company = company
        self.customer = customer
        self.currency = currency
        self.pdc = pdc

        # Reset message_log so per-test assertions on msgprint are clean.
        # Frappe accumulates msgprint output across calls in the same
        # request; tests that share a request need a clean slate.
        frappe.message_log = []

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _make_batch(self, cheque_names):
        """Insert a Draft Cheque Batch with the given cheque names as items."""
        batch = frappe.new_doc("Cheque Batch")
        batch.batch_date = today()
        batch.company = self.company
        # v1.3: the cascade routes through change_cheque_status, which enforces
        # the same preconditions a single deposit does — including a bank
        # account. The batch supplies it to members that have none.
        batch.bank_account = get_test_env()["bank_account"]
        for chq_name in cheque_names:
            batch.append("items", {"cheque": chq_name})
        batch.flags.ignore_permissions = True
        batch.insert()
        return batch

    # ------------------------------------------------------------------ #
    #  C3 regressions                                                     #
    # ------------------------------------------------------------------ #

    def test_c3_all_succeed_no_msgprint(self):
        """Happy path: every cheque transitions to Deposited, no
        Partial / Failed msgprint surfaces."""
        chq1 = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc)
        chq2 = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc)

        batch = self._make_batch([chq1.name, chq2.name])
        batch.flags.ignore_permissions = True
        batch.submit()

        self.assertEqual(frappe.db.get_value("Cheque", chq1.name, "status"), "Deposited")
        self.assertEqual(frappe.db.get_value("Cheque", chq2.name, "status"), "Deposited")

        titles = [m.get("title", "") for m in frappe.message_log]
        self.assertNotIn("Partial Batch Deposit", titles)
        self.assertNotIn("Batch Deposit Failed", titles)

    def test_c3_partial_failure_surfaces_via_msgprint(self):
        """Partial failure: one cheque fails, the other transitions.
        The user sees a Partial Batch Deposit msgprint listing the
        failed cheque, and an Error Log row is created."""
        chq1 = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc)
        chq2 = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc)
        failing = chq1.name

        real_log = Cheque.log_status_change

        def selective_fail(self_doc, *args, **kwargs):
            if self_doc.name == failing:
                raise frappe.ValidationError("simulated failure")
            return real_log(self_doc, *args, **kwargs)

        before = now_datetime()
        batch = self._make_batch([chq1.name, chq2.name])
        batch.flags.ignore_permissions = True
        with patch.object(Cheque, "log_status_change", selective_fail):
            batch.submit()

        # Failed cheque untouched; successful one transitioned.
        self.assertEqual(frappe.db.get_value("Cheque", chq1.name, "status"), "Received")
        self.assertEqual(frappe.db.get_value("Cheque", chq2.name, "status"), "Deposited")

        # msgprint surfaced with the expected title and the failing cheque's name.
        partial = [
            m for m in frappe.message_log
            if "Partial Batch Deposit" in m.get("title", "")
        ]
        self.assertEqual(
            len(partial), 1,
            f"Expected exactly one 'Partial Batch Deposit' msgprint, got: {frappe.message_log}",
        )
        self.assertIn(chq1.name, partial[0].get("message", ""))

        # Error Log row created mentioning the failing cheque.
        errs = frappe.get_all(
            "Error Log",
            filters={"creation": [">=", before]},
            fields=["method", "error"],
            limit=20,
        )
        batch_errs = [
            e for e in errs
            if "ChequeBatch" in (e.get("method") or "")
            or "ChequeBatch" in (e.get("error") or "")
        ]
        self.assertGreater(
            len(batch_errs), 0,
            f"Expected an Error Log row mentioning 'ChequeBatch'. Got: {errs}",
        )

    def test_c3_all_fail_raises(self):
        """All cheques fail: batch.submit() must raise (zero useful
        work). Both cheques remain in their pre-batch state."""
        chq1 = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc)
        chq2 = _make_incoming_cheque(self.company, self.customer, self.currency, self.pdc)

        def always_fail(self_doc, *args, **kwargs):
            raise frappe.ValidationError("simulated failure")

        batch = self._make_batch([chq1.name, chq2.name])
        batch.flags.ignore_permissions = True
        with patch.object(Cheque, "log_status_change", always_fail):
            with self.assertRaises(frappe.ValidationError):
                batch.submit()

        # No transitions persisted.
        self.assertEqual(frappe.db.get_value("Cheque", chq1.name, "status"), "Received")
        self.assertEqual(frappe.db.get_value("Cheque", chq2.name, "status"), "Received")
