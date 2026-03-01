# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe.tests.utils import FrappeTestCase

from cheque_tracker.cheque_tracker.doctype.cheque_book.test_cheque_book import make_cheque_book


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

    def test_cleared_cheque_cannot_cancel(self):
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7400, 7410, company=co, bank_account=ba)
        cb.submit()
        chq = _outgoing(cb, co, cu, cy)
        chq.submit()
        frappe.db.set_value("Cheque", chq.name, "status", "Cleared")
        chq.reload()
        with self.assertRaises(frappe.ValidationError):
            chq.cancel()

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
