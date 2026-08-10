# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import threading
import frappe
from frappe.tests.utils import FrappeTestCase

from cheque_tracker.cheque_tracker.doctype.cheque_book.test_cheque_book import make_cheque_book
from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import (
    mark_leaf_issued,
    release_leaf,
    reserve_leaf,
)


def _submitted_book(start, end, **kw):
    cb = make_cheque_book(start, end, **kw)
    cb.submit()
    return cb


class TestChequeLeaf(FrappeTestCase):

    def test_reserve_marks_status_reserved(self):
        cb = _submitted_book(6000, 6010)
        res = reserve_leaf(cb.name, "DUMMY-6001", frappe.session.user)
        status = frappe.db.get_value("Cheque Leaf", res["name"], "leaf_status")
        self.assertEqual(status, "Reserved")

    def test_reserve_links_cheque(self):
        cb = _submitted_book(6100, 6110)
        res = reserve_leaf(cb.name, "DUMMY-6101", frappe.session.user)
        link = frappe.db.get_value("Cheque Leaf", res["name"], "cheque")
        self.assertEqual(link, "DUMMY-6101")

    def test_issued_leaf_not_reserved_again(self):
        cb = _submitted_book(6200, 6210)
        r1 = reserve_leaf(cb.name, "DUMMY-6201", frappe.session.user)
        mark_leaf_issued(r1["name"])
        r2 = reserve_leaf(cb.name, "DUMMY-6202", frappe.session.user)
        self.assertNotEqual(r1["name"], r2["name"])

    def test_voided_leaf_not_reserved_again(self):
        cb = _submitted_book(6300, 6310)
        r1 = reserve_leaf(cb.name, "DUMMY-6301", frappe.session.user)
        release_leaf(r1["name"], status="Voided")
        r2 = reserve_leaf(cb.name, "DUMMY-6302", frappe.session.user)
        self.assertNotEqual(r1["name"], r2["name"])

    def test_exhausted_book_raises(self):
        cb = _submitted_book(6400, 6401)
        reserve_leaf(cb.name, "DUMMY-A", frappe.session.user)
        reserve_leaf(cb.name, "DUMMY-B", frappe.session.user)
        with self.assertRaises(frappe.ValidationError):
            reserve_leaf(cb.name, "DUMMY-C", frappe.session.user)

    def test_concurrent_reservation_gets_distinct_leaves(self):
        """
        Two threads must reserve two *different* leaves.
        The book has 21 leaves (6500-6520) so both should succeed.

        `frappe.local` is thread-local, so a spawned thread has no database
        connection of its own — the previous version of this test shared the
        parent's and every thread died with "object is not bound", which the
        assertion then reported as an app failure. Each thread now opens its own
        connection, which is also the only way `SELECT … FOR UPDATE` in
        reserve_leaf is exercised at all: row locks are meaningless within a
        single transaction.

        Because the workers need to *see* the book, it has to be committed
        first, which puts this data outside the test's rollback — hence the
        explicit cleanup.
        """
        site = frappe.local.site
        cb = _submitted_book(6500, 6520)
        frappe.db.commit()
        self.addCleanup(self._cleanup_book, cb.name)

        results = []
        errors  = []
        lock = threading.Lock()

        def do_reserve(cheque_name):
            frappe.init(site=site)
            frappe.connect()
            try:
                r = reserve_leaf(cb.name, cheque_name, "Administrator")
                frappe.db.commit()
                with lock:
                    results.append(r["name"])
            except Exception as exc:
                frappe.db.rollback()
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                frappe.destroy()

        t1 = threading.Thread(target=do_reserve, args=("CONC-6501",))
        t2 = threading.Thread(target=do_reserve, args=("CONC-6502",))
        t1.start(); t2.start()
        t1.join();  t2.join()

        # frappe.destroy() in the workers tore down this thread's context too.
        frappe.init(site=site)
        frappe.connect()

        self.assertEqual(errors, [], f"Unexpected errors: {errors}")
        self.assertEqual(len(results), 2)
        self.assertNotEqual(results[0], results[1],
                            "Both threads reserved the same leaf!")

    def _cleanup_book(self, book_name):
        """Remove the committed fixture the concurrency test had to create."""
        for leaf in frappe.get_all("Cheque Leaf", filters={"cheque_book": book_name}, pluck="name"):
            frappe.delete_doc("Cheque Leaf", leaf, force=True, ignore_permissions=True)
        if frappe.db.exists("Cheque Book", book_name):
            frappe.db.set_value("Cheque Book", book_name, "docstatus", 2, update_modified=False)
            frappe.delete_doc("Cheque Book", book_name, force=True, ignore_permissions=True)
        frappe.db.commit()

    def test_duplicate_leaf_in_book_raises(self):
        cb = _submitted_book(6600, 6600)  # one leaf: 6600
        leaf = frappe.new_doc("Cheque Leaf")
        leaf.cheque_book  = cb.name
        leaf.company      = cb.company
        leaf.bank_account = cb.bank_account
        leaf.cheque_no    = "6600"
        leaf.leaf_status  = "Unused"
        leaf.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            leaf.insert()
