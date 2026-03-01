# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe.tests.utils import FrappeTestCase


# ------------------------------------------------------------------ #
#  Shared factory                                                      #
# ------------------------------------------------------------------ #

def make_cheque_book(start=1, end=10, company=None, bank_account=None,
                     sequence_type="Numeric", digits_count=0, prefix="", suffix=""):
    if not company:
        rows = frappe.get_all("Company", limit=1)
        if not rows:
            raise RuntimeError("No Company found in test DB")
        company = rows[0].name

    if not bank_account:
        rows = frappe.get_all("Bank Account", filters={"company": company}, limit=1)
        if not rows:
            raise RuntimeError(f"No Bank Account found for company {company}")
        bank_account = rows[0].name

    cb = frappe.new_doc("Cheque Book")
    cb.company       = company
    cb.bank_account  = bank_account
    cb.sequence_type = sequence_type
    cb.start_cheque_no = str(start)
    cb.end_cheque_no   = str(end)
    cb.issue_date      = frappe.utils.today()
    if digits_count:
        cb.digits_count = digits_count
    if prefix:
        cb.prefix = prefix
    if suffix:
        cb.suffix = suffix
    cb.flags.ignore_permissions = True
    cb.insert()
    return cb


# ------------------------------------------------------------------ #
#  Tests                                                               #
# ------------------------------------------------------------------ #

class TestChequeBook(FrappeTestCase):

    def test_leaf_count_on_numeric_range(self):
        cb = make_cheque_book(1, 10)
        cb.submit()
        leaves = frappe.get_all("Cheque Leaf", filters={"cheque_book": cb.name})
        self.assertEqual(len(leaves), 10)

    def test_leaf_sequence_values(self):
        cb = make_cheque_book(100, 105)
        cb.submit()
        nos = sorted(
            frappe.get_all("Cheque Leaf", filters={"cheque_book": cb.name}, pluck="cheque_no")
        )
        self.assertEqual(nos, ["100", "101", "102", "103", "104", "105"])

    def test_zero_padded_leaves(self):
        cb = make_cheque_book(1, 3, digits_count=6)
        cb.submit()
        nos = sorted(
            frappe.get_all("Cheque Leaf", filters={"cheque_book": cb.name}, pluck="cheque_no")
        )
        self.assertEqual(nos, ["000001", "000002", "000003"])

    def test_prefixed_leaves(self):
        cb = make_cheque_book(1, 3, prefix="CHK-", digits_count=3)
        cb.submit()
        nos = sorted(
            frappe.get_all("Cheque Leaf", filters={"cheque_book": cb.name}, pluck="cheque_no")
        )
        self.assertEqual(nos, ["CHK-001", "CHK-002", "CHK-003"])

    def test_status_becomes_active_on_submit(self):
        cb = make_cheque_book(200, 202)
        cb.submit()
        self.assertEqual(
            frappe.db.get_value("Cheque Book", cb.name, "status"), "Active"
        )

    def test_unused_counter_set_after_submit(self):
        cb = make_cheque_book(300, 304)
        cb.submit()
        self.assertEqual(
            frappe.db.get_value("Cheque Book", cb.name, "unused_leaves"), 5
        )

    def test_bank_account_company_mismatch_raises(self):
        companies = frappe.get_all("Company", limit=2)
        if len(companies) < 2:
            self.skipTest("Need ≥ 2 companies for mismatch test")
        co_a, co_b = companies[0].name, companies[1].name
        ba_b = frappe.get_all("Bank Account", filters={"company": co_b}, limit=1)
        if not ba_b:
            self.skipTest("No bank account for second company")

        cb = frappe.new_doc("Cheque Book")
        cb.company      = co_a
        cb.bank_account = ba_b[0].name
        cb.sequence_type   = "Numeric"
        cb.start_cheque_no = "1"
        cb.end_cheque_no   = "5"
        cb.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            cb.insert()

    def test_end_before_start_raises(self):
        company = frappe.get_all("Company", limit=1)[0].name
        ba = frappe.get_all("Bank Account", filters={"company": company}, limit=1)
        if not ba:
            self.skipTest("No bank account")
        cb = frappe.new_doc("Cheque Book")
        cb.company      = company
        cb.bank_account = ba[0].name
        cb.sequence_type   = "Numeric"
        cb.start_cheque_no = "100"
        cb.end_cheque_no   = "50"
        cb.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            cb.insert()

    def test_cancel_voids_unused_leaves(self):
        cb = make_cheque_book(400, 405)
        cb.submit()
        cb.cancel()
        cancelled = frappe.get_all(
            "Cheque Leaf",
            filters={"cheque_book": cb.name, "leaf_status": "Cancelled"},
        )
        self.assertEqual(len(cancelled), 6)
