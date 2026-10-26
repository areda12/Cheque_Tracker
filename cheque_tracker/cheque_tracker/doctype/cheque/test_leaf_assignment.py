# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""v1.3.1 — leaf assignment and back-dating.

1. The leaf a clerk names (by Cheque Leaf or Cheque No) is the one used; the
   next Unused leaf is taken only when both are blank; a used leaf is refused
   and cheque_no is never silently rewritten.
2. A leaf's issued_on is the cheque's issue_date, not the day it was keyed in.
3. A returned outgoing cheque cancels its leaf.
4. A System Manager can move a submitted cheque to another Unused leaf.
5. An Effective Date back-dates the transition it is saved ahead of.
6. The one-time CHQ-2026-00004 correction, and that re-running it is a no-op.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime, today

from cheque_tracker.cheque_tracker.doctype.cheque.cheque import (
    change_cheque_status,
    reassign_cheque_leaf,
)
from cheque_tracker.cheque_tracker.doctype.cheque_book.test_cheque_book import make_cheque_book
from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import release_leaf
from cheque_tracker.patches.v3_4 import fix_chq_2026_00004_leaf as chq4_patch
from cheque_tracker.tests import e2e
from cheque_tracker.tests.utils import get_test_env

# Back-dated well inside the past so "not in the future" never interferes.
ISSUE_DATE = add_days(today(), -8)


def _book(size=10):
    """A fresh submitted book with its own number range.

    The 9,xxx,xxx range keeps clear of the 600000-899999 range e2e.make_outgoing
    draws from, so the §4.5.4 duplicate guard never fires across tests.
    """
    env = get_test_env()
    start = 9_000_000 + int(frappe.generate_hash(length=8), 16) % 900_000
    book = make_cheque_book(start, start + size - 1, company=env["company"], bank_account=env["bank_account"])
    book.submit()
    return book


def _leaves(book):
    return frappe.get_all(
        "Cheque Leaf",
        filters={"cheque_book": book.name},
        fields=["name", "cheque_no", "leaf_status", "cheque", "issued_on", "reserved_by", "void_reason"],
        order_by="cheque_no asc",
    )


def _leaf(name):
    return frappe.db.get_value(
        "Cheque Leaf",
        name,
        ["name", "cheque_no", "leaf_status", "cheque", "issued_on", "reserved_by", "reserved_on", "void_reason"],
        as_dict=True,
    )


def _draft(book, **values):
    env = get_test_env()
    doc = frappe.new_doc("Cheque")
    doc.update(
        {
            "cheque_type": "Outgoing",
            "company": env["company"],
            "party_type": "Supplier",
            "party": env["supplier"],
            "amount": 1000,
            "currency": env["currency"],
            "issue_date": ISSUE_DATE,
            "due_date": add_days(today(), 20),
            "cheque_book": book.name,
            "bank_account": env["bank_account"],
        }
    )
    doc.update(values)
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc


def _issued(book, **values):
    doc = _draft(book, **values)
    doc.submit()
    doc.reload()
    return doc


def _events(cheque_name, event_type=None):
    filters = {"parent": cheque_name, "parenttype": "Cheque"}
    if event_type:
        filters["event_type"] = event_type
    return frappe.get_all(
        "Cheque Event",
        filters=filters,
        fields=["event_type", "event_datetime", "notes"],
        order_by="idx asc",
    )


# ====================================================================== #
#  1 + 2 — the chosen leaf, and its issued_on                            #
# ====================================================================== #


class TestChosenLeaf(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.book = _book()
        self.leaves = _leaves(self.book)

    def test_issue_with_explicit_leaf_uses_that_leaf(self):
        chosen = self.leaves[2]
        chq = _issued(self.book, cheque_leaf=chosen.name)

        self.assertEqual(chq.cheque_leaf, chosen.name)
        self.assertEqual(chq.cheque_no, chosen.cheque_no)
        leaf = _leaf(chosen.name)
        self.assertEqual(leaf.leaf_status, "Issued")
        self.assertEqual(leaf.cheque, chq.name)
        self.assertEqual(
            _leaf(self.leaves[0].name).leaf_status, "Unused",
            "the first Unused leaf must not be consumed when another was chosen",
        )

    def test_issue_with_explicit_cheque_no_uses_that_leaf(self):
        # The CHQ-2026-00004 shape: a number is given, the next Unused leaf is a
        # different one.
        chosen = self.leaves[2]
        chq = _issued(self.book, cheque_no=chosen.cheque_no)

        self.assertEqual(chq.cheque_no, chosen.cheque_no, "cheque_no was overwritten")
        self.assertEqual(chq.cheque_leaf, chosen.name)
        self.assertEqual(_leaf(chosen.name).leaf_status, "Issued")
        self.assertEqual(_leaf(self.leaves[0].name).leaf_status, "Unused")

    def test_zero_padded_cheque_no_resolves_to_its_leaf(self):
        # Banks print 000528201382 for leaf 528201382.
        chosen = self.leaves[3]
        chq = _draft(self.book, cheque_no="000" + chosen.cheque_no)

        self.assertEqual(chq.cheque_leaf, chosen.name)
        self.assertEqual(chq.cheque_no, chosen.cheque_no)

    def test_issue_with_blank_leaf_takes_the_next_unused(self):
        first = _issued(self.book)
        second = _issued(self.book)

        self.assertEqual(first.cheque_leaf, self.leaves[0].name)
        self.assertEqual(first.cheque_no, self.leaves[0].cheque_no)
        self.assertEqual(second.cheque_leaf, self.leaves[1].name)

    def test_blank_takes_the_next_unused_after_a_chosen_one(self):
        _issued(self.book, cheque_leaf=self.leaves[0].name)
        auto = _draft(self.book)
        self.assertEqual(auto.cheque_leaf, self.leaves[1].name)

    def test_issue_with_issued_leaf_fails(self):
        taken = self.leaves[1]
        owner = _issued(self.book, cheque_leaf=taken.name)

        with self.assertRaises(frappe.ValidationError) as ctx:
            _draft(self.book, cheque_leaf=taken.name)
        self.assertIn(owner.name, str(ctx.exception), "the error must say who holds the leaf")

        with self.assertRaises(frappe.ValidationError):
            _draft(self.book, cheque_no=taken.cheque_no)

        self.assertEqual(_leaf(taken.name).cheque, owner.name, "the owner's leaf must be untouched")
        self.assertEqual(
            _leaf(self.leaves[0].name).leaf_status, "Unused",
            "a refused leaf must not fall back to auto-picking another",
        )

    def test_issue_with_voided_or_cancelled_leaf_fails(self):
        release_leaf(self.leaves[2].name, status="Voided", void_reason="spoilt")
        release_leaf(self.leaves[3].name, status="Cancelled", void_reason="torn")

        for leaf in (self.leaves[2], self.leaves[3]):
            with self.assertRaises(frappe.ValidationError):
                _draft(self.book, cheque_leaf=leaf.name)
            with self.assertRaises(frappe.ValidationError):
                _draft(self.book, cheque_no=leaf.cheque_no)

    def test_leaf_reserved_by_another_draft_fails(self):
        holder = _draft(self.book, cheque_leaf=self.leaves[4].name)
        with self.assertRaises(frappe.ValidationError) as ctx:
            _draft(self.book, cheque_leaf=self.leaves[4].name)
        self.assertIn(holder.name, str(ctx.exception))

    def test_leaf_of_another_book_fails(self):
        other = _book(3)
        foreign = _leaves(other)[0]
        with self.assertRaises(frappe.ValidationError):
            _draft(self.book, cheque_leaf=foreign.name)
        self.assertEqual(_leaf(foreign.name).leaf_status, "Unused")

    def test_unknown_cheque_no_fails_rather_than_being_overwritten(self):
        with self.assertRaises(frappe.ValidationError):
            _draft(self.book, cheque_no="NOT-A-LEAF-1")
        self.assertFalse(
            frappe.get_all("Cheque Leaf", filters={"cheque_book": self.book.name, "leaf_status": "Reserved"}),
            "nothing may be reserved for a refused number",
        )

    def test_leaf_and_number_that_disagree_on_a_new_cheque_fail(self):
        with self.assertRaises(frappe.ValidationError):
            _draft(self.book, cheque_leaf=self.leaves[1].name, cheque_no=self.leaves[2].cheque_no)

    def test_retyping_the_number_on_a_draft_moves_the_reservation(self):
        chq = _draft(self.book)
        self.assertEqual(chq.cheque_leaf, self.leaves[0].name)

        chq.cheque_no = self.leaves[5].cheque_no
        chq.save(ignore_permissions=True)

        self.assertEqual(chq.cheque_leaf, self.leaves[5].name)
        self.assertEqual(_leaf(self.leaves[5].name).leaf_status, "Reserved")
        freed = _leaf(self.leaves[0].name)
        self.assertEqual(freed.leaf_status, "Unused")
        self.assertFalse(freed.cheque, "the abandoned leaf must be unlinked")

    def test_picking_another_leaf_on_a_draft_carries_its_number(self):
        chq = _draft(self.book)
        chq.cheque_leaf = self.leaves[6].name
        chq.save(ignore_permissions=True)

        self.assertEqual(chq.cheque_no, self.leaves[6].cheque_no)
        self.assertEqual(_leaf(self.leaves[0].name).leaf_status, "Unused")

    def test_manual_number_that_is_no_leaf_still_raises_on_a_draft(self):
        chq = _draft(self.book)
        chq.cheque_no = "MANUAL-OVERRIDE-999"
        with self.assertRaises(frappe.ValidationError):
            chq.save(ignore_permissions=True)

    def test_insert_straight_at_docstatus_1_respects_the_chosen_number(self):
        # An API client creating and issuing in one call never runs before_save.
        chosen = self.leaves[3]
        chq = _draft(self.book, cheque_no=chosen.cheque_no, docstatus=1)
        chq.reload()

        self.assertEqual(chq.status, "Issued")
        self.assertEqual(chq.cheque_leaf, chosen.name)
        self.assertEqual(_leaf(chosen.name).leaf_status, "Issued")

    def test_issued_on_is_the_issue_date_not_today(self):
        chq = _issued(self.book)
        self.assertEqual(getdate(_leaf(chq.cheque_leaf).issued_on), getdate(ISSUE_DATE))

    def test_issued_on_falls_back_to_today_without_an_issue_date(self):
        chq = _issued(self.book, issue_date=None)
        self.assertEqual(getdate(_leaf(chq.cheque_leaf).issued_on), getdate(today()))

    def test_switching_a_draft_to_incoming_lets_go_of_its_leaf(self):
        chq = _draft(self.book)
        leaf = chq.cheque_leaf
        chq.cheque_type = "Incoming"
        chq.cheque_no = "EXT-" + frappe.generate_hash(length=6)
        chq.drawee_bank = get_test_env()["drawee_bank"]
        chq.save(ignore_permissions=True)

        self.assertFalse(chq.cheque_leaf)
        self.assertFalse(chq.cheque_book)
        self.assertEqual(_leaf(leaf).leaf_status, "Unused")


# ====================================================================== #
#  3 — Returned outgoing cheque → leaf Cancelled                         #
# ====================================================================== #


class TestReturnedLeaf(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    def test_workflow_return_cancels_the_leaf(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        e2e.act(chq, "Hand Over")
        e2e.act(chq, "Return")

        self.assertEqual(chq.status, "Returned")
        leaf = _leaf(chq.cheque_leaf)
        self.assertEqual(leaf.leaf_status, "Cancelled")
        self.assertEqual(leaf.cheque, chq.name, "the cancelled leaf stays linked to its cheque")
        self.assertIn(chq.name, leaf.void_reason or "")
        self.assertEqual(
            frappe.db.get_value("Cheque Book", chq.cheque_book, "cancelled_leaves"), 1,
            "the book's counters must see the cancellation",
        )
        notes = [e.notes for e in _events(chq.name, "Note")]
        self.assertTrue(any("cancelled" in (n or "") for n in notes), notes)

    def test_return_straight_from_issued_cancels_the_leaf(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        e2e.act(chq, "Return")
        self.assertEqual(_leaf(chq.cheque_leaf).leaf_status, "Cancelled")

    def test_ui_return_cancels_the_leaf(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        change_cheque_status(chq.name, "Handed Over")
        change_cheque_status(chq.name, "Returned")
        chq.reload()
        self.assertEqual(_leaf(chq.cheque_leaf).leaf_status, "Cancelled")

    def test_incoming_return_has_no_leaf_to_touch(self):
        chq = e2e.make_incoming(with_payment_entry=False)
        e2e.act(chq, "Return")
        self.assertEqual(chq.status, "Returned")
        self.assertFalse(chq.cheque_leaf)

    def test_bounce_does_not_cancel_the_leaf(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        e2e.act(chq, "Hand Over")
        e2e.set_fields(chq, bounce_reason="Account Closed")
        e2e.act(chq, "Bounce")
        self.assertEqual(_leaf(chq.cheque_leaf).leaf_status, "Issued")


# ====================================================================== #
#  4 — Reassign Leaf                                                     #
# ====================================================================== #


class TestReassignLeaf(FrappeTestCase):
    REASON = "Recorded against 1 instead of the leaf actually written."

    def setUp(self):
        frappe.set_user("Administrator")
        self.book = _book()
        self.leaves = _leaves(self.book)

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_reassign_moves_the_cheque_and_frees_the_old_leaf(self):
        chq = _issued(self.book)
        old = self.leaves[0]
        new = self.leaves[2]
        self.assertEqual(chq.cheque_leaf, old.name)

        result = reassign_cheque_leaf(chq.name, reason=self.REASON, new_leaf=new.name)
        chq.reload()

        self.assertEqual(result["cheque_leaf"], new.name)
        self.assertEqual(chq.cheque_leaf, new.name)
        self.assertEqual(chq.cheque_no, new.cheque_no)

        freed = _leaf(old.name)
        self.assertEqual(freed.leaf_status, "Unused")
        self.assertFalse(freed.cheque)
        self.assertFalse(freed.issued_on)
        self.assertFalse(freed.reserved_by)

        taken = _leaf(new.name)
        self.assertEqual(taken.leaf_status, "Issued")
        self.assertEqual(taken.cheque, chq.name)
        self.assertEqual(getdate(taken.issued_on), getdate(ISSUE_DATE))

        note = next(
            (e.notes for e in _events(chq.name, "Note") if "reassigned" in (e.notes or "")), None
        )
        self.assertIsNotNone(note, "the reassignment must be on the timeline")
        self.assertIn(self.REASON, note)
        self.assertIn(frappe.session.user, note)
        self.assertIn(old.cheque_no, note)
        self.assertIn(new.cheque_no, note)

    def test_reassign_by_cheque_number(self):
        chq = _issued(self.book)
        reassign_cheque_leaf(chq.name, reason=self.REASON, cheque_no=self.leaves[4].cheque_no)
        chq.reload()
        self.assertEqual(chq.cheque_leaf, self.leaves[4].name)

    def test_reassign_requires_a_reason(self):
        chq = _issued(self.book)
        with self.assertRaises(frappe.ValidationError):
            reassign_cheque_leaf(chq.name, reason="   ", new_leaf=self.leaves[2].name)
        self.assertEqual(frappe.db.get_value("Cheque", chq.name, "cheque_leaf"), self.leaves[0].name)

    def test_reassign_is_refused_to_a_non_system_manager(self):
        chq = _issued(self.book)
        frappe.set_user(get_test_env()["treasury_user"])
        try:
            with self.assertRaises(frappe.PermissionError):
                reassign_cheque_leaf(chq.name, reason=self.REASON, new_leaf=self.leaves[2].name)
        finally:
            frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value("Cheque", chq.name, "cheque_leaf"), self.leaves[0].name)
        self.assertEqual(_leaf(self.leaves[2].name).leaf_status, "Unused")

    def test_reassign_refuses_a_used_leaf(self):
        chq = _issued(self.book)
        other = _issued(self.book)
        with self.assertRaises(frappe.ValidationError):
            reassign_cheque_leaf(chq.name, reason=self.REASON, new_leaf=other.cheque_leaf)
        self.assertEqual(_leaf(other.cheque_leaf).cheque, other.name)

    def test_reassign_refuses_a_leaf_of_another_book(self):
        chq = _issued(self.book)
        foreign = _leaves(_book(3))[0]
        with self.assertRaises(frappe.ValidationError):
            reassign_cheque_leaf(chq.name, reason=self.REASON, new_leaf=foreign.name)

    def test_reassign_refuses_a_draft(self):
        chq = _draft(self.book)
        with self.assertRaises(frappe.ValidationError):
            reassign_cheque_leaf(chq.name, reason=self.REASON, new_leaf=self.leaves[3].name)

    def test_reassigning_a_returned_cheque_keeps_the_new_leaf_cancelled(self):
        chq = _issued(self.book)
        e2e.act(chq, "Hand Over")
        e2e.act(chq, "Return")
        self.assertEqual(_leaf(self.leaves[0].name).leaf_status, "Cancelled")

        reassign_cheque_leaf(chq.name, reason=self.REASON, new_leaf=self.leaves[2].name)

        self.assertEqual(_leaf(self.leaves[2].name).leaf_status, "Cancelled")
        self.assertEqual(_leaf(self.leaves[2].name).cheque, chq.name)
        self.assertEqual(_leaf(self.leaves[0].name).leaf_status, "Unused")


# ====================================================================== #
#  5 — Effective Date                                                    #
# ====================================================================== #


class TestEffectiveDate(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.effective = get_datetime(add_to_date(now_datetime(), days=-5)).replace(
            hour=10, minute=30, second=0, microsecond=0
        )

    def _event(self, cheque_name, event_type):
        rows = _events(cheque_name, event_type)
        self.assertTrue(rows, f"no {event_type} event on {cheque_name}")
        return rows[-1]

    def test_back_recorded_hand_over_carries_the_effective_date(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        e2e.set_fields(chq, effective_date=self.effective)
        e2e.act(chq, "Hand Over")

        event = self._event(chq.name, "Handed Over")
        self.assertEqual(get_datetime(event.event_datetime), self.effective)
        self.assertIn("recorded", event.notes, "a back-dated event must say when it was keyed in")
        self.assertFalse(chq.effective_date, "the Effective Date is consumed by the transition")
        custody = [e for e in _events(chq.name, "Note") if "Custody left" in (e.notes or "")]
        self.assertTrue(custody)
        self.assertEqual(get_datetime(custody[-1].event_datetime), self.effective)

    def test_the_next_transition_defaults_to_now(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        e2e.set_fields(chq, effective_date=self.effective)
        e2e.act(chq, "Hand Over")

        before = now_datetime().replace(microsecond=0)
        e2e.act(chq, "Return")
        returned = self._event(chq.name, "Returned")
        self.assertGreaterEqual(get_datetime(returned.event_datetime), before)

    def test_back_recorded_return_and_deposit(self):
        out = e2e.make_outgoing(with_payment_entry=False)
        e2e.set_fields(out, effective_date=self.effective)
        e2e.act(out, "Return")
        self.assertEqual(get_datetime(self._event(out.name, "Returned").event_datetime), self.effective)
        self.assertIn(
            frappe.utils.format_date(self.effective.date()), _leaf(out.cheque_leaf).void_reason or ""
        )

        inc = e2e.make_incoming(with_payment_entry=False)
        e2e.set_fields(inc, effective_date=self.effective)
        e2e.act(inc, "Deposit")
        self.assertEqual(get_datetime(self._event(inc.name, "Deposited").event_datetime), self.effective)

    def test_effective_date_through_change_cheque_status(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        change_cheque_status(chq.name, "Handed Over", effective_date=str(self.effective))
        self.assertEqual(
            get_datetime(self._event(chq.name, "Handed Over").event_datetime), self.effective
        )

    def test_saving_an_effective_date_alone_records_nothing(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        count = len(_events(chq.name))
        e2e.set_fields(chq, effective_date=self.effective)

        self.assertEqual(len(_events(chq.name)), count)
        self.assertEqual(get_datetime(chq.effective_date), self.effective, "it waits for the next action")

    def test_future_effective_date_is_refused(self):
        chq = e2e.make_outgoing(with_payment_entry=False)
        with self.assertRaises(frappe.ValidationError):
            e2e.set_fields(chq, effective_date=add_to_date(now_datetime(), days=1))
        with self.assertRaises(frappe.ValidationError):
            change_cheque_status(
                chq.name, "Handed Over", effective_date=str(add_to_date(now_datetime(), days=1))
            )
        self.assertEqual(frappe.db.get_value("Cheque", chq.name, "status"), "Issued")

    def test_back_recorded_issue(self):
        chq = _draft(_book(3), effective_date=self.effective)
        chq.submit()
        chq.reload()
        self.assertEqual(get_datetime(self._event(chq.name, "Issued").event_datetime), self.effective)
        self.assertFalse(chq.effective_date)

    def test_clear_takes_its_date_from_the_effective_date(self):
        chq = e2e.make_incoming()
        e2e.act(chq, "Deposit")
        e2e.set_fields(chq, effective_date=self.effective)
        e2e.act(chq, "Clear")

        self.assertEqual(getdate(chq.cleared_date), self.effective.date())
        self.assertEqual(get_datetime(self._event(chq.name, "Cleared").event_datetime), self.effective)


# ====================================================================== #
#  6 — the CHQ-2026-00004 correction                                     #
# ====================================================================== #


class TestChq00004Patch(FrappeTestCase):
    """The patch's correction run against a faithful replica of the incident:
    a returned outgoing cheque written on the third leaf but recorded — and
    stamped Issued, today — on the first."""

    def setUp(self):
        frappe.set_user("Administrator")
        self.book = _book(6)
        self.leaves = _leaves(self.book)
        self.wrong, self.right = self.leaves[0], self.leaves[2]

        chq = _issued(self.book)  # auto-picks the first leaf, as v1.3.1 did
        e2e.act(chq, "Hand Over")
        e2e.act(chq, "Return")
        # The incident predates "Returned → Cancelled": the wrong leaf is still
        # Issued, stamped with the day it was keyed in.
        frappe.db.set_value(
            "Cheque Leaf", self.wrong.name, {"leaf_status": "Issued", "issued_on": today(), "void_reason": None}
        )
        self.cheque = chq.name

    def _correction(self, **overrides):
        values = {
            "cheque": self.cheque,
            "cheque_book": self.book.name,
            "wrong_leaf": self.wrong.name,
            "wrong_no": self.wrong.cheque_no,
            "right_leaf": self.right.name,
            "right_no": self.right.cheque_no,
            "issued_on": ISSUE_DATE,
        }
        values.update(overrides)
        return values

    def _snapshot(self):
        return (
            frappe.db.get_value("Cheque", self.cheque, ["cheque_leaf", "cheque_no", "modified"], as_dict=True),
            _leaf(self.wrong.name),
            frappe.db.get_value("Cheque Leaf", self.wrong.name, "modified"),
            _leaf(self.right.name),
            frappe.db.get_value("Cheque Leaf", self.right.name, "modified"),
            len(_events(self.cheque)),
        )

    def test_corrects_the_incident(self):
        unused_before = frappe.db.get_value("Cheque Book", self.book.name, "unused_leaves")

        self.assertEqual(chq4_patch.correct_leaf(**self._correction()), "corrected")

        cheque = frappe.db.get_value("Cheque", self.cheque, ["cheque_leaf", "cheque_no"], as_dict=True)
        self.assertEqual(cheque.cheque_leaf, self.right.name)
        self.assertEqual(cheque.cheque_no, self.right.cheque_no)

        right = _leaf(self.right.name)
        self.assertEqual(right.leaf_status, "Cancelled")
        self.assertEqual(right.cheque, self.cheque)
        self.assertEqual(getdate(right.issued_on), getdate(ISSUE_DATE))

        wrong = _leaf(self.wrong.name)
        self.assertEqual(wrong.leaf_status, "Unused")
        self.assertFalse(wrong.cheque)
        self.assertFalse(wrong.issued_on)
        self.assertFalse(wrong.reserved_by)

        self.assertEqual(
            frappe.db.get_value("Cheque Book", self.book.name, "unused_leaves"), unused_before,
            "one leaf freed and one taken: the Unused count is unchanged",
        )
        notes = [e.notes for e in _events(self.cheque, "Note")]
        self.assertTrue(any("corrected by patch" in (n or "") for n in notes), notes)

    def test_rerun_is_a_noop(self):
        chq4_patch.correct_leaf(**self._correction())
        before = self._snapshot()

        self.assertEqual(chq4_patch.correct_leaf(**self._correction()), "already")
        self.assertEqual(self._snapshot(), before, "a second run must write nothing")

    def test_refuses_when_the_right_leaf_belongs_to_another_cheque(self):
        other = _draft(self.book, cheque_leaf=self.right.name)
        before = self._snapshot()

        self.assertEqual(chq4_patch.correct_leaf(**self._correction()), "mismatch")
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(_leaf(self.right.name).cheque, other.name)

    def test_refuses_when_the_numbers_do_not_match(self):
        before = self._snapshot()
        self.assertEqual(chq4_patch.correct_leaf(**self._correction(right_no="0")), "mismatch")
        self.assertEqual(self._snapshot(), before)

    def test_the_shipped_correction_leaves_other_sites_alone(self):
        names = (chq4_patch.CORRECTION["wrong_leaf"], chq4_patch.CORRECTION["right_leaf"])
        leaves_before = [_leaf(n) for n in names]
        cheque_before = frappe.db.get_value(
            "Cheque", chq4_patch.CORRECTION["cheque"], ["cheque_leaf", "cheque_no", "modified"], as_dict=True
        )

        chq4_patch.execute()
        chq4_patch.execute()

        self.assertEqual([_leaf(n) for n in names], leaves_before)
        self.assertEqual(
            frappe.db.get_value(
                "Cheque", chq4_patch.CORRECTION["cheque"], ["cheque_leaf", "cheque_no", "modified"], as_dict=True
            ),
            cheque_before,
        )
