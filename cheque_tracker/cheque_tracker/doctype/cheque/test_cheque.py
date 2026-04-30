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
    # Fresh fetch — after_insert hook appends a "Created" event and bumps
    # modified, so the in-memory doc is stale for any caller that submits.
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

    # ------------------------------------------------------------------ #
    #  C1 regression — savepoint rollback on post-reservation failure     #
    # ------------------------------------------------------------------ #

    def test_c1_leaf_rollback_when_later_validation_fails(self):
        """
        Regression for the pre-fix bug where _handle_outgoing_leaf_reservation
        called frappe.db.commit() after reserving the leaf, prematurely
        committing the outer save() transaction. A later raise in before_save
        could not roll the leaf back, leaving an orphan Reserved leaf.

        With the fix (the explicit commit removed), the leaf reservation
        runs inside the outer save() transaction. Production request handlers
        always rollback on exception; this test simulates that discipline by
        wrapping the failing insert in a savepoint and rolling it back before
        asserting on leaf state.
        """
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(7800, 7810, company=co, bank_account=ba)
        cb.submit()

        reserved_before = frappe.get_all(
            "Cheque Leaf",
            filters={"cheque_book": cb.name, "leaf_status": "Reserved"},
        )
        self.assertEqual(len(reserved_before), 0)

        # Simulate the rollback discipline of a production request handler:
        # any exception during save() is followed by a transaction rollback.
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
    #  E2 + E8 regressions — GL integrity hole                            #
    # ------------------------------------------------------------------ #

    def _build_cleared_cheque(self):
        """
        Helper: build a fully-cleared cheque (PE submitted + JE submitted).
        Returns (cheque_doc, clearance_je_doc). Skips the test if the site
        lacks the company / customer / accounts needed to run the financial
        flow end-to-end.
        """
        company, customer, currency = _financial_env()
        if not all([company, customer]):
            self.skipTest("Missing company or customer in test environment.")

        pdc = _get_or_create_pdc_account(company)
        bank_gl = _get_test_bank_gl_account(company)
        ar = frappe.db.get_value("Company", company, "default_receivable_account")
        if not pdc or not bank_gl or not ar:
            self.skipTest("Missing PDC / Bank GL / AR account in test environment.")

        _configure_settings(company, pdc, bank_gl)

        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_clearance_journal_entry,
            make_recording_payment_entry,
        )

        chq = _make_incoming_cheque(company, customer, currency, pdc)

        pe_name = make_recording_payment_entry(chq.name)
        pe = frappe.get_doc("Payment Entry", pe_name)
        pe.flags.ignore_permissions = True
        pe.submit()

        je_name = make_clearance_journal_entry(chq.name)
        je = frappe.get_doc("Journal Entry", je_name)
        je.flags.ignore_permissions = True
        je.submit()

        # Fresh fetch by name — sidesteps reload() corner cases when the
        # JE-submit hook has mutated the cheque via frappe.db.set_value
        # while we still hold the in-memory doc.
        chq = frappe.get_doc("Cheque", chq.name)
        self.assertEqual(chq.status, "Cleared")
        return chq, je

    def _make_received_for_f1(self):
        """
        Create an Incoming cheque submitted to Received status, for F1
        role-gate tests. Incoming cheques auto-transition Draft → Received
        on submit (via Cheque.on_submit hook), giving a known starting
        state for testing Received → In Safe (a Treasury-only transition
        per F1 mapping).
        """
        company, customer, currency = _financial_env()
        if not all([company, customer]):
            self.skipTest("Missing company or customer in test environment.")

        pdc = _get_or_create_pdc_account(company)
        bank_gl = _get_test_bank_gl_account(company)
        if not pdc or not bank_gl:
            self.skipTest("Missing PDC / Bank GL account in test environment.")

        _configure_settings(company, pdc, bank_gl)

        chq = _make_incoming_cheque(company, customer, currency, pdc)
        chq = frappe.get_doc("Cheque", chq.name)  # fresh fetch
        self.assertEqual(
            chq.status, "Received",
            f"Expected Incoming cheque to be in Received status after submit, got {chq.status!r}",
        )
        return chq

    def test_e2_blocks_transition_out_of_cleared(self):
        """E2: change_cheque_status must reject any transition away from
        Cleared while the clearance JE is still submitted."""
        chq, _je = self._build_cleared_cheque()

        with self.assertRaises(frappe.ValidationError):
            change_cheque_status(chq.name, "Received")

        # Status must remain Cleared in the DB (no partial state)
        db_status = frappe.db.get_value("Cheque", chq.name, "status")
        self.assertEqual(db_status, "Cleared")

    def test_e2_allows_transition_back_to_cleared_after_je_cancel(self):
        """E2: cancelling the clearance JE rolls cheque back to Received
        via the on_cancel hook; subsequent transitions are no longer
        blocked by the E2 guard."""
        chq, je = self._build_cleared_cheque()

        # Cancel the JE — triggers _handle_clearance_je_cancel, which
        # sets cheque status back to Received and clears the link.
        je.flags.ignore_permissions = True
        je.cancel()

        chq.reload()
        self.assertEqual(chq.status, "Received")

        # The E2 guard should no longer fire (doc.status != "Cleared").
        result = change_cheque_status(chq.name, "In Safe")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            frappe.db.get_value("Cheque", chq.name, "status"),
            "In Safe",
        )

    def test_e8_link_fields_locked_post_submit(self):
        """E8: with allow_on_submit=0 on the three accounting link fields,
        a doc.save() that mutates one of them must be rejected by Frappe's
        update-after-submit guard. The PE/JE hooks bypass this via
        frappe.db.set_value, so they remain functional."""
        chq, _je = self._build_cleared_cheque()

        fresh = frappe.get_doc("Cheque", chq.name)
        original = fresh.recording_payment_entry
        self.assertIsNotNone(original)

        fresh.recording_payment_entry = "PE-FAKE-NONEXISTENT"
        fresh.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            fresh.save()

        # DB value must be untouched
        self.assertEqual(
            frappe.db.get_value("Cheque", chq.name, "recording_payment_entry"),
            original,
        )

    # ------------------------------------------------------------------ #
    #  D2 regression — Cheque.on_cancel cleans up Draft accounting docs   #
    # ------------------------------------------------------------------ #

    def test_d2_cancel_cheque_cleans_up_draft_payment_entry(self):
        """
        D2: Cancelling a cheque must delete any Draft Payment Entry
        linked to it, otherwise the orphan PE can be submitted later
        against a cancelled cheque.
        """
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial import (
            make_recording_payment_entry,
        )

        co, ba, cu, cy = self._env()
        pdc = _get_or_create_pdc_account(co)
        bank_gl = _get_test_bank_gl_account(co)
        if not pdc or not bank_gl:
            self.skipTest("Missing PDC / Bank GL account in test environment.")
        _configure_settings(co, pdc, bank_gl)

        chq = _make_incoming_cheque(co, cu, cy, pdc)
        chq = frappe.get_doc("Cheque", chq.name)

        # Create a Draft recording PE (don't submit it)
        pe_name = make_recording_payment_entry(chq.name)
        pe_docstatus_before = frappe.db.get_value("Payment Entry", pe_name, "docstatus")
        self.assertEqual(
            pe_docstatus_before, 0,
            "Test setup: PE should be Draft before cancel",
        )

        # make_recording_payment_entry mutated cheque.recording_payment_entry
        # via db.set_value, so the in-memory chq has stale modified.
        # Fresh-fetch before cancel to avoid TimestampMismatchError.
        chq = frappe.get_doc("Cheque", chq.name)
        chq.flags.ignore_permissions = True
        chq.cancel()

        # Assert: Draft PE no longer exists (was deleted, not just cancelled)
        pe_exists = frappe.db.exists("Payment Entry", pe_name)
        self.assertFalse(
            pe_exists,
            f"Draft Payment Entry {pe_name} should have been deleted on cheque cancel, but still exists.",
        )

    # ------------------------------------------------------------------ #
    #  F1 regressions — workflow-role gating on change_cheque_status      #
    # ------------------------------------------------------------------ #

    def _ensure_accounts_user(self):
        """Return the email of a user that has ONLY Accounts User role
        (Treasury User / System Manager / Administrator stripped).
        Creates the user if missing."""
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

        # Strip elevated roles, ensure Accounts User present
        unwanted = {"Treasury User", "System Manager", "Administrator"}
        user.roles = [r for r in user.roles if r.role not in unwanted]
        if not any(r.role == "Accounts User" for r in user.roles):
            user.append("roles", {"role": "Accounts User"})
        user.flags.ignore_permissions = True
        user.save()
        return email

    def _make_outgoing_for_f1(self):
        """Build a submitted Outgoing cheque (status stays 'Draft' after
        submit per current on_submit logic — that's the from-state we
        test transitions against). Returns the cheque doc."""
        co, ba, cu, cy = self._env()
        cb = make_cheque_book(8500, 8510, company=co, bank_account=ba)
        cb.submit()
        chq = _outgoing(cb, co, cu, cy)
        chq.submit()
        chq.reload()
        return chq

    def test_f1_accounts_user_blocked_from_treasury_only_transition(self):
        """F1: Accounts User must be blocked from a Treasury-only
        transition. Uses Received → In Safe — a row that exists in
        _TRANSITION_ROLES with allowed roles {Treasury User, System Manager}."""
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

        # DB status must be unchanged after the blocked call.
        after_status = frappe.db.get_value("Cheque", chq.name, "status")
        self.assertEqual(after_status, before_status)

    def test_f1_treasury_user_allowed_for_treasury_transition(self):
        """F1: Administrator (System Manager) must be allowed to drive
        a Treasury-only transition. Uses Received → In Safe — a row that
        exists in _TRANSITION_ROLES with allowed roles
        {Treasury User, System Manager}."""
        chq = self._make_received_for_f1()

        # Running as Administrator (the test default) — has System Manager.
        result = change_cheque_status(chq.name, "In Safe")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            frappe.db.get_value("Cheque", chq.name, "status"),
            "In Safe",
        )

    def test_f1_unknown_transition_falls_through(self):
        """F1: pairs (from_status, to_status) not in _TRANSITION_ROLES
        must fall through. The Select field on Cheque.status constrains
        the allowed values, so an unknown new_status is rejected by
        Frappe's core validation rather than slipping through silently."""
        chq = self._make_outgoing_for_f1()

        # "InvalidStatus" is not in the Cheque.status Select options →
        # Frappe will raise (ValidationError or similar) when log_status_change
        # writes it back via db.set_value or when we attempt the transition.
        with self.assertRaises(Exception):
            change_cheque_status(chq.name, "InvalidStatus")
