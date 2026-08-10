"""§5.2 — Cheque Batch cascade, member validation and totals.

The cascade routes every member through `change_cheque_status`, the same
endpoint a single cheque uses. These tests check that the consequences of that
actually hold: one event per member, the same preconditions, and the same
Payment Entry settlement.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from cheque_tracker.tests import e2e
from cheque_tracker.tests.utils import get_test_env


class ChequeBatchTestCase(FrappeTestCase):
    def setUp(self):
        self.env = get_test_env()
        frappe.set_user("Administrator")
        frappe.message_log = []

    def make_batch(self, cheques, submit=False):
        batch = frappe.new_doc("Cheque Batch")
        batch.batch_date = today()
        batch.company = self.env["company"]
        batch.bank_account = self.env["bank_account"]
        for cheque in cheques:
            batch.append("items", {"cheque": cheque.name if hasattr(cheque, "name") else cheque})
        batch.flags.ignore_permissions = True
        batch.insert(ignore_permissions=True)
        if submit:
            batch.submit()
            batch.reload()
        return batch

    def events(self, cheque_name, event_type=None):
        filters = {"parent": cheque_name, "parenttype": "Cheque"}
        if event_type:
            filters["event_type"] = event_type
        return frappe.get_all("Cheque Event", filters=filters, pluck="event_type")


class TestBatchTotals(ChequeBatchTestCase):
    def test_totals_recomputed_from_members(self):
        a = e2e.make_incoming(amount=1000)
        b = e2e.make_incoming(amount=2500)
        batch = self.make_batch([a, b])

        self.assertEqual(batch.total_cheques, 2)
        self.assertEqual(flt(batch.total_amount), 3500.0)

    def test_totals_follow_an_item_change(self):
        a = e2e.make_incoming(amount=1000)
        b = e2e.make_incoming(amount=2500)
        batch = self.make_batch([a, b])

        batch.items = [row for row in batch.items if row.cheque == a.name]
        batch.flags.ignore_permissions = True
        batch.save(ignore_permissions=True)

        self.assertEqual(batch.total_cheques, 1)
        self.assertEqual(flt(batch.total_amount), 1000.0)

    def test_item_snapshot_is_filled_from_the_cheque(self):
        """The Deposit Slip prints these columns — a blank snapshot is a wrong
        document handed to a bank teller."""
        cheque = e2e.make_incoming(amount=7777)
        batch = self.make_batch([cheque])
        row = batch.items[0]

        self.assertEqual(row.cheque_no, cheque.cheque_no)
        self.assertEqual(flt(row.amount), 7777.0)
        self.assertEqual(row.party, cheque.party)
        self.assertEqual(str(row.due_date), str(cheque.due_date))


class TestBatchMemberValidation(ChequeBatchTestCase):
    def test_outgoing_cheque_rejected(self):
        outgoing = e2e.make_outgoing()
        with self.assertRaises(frappe.ValidationError):
            self.make_batch([outgoing])

    def test_cheque_already_deposited_rejected(self):
        cheque = e2e.make_incoming()
        e2e.act(cheque, "Deposit")
        with self.assertRaises(frappe.ValidationError):
            self.make_batch([cheque])

    def test_cash_clearance_cheque_rejected(self):
        cheque = e2e.make_incoming(
            clearance_type="Cash", cash_account=self.env["cash_account"]
        )
        with self.assertRaises(frappe.ValidationError):
            self.make_batch([cheque])

    def test_cheque_in_another_open_batch_rejected(self):
        cheque = e2e.make_incoming()
        self.make_batch([cheque])
        with self.assertRaises(frappe.ValidationError):
            self.make_batch([cheque])

    def test_duplicate_row_rejected(self):
        cheque = e2e.make_incoming()
        with self.assertRaises(frappe.ValidationError):
            self.make_batch([cheque, cheque])

    def test_draft_cheque_rejected(self):
        cheque = e2e.make_incoming(submit=False)
        with self.assertRaises(frappe.ValidationError):
            self.make_batch([cheque])


class TestBatchCascade(ChequeBatchTestCase):
    def test_submit_cascades_deposit_with_one_event_each(self):
        a = e2e.make_incoming()
        b = e2e.make_incoming()
        batch = self.make_batch([a, b], submit=True)

        self.assertEqual(batch.status, "Deposited")
        for cheque in (a, b):
            self.assertEqual(frappe.db.get_value("Cheque", cheque.name, "status"), "Deposited")
            deposited = self.events(cheque.name, "Deposited")
            self.assertEqual(
                len(deposited), 1, f"{cheque.name} logged {len(deposited)} Deposited events"
            )

    def test_batch_supplies_its_bank_account_to_members(self):
        cheque = e2e.make_incoming(bank_account=None)
        self.assertFalse(cheque.bank_account)

        self.make_batch([cheque], submit=True)

        self.assertEqual(
            frappe.db.get_value("Cheque", cheque.name, "bank_account"),
            self.env["bank_account"],
        )
        self.assertEqual(frappe.db.get_value("Cheque", cheque.name, "status"), "Deposited")

    def test_clear_batch_cascades_and_settles(self):
        a = e2e.make_incoming()
        b = e2e.make_incoming()
        batch = self.make_batch([a, b], submit=True)

        batch.clear_batch()
        batch.reload()

        self.assertEqual(batch.status, "Cleared")
        for cheque in (a, b):
            self.assertEqual(frappe.db.get_value("Cheque", cheque.name, "status"), "Cleared")
            self.assertTrue(
                frappe.db.get_value("Cheque", cheque.name, "cleared_date"),
                f"{cheque.name} cleared with no cleared_date",
            )
            self.assertEqual(len(self.events(cheque.name, "Cleared")), 1)

    def test_clear_batch_posts_no_journal_entry(self):
        """D2 — the tracker posts no GL; a batch is no exception."""
        cheque = e2e.make_incoming()
        batch = self.make_batch([cheque], submit=True)

        before = frappe.db.count("Journal Entry")
        batch.clear_batch()
        after = frappe.db.count("Journal Entry")

        self.assertEqual(before, after, "batch clearance posted a Journal Entry")

    def test_bounce_batch_requires_a_reason(self):
        cheque = e2e.make_incoming()
        batch = self.make_batch([cheque], submit=True)

        with self.assertRaises(frappe.ValidationError):
            batch.bounce_batch(bounce_reason=None)

    def test_bounce_batch_cascades_and_stamps_the_reason(self):
        a = e2e.make_incoming()
        b = e2e.make_incoming()
        batch = self.make_batch([a, b], submit=True)

        batch.bounce_batch(bounce_reason="Insufficient Funds")
        batch.reload()

        self.assertEqual(batch.status, "Bounced")
        for cheque in (a, b):
            self.assertEqual(frappe.db.get_value("Cheque", cheque.name, "status"), "Bounced")
            self.assertEqual(
                frappe.db.get_value("Cheque", cheque.name, "bounce_reason"), "Insufficient Funds"
            )
            self.assertEqual(len(self.events(cheque.name, "Bounced")), 1)

    def test_clear_requires_a_deposited_batch(self):
        cheque = e2e.make_incoming()
        batch = self.make_batch([cheque])  # still Draft
        with self.assertRaises(frappe.ValidationError):
            batch.clear_batch()

    def test_cascade_is_idempotent_for_members_already_there(self):
        """Re-running a partially applied batch must not double-log."""
        cheque = e2e.make_incoming()
        batch = self.make_batch([cheque], submit=True)

        batch._cascade("Deposited", "second pass")

        self.assertEqual(len(self.events(cheque.name, "Deposited")), 1)
