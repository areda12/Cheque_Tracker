# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe.tests.utils import FrappeTestCase

from cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book import (
    get_book_counters,
)
from cheque_tracker.tests.utils import get_test_env


# ------------------------------------------------------------------ #
#  Shared factory                                                      #
# ------------------------------------------------------------------ #

def make_cheque_book(start=1, end=10, company=None, bank_account=None,
                     sequence_type="Numeric", digits_count=0, prefix="", suffix=""):
    # Resolve through the pinned test environment rather than
    # `get_all(..., limit=1)`, which is ordered by `modified desc` and so
    # picked a different company on every run.
    env = get_test_env()
    if not company:
        company = env["company"]

    if not bank_account:
        if company == env["company"]:
            bank_account = env["bank_account"]
        else:
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
        # The pinned env guarantees a second company with its own bank account,
        # so this no longer skips itself when the site happens to be sparse.
        env = get_test_env()
        co_a = env["company"]
        ba_b = env["secondary_bank_account"]
        self.assertNotEqual(
            frappe.db.get_value("Bank Account", ba_b, "company"),
            co_a,
            "secondary bank account must belong to a different company",
        )

        cb = frappe.new_doc("Cheque Book")
        cb.company      = co_a
        cb.bank_account = ba_b
        cb.sequence_type   = "Numeric"
        cb.start_cheque_no = "1"
        cb.end_cheque_no   = "5"
        cb.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            cb.insert()

    def test_end_before_start_raises(self):
        env = get_test_env()
        cb = frappe.new_doc("Cheque Book")
        cb.company      = env["company"]
        cb.bank_account = env["bank_account"]
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

    # ------------------------------------------------------------------ #
    #  C2 regression — write-perm check on get_book_counters              #
    # ------------------------------------------------------------------ #

    def _ensure_role_only_user(self, email_prefix, first_name, role):
        """
        Idempotently provision a test user with ONLY the given role
        (Treasury / Accounts / Cheque Auditor / System Manager /
        Administrator stripped if previously present). Local helper
        — parallel to the F1/F2 helpers in test_cheque.py; duplicated
        here so test_cheque_book.py remains self-contained.
        """
        email = f"{email_prefix}@cheque-tracker.test"
        if frappe.db.exists("User", email):
            user = frappe.get_doc("User", email)
        else:
            user = frappe.new_doc("User")
            user.email = email
            user.first_name = first_name
            user.last_name = "Test"
            user.enabled = 1
            user.send_welcome_email = 0
            user.flags.ignore_permissions = True
            user.insert()

        unwanted = {
            "Treasury User", "Accounts User", "Cheque Auditor",
            "System Manager", "Administrator",
        } - {role}
        user.roles = [r for r in user.roles if r.role not in unwanted]
        if not any(r.role == role for r in user.roles):
            user.append("roles", {"role": role})
        user.flags.ignore_permissions = True
        user.save()
        return email

    def test_c2_get_book_counters_allowed_for_treasury_user(self):
        """C2: Treasury User has write perm on Cheque Book, so the
        endpoint runs and returns the counters dict."""
        if not frappe.db.exists("Role", "Treasury User"):
            self.skipTest("Treasury User role not present on test site.")

        treasury = self._ensure_role_only_user(
            "test_c2_treasury_user", "Test C2 Treasury", "Treasury User",
        )
        cb = make_cheque_book(8100, 8110)
        cb.submit()

        original_user = frappe.session.user
        try:
            frappe.set_user(treasury)
            result = get_book_counters(cb.name)
        finally:
            frappe.set_user(original_user)

        self.assertIsInstance(result, dict)
        for k in ("unused_leaves", "issued_leaves", "voided_leaves", "cancelled_leaves"):
            self.assertIn(k, result)

    def test_c2_get_book_counters_blocked_for_read_only_role(self):
        """
        C2: get_book_counters writes via db_set inside _refresh_counters.
        Users with only read permission must be blocked from triggering
        the write side-effect.
        """
        if not frappe.db.exists("Role", "Cheque Auditor"):
            self.skipTest("Cheque Auditor role not present on test site.")

        auditor = self._ensure_role_only_user(
            "test_c2_cheque_auditor", "Test C2 Auditor", "Cheque Auditor",
        )
        cb = make_cheque_book(8000, 8010)
        cb.submit()

        original_user = frappe.session.user
        try:
            frappe.set_user(auditor)
            with self.assertRaises(frappe.PermissionError):
                get_book_counters(cb.name)
        finally:
            frappe.set_user(original_user)


# ====================================================================== #
#  v1.1.6 — §3.2.8: stored counters track every leaf state change        #
# ====================================================================== #

class TestChequeBookCounters(FrappeTestCase):
    """The counters used to be recomputed only when the Cheque Book form was
    opened (get_book_counters), so the list view — and every other reader of the
    stored fields — showed values that were stale the moment a leaf moved.

    Every assertion below reads the STORED column directly, never
    get_book_counters, otherwise the recompute-on-read would mask the bug.
    """

    def _stored(self, book):
        return frappe.db.get_value(
            "Cheque Book",
            book,
            ["unused_leaves", "issued_leaves", "voided_leaves", "cancelled_leaves", "status"],
            as_dict=True,
        )

    def test_counters_after_submit(self):
        cb = make_cheque_book(9600, 9609)
        cb.submit()
        stored = self._stored(cb.name)
        self.assertEqual(stored.unused_leaves, 10)
        self.assertEqual(stored.issued_leaves, 0)
        self.assertEqual(stored.voided_leaves, 0)

    def test_counters_after_reserve(self):
        from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import reserve_leaf

        cb = make_cheque_book(9700, 9709)
        cb.submit()
        reserve_leaf(cb.name, "DUMMY-9700", frappe.session.user)

        stored = self._stored(cb.name)
        self.assertEqual(stored.unused_leaves, 9, "reserve did not refresh the stored counter")
        self.assertEqual(stored.issued_leaves, 0)

    def test_counters_after_issue(self):
        from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import (
            mark_leaf_issued,
            reserve_leaf,
        )

        cb = make_cheque_book(9800, 9809)
        cb.submit()
        leaf = reserve_leaf(cb.name, "DUMMY-9800", frappe.session.user)
        mark_leaf_issued(leaf["name"])

        stored = self._stored(cb.name)
        self.assertEqual(stored.unused_leaves, 9)
        self.assertEqual(stored.issued_leaves, 1, "issue did not refresh the stored counter")

    def test_counters_after_void(self):
        from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import (
            release_leaf,
            reserve_leaf,
        )

        cb = make_cheque_book(9900, 9909)
        cb.submit()
        leaf = reserve_leaf(cb.name, "DUMMY-9900", frappe.session.user)
        release_leaf(leaf["name"], status="Voided", void_reason="test")

        stored = self._stored(cb.name)
        self.assertEqual(stored.unused_leaves, 9)
        self.assertEqual(stored.voided_leaves, 1, "void did not refresh the stored counter")

    def test_counters_after_manual_leaf_edit(self):
        """A leaf edited through the desk form goes through the ORM, so the
        ChequeLeaf.on_update hook is what keeps the book honest."""
        cb = make_cheque_book(10000, 10009)
        cb.submit()
        leaf_name = frappe.get_all(
            "Cheque Leaf", filters={"cheque_book": cb.name}, limit=1, pluck="name"
        )[0]

        leaf = frappe.get_doc("Cheque Leaf", leaf_name)
        leaf.leaf_status = "Voided"
        leaf.void_reason = "manual correction"
        leaf.flags.ignore_permissions = True
        leaf.save()

        stored = self._stored(cb.name)
        self.assertEqual(stored.unused_leaves, 9)
        self.assertEqual(stored.voided_leaves, 1, "manual edit did not refresh the stored counter")

    def test_counters_after_book_cancel(self):
        cb = make_cheque_book(10100, 10104)
        cb.submit()
        cb.cancel()

        stored = self._stored(cb.name)
        self.assertEqual(stored.unused_leaves, 0)
        self.assertEqual(stored.cancelled_leaves, 5)
        self.assertEqual(stored.status, "Cancelled")

    def test_book_auto_exhausts_when_last_leaf_consumed(self):
        from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import (
            mark_leaf_issued,
            reserve_leaf,
        )

        cb = make_cheque_book(10200, 10201)
        cb.submit()
        for i in range(2):
            leaf = reserve_leaf(cb.name, f"DUMMY-1020{i}", frappe.session.user)
            mark_leaf_issued(leaf["name"])

        stored = self._stored(cb.name)
        self.assertEqual(stored.unused_leaves, 0)
        self.assertEqual(stored.issued_leaves, 2)
        self.assertEqual(stored.status, "Exhausted")
