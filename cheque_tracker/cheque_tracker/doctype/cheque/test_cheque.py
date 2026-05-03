# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from cheque_tracker.cheque_tracker.doctype.cheque.cheque import Cheque, change_cheque_status
from cheque_tracker.cheque_tracker.doctype.cheque_book.test_cheque_book import make_cheque_book
from cheque_tracker.cheque_tracker.doctype.cheque.test_cheque_financial import (
    _configure_settings,
    _get_or_create_pdc_account,
    _get_test_bank_gl_account,
    _make_incoming_cheque,
    _env as _financial_env,
)


def _env():
    companies = frappe.get_all("Company", limit=1)
    if not companies:
        return None, None, None, None
    company = companies[0].name
    ba = frappe.get_all("Bank Account", filters={"company": company}, limit=1)
    bank_account = ba[0].name if ba else None
    customers = frappe.get_all("Customer", limit=1)
    customer = customers[0].name if customers else None
    currency = frappe.db.get_value("Company", company, "default_currency") or "USD"
    return company, bank_account, customer, currency


def _outgoing(cb, company, customer, currency):
    chq = frappe.new_doc("Cheque")
    chq.cheque_type  = "Outgoing"
    chq.company      = company
    chq.party_type   = "Customer"
    chq.party        = customer
    chq.amount       = 1000
    chq.currency     = currency
    chq.due_date     = frappe.utils.add_days(frappe.utils.today(), 30)
    chq.cheque_book  = cb.name
    chq.cheque_no    = "PLACEHOLDER"  # overwritten by before_save
    chq.flags.ignore_permissions = True
    chq.insert()
    chq = frappe.get_doc("Cheque", chq.name)
    return chq


class TestCheque(FrappeTestCase):

    def _env(self):
        co, ba, cu, cy = _env()
        if not all([co, ba, cu]):
            self.skipTest("Missing company / bank account / customer")
        return co, ba, cu, cy

    def test_outgoing_reserves_leaf_on_save(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7000, 7010, company=co, bank_account=ba)
        cb.submit()
        chq = _outgoing(cb, co, cu, cy)
        self.assertIsNotNone(chq.cheque_leaf)
        self.assertIsNotNone(chq.cheque_no)
        self.assertEqual(
            frappe.db.get_value("Cheque Leaf", chq.cheque_leaf, "leaf_status"),
            "Reserved",
        )

    def test_outgoing_cheque_no_matches_leaf(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7100, 7110, company=co, bank_account=ba)
        cb.submit()
        chq = _outgoing(cb, co, cu, cy)
        leaf_no = frappe.db.get_value("Cheque Leaf", chq.cheque_leaf, "cheque_no")
        self.assertEqual(chq.cheque_no, leaf_no)

    def test_submit_marks_leaf_issued(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7200, 7210, company=co, bank_account=ba)
        cb.submit()
        chq = _outgoing(cb, co, cu, cy)
        chq.submit()
        self.assertEqual(
            frappe.db.get_value("Cheque Leaf", chq.cheque_leaf, "leaf_status"),
            "Issued",
        )

    def test_cancel_voids_leaf(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7300, 7310, company=co, bank_account=ba)
        cb.submit()
        chq  = _outgoing(cb, co, cu, cy)
        chq.submit()
        leaf = chq.cheque_leaf
        chq.cancel()
        self.assertEqual(
            frappe.db.get_value("Cheque Leaf", leaf, "leaf_status"),
            "Voided",
        )

    def test_two_cheques_get_different_leaves(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7500, 7520, company=co, bank_account=ba)
        cb.submit()
        chq1 = _outgoing(cb, co, cu, cy)
        chq2 = _outgoing(cb, co, cu, cy)
        self.assertNotEqual(chq1.cheque_leaf, chq2.cheque_leaf)
        self.assertNotEqual(chq1.cheque_no,   chq2.cheque_no)

    def test_manual_cheque_no_override_raises(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7600, 7610, company=co, bank_account=ba)
        cb.submit()
        chq = _outgoing(cb, co, cu, cy)
        chq.cheque_no = "MANUAL-OVERRIDE-999"
        with self.assertRaises(frappe.ValidationError):
            chq.save()

    def test_incoming_cheque_no_book_required(self):
        co, ba, cu, cy = self._env()
        chq = frappe.new_doc("Cheque")
        chq.cheque_type  = "Incoming"
        chq.company      = co
        chq.party_type   = "Customer"
        chq.party        = cu
        chq.amount       = 500
        chq.currency     = cy
        chq.due_date     = frappe.utils.add_days(frappe.utils.today(), 15)
        chq.cheque_no    = "EXT-99999"
        chq.drawer_name  = "John Doe"
        chq.flags.ignore_permissions = True
        chq.insert()
        self.assertEqual(chq.cheque_no, "EXT-99999")
        self.assertIsNone(chq.cheque_leaf)

    def test_event_created_on_insert(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7700, 7710, company=co, bank_account=ba)
        cb.submit()
        chq = _outgoing(cb, co, cu, cy)
        events = frappe.get_all(
            "Cheque Event",
            filters={"parent": chq.name, "event_type": "Created"},
        )
        self.assertGreaterEqual(len(events), 1)

    # ------------------------------------------------------------------ #
    #  C1 regression — savepoint rollback on post-reservation failure     #
    # ------------------------------------------------------------------ #

    def test_c1_leaf_rollback_when_later_validation_fails(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7800, 7810, company=co, bank_account=ba)
        cb.submit()

        reserved_before = frappe.get_all(
            "Cheque Leaf",
            filters={"cheque_book": cb.name, "leaf_status": "Reserved"},
        )
        self.assertEqual(len(reserved_before), 0)

        sp = "test_c1_rollback"
        frappe.db.savepoint(sp)
        try:
            with patch.object(
                Cheque,
                "_validate_outgoing_cheque_no",
                side_effect=frappe.ValidationError("simulated post-reservation failure"),
            ):
                chq = frappe.new_doc("Cheque")
                chq.cheque_type = "Outgoing"
                chq.company = co
                chq.party_type = "Customer"
                chq.party = cu
                chq.amount = 1000
                chq.currency = cy
                chq.due_date = frappe.utils.add_days(frappe.utils.today(), 30)
                chq.cheque_book = cb.name
                chq.cheque_no = "PLACEHOLDER"
                chq.flags.ignore_permissions = True
                with self.assertRaises(frappe.ValidationError):
                    chq.insert()
        finally:
            frappe.db.rollback(save_point=sp)

        reserved_after = frappe.get_all(
            "Cheque Leaf",
            filters={"cheque_book": cb.name, "leaf_status": "Reserved"},
        )
        self.assertEqual(
            len(reserved_after), 0,
            "Leaf reservation must be rolled back when a later validation fails.",
        )
        orphans = frappe.get_all(
            "Cheque Leaf",
            filters={"cheque_book": cb.name, "cheque": ["is", "set"]},
            pluck="name",
        )
        self.assertEqual(
            orphans, [],
            f"No leaf should retain a cheque link after rollback (found: {orphans}).",
        )

    # ------------------------------------------------------------------ #
    #  F1 regressions — workflow-role gating on change_cheque_status      #
    # ------------------------------------------------------------------ #

    def _make_received_for_f1(self):
        """Submitted Incoming cheque starts in Received status."""
        company, customer, currency = _financial_env()
        if not all([company, customer]):
            self.skipTest("Missing company or customer in test environment.")

        pdc = _get_or_create_pdc_account(company)
        bank_gl = _get_test_bank_gl_account(company)
        if not pdc or not bank_gl:
            self.skipTest("Missing PDC / Bank GL account in test environment.")

        _configure_settings(company, pdc, bank_gl)

        chq = _make_incoming_cheque(company, customer, currency, pdc)
        chq = frappe.get_doc("Cheque", chq.name)
        self.assertEqual(
            chq.status, "Received",
            f"Expected Incoming cheque to be in Received status after submit, got {chq.status!r}",
        )
        return chq

    def _ensure_accounts_user(self):
        email = "test_f1_accounts_user@cheque-tracker.test"
        if frappe.db.exists("User", email):
            user = frappe.get_doc("User", email)
        else:
            user = frappe.new_doc("User")
            user.email = email
            user.first_name = "Test F1"
            user.last_name = "Accounts"
            user.enabled = 1
            user.send_welcome_email = 0
            user.flags.ignore_permissions = True
            user.insert()

        unwanted = {"Treasury User", "System Manager", "Administrator"}
        user.roles = [r for r in user.roles if r.role not in unwanted]
        if not any(r.role == "Accounts User" for r in user.roles):
            user.append("roles", {"role": "Accounts User"})
        user.flags.ignore_permissions = True
        user.save()
        return email

    def _make_outgoing_for_f1(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(8500, 8510, company=co, bank_account=ba)
        cb.submit()
        chq = _outgoing(cb, co, cu, cy)
        chq.submit()
        chq.reload()
        return chq

    def test_f1_accounts_user_blocked_from_treasury_only_transition(self):
        if not frappe.db.exists("Role", "Accounts User"):
            self.skipTest("Accounts User role not present on test site.")

        chq = self._make_received_for_f1()
        before_status = frappe.db.get_value("Cheque", chq.name, "status")

        accounts_email = self._ensure_accounts_user()
        try:
            frappe.set_user(accounts_email)
            with self.assertRaises(frappe.PermissionError):
                change_cheque_status(chq.name, "In Safe")
        finally:
            frappe.set_user("Administrator")

        after_status = frappe.db.get_value("Cheque", chq.name, "status")
        self.assertEqual(after_status, before_status)

    def test_f1_treasury_user_allowed_for_treasury_transition(self):
        chq = self._make_received_for_f1()

        result = change_cheque_status(chq.name, "In Safe")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            frappe.db.get_value("Cheque", chq.name, "status"),
            "In Safe",
        )

    def test_f1_unknown_transition_falls_through(self):
        chq = self._make_outgoing_for_f1()
        with self.assertRaises(Exception):
            change_cheque_status(chq.name, "InvalidStatus")

    # ------------------------------------------------------------------ #
    #  F2 regression — Settings read perm for Accounts User               #
    # ------------------------------------------------------------------ #

    def _ensure_treasury_user(self):
        email = "test_f2_treasury_user@cheque-tracker.test"
        if frappe.db.exists("User", email):
            user = frappe.get_doc("User", email)
        else:
            user = frappe.new_doc("User")
            user.email = email
            user.first_name = "Test F2"
            user.last_name = "Treasury"
            user.enabled = 1
            user.send_welcome_email = 0
            user.flags.ignore_permissions = True
            user.insert()

        unwanted = {"Accounts User", "System Manager", "Administrator"}
        user.roles = [r for r in user.roles if r.role not in unwanted]
        if not any(r.role == "Treasury User" for r in user.roles):
            user.append("roles", {"role": "Treasury User"})
        user.flags.ignore_permissions = True
        user.save()
        return email

    def test_f2_accounts_user_has_settings_read_permission(self):
        if not frappe.db.exists("Role", "Accounts User"):
            self.skipTest("Accounts User role not present on test site.")

        accounts_user = self._ensure_accounts_user()
        has_read = frappe.has_permission(
            "Cheque Tracker Settings",
            "read",
            user=accounts_user,
        )
        self.assertTrue(
            has_read,
            f"Accounts User {accounts_user} must have read permission on "
            "Cheque Tracker Settings.",
        )

        treasury_user = self._ensure_treasury_user()
        self.assertTrue(
            frappe.has_permission(
                "Cheque Tracker Settings",
                "read",
                user=treasury_user,
            ),
            "Treasury User read permission regressed.",
        )

        self.assertTrue(
            frappe.has_permission(
                "Cheque Tracker Settings",
                "write",
                user="Administrator",
            ),
            "System Manager (Administrator) write permission regressed.",
        )
