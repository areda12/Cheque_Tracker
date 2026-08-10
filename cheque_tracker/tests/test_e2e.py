"""§4.8 — scenario matrix, Payment Entry integration, and the v1.2 migration.

The matrix itself lives in `cheque_tracker.tests.e2e` so it can also be run
standalone as `bench execute cheque_tracker.tests.e2e.run_all`. Here each
scenario becomes its own test, so a failure names the flow that broke instead of
collapsing eleven flows into one red line.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate, today

from cheque_tracker.cheque_tracker.doctype.cheque import payment_entry_sync
from cheque_tracker.tests import e2e
from cheque_tracker.tests.utils import (
    PE_APPROVER_USER,
    PE_CLERK_USER,
    ensure_payment_entry_workflow,
    ensure_pe_users,
    get_test_env,
)


# ====================================================================== #
#  §4.8 scenario matrix                                                  #
# ====================================================================== #


def _make_scenario_test(fn):
    def test(self):
        fn()

    test.__doc__ = fn.__doc__
    return test


class TestScenarioMatrix(FrappeTestCase):
    """One test per flow — generated from e2e.SCENARIOS so the two cannot drift."""


for _name, _fn in e2e.SCENARIOS:
    _method = "test_" + _fn.__name__
    setattr(TestScenarioMatrix, _method, _make_scenario_test(_fn))


# ====================================================================== #
#  §4.5 — Payment Entry integration                                      #
# ====================================================================== #


class TestPaymentEntryIntegration(FrappeTestCase):
    """The EEI model: the PE stays draft until the cheque is collected.

    These run with an ACTIVE approval workflow on Payment Entry, mirroring
    production's "Approval Pending by Accounting Manager" gate — that is the only
    way the degraded ToDo path is reachable.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_payment_entry_workflow()
        ensure_pe_users()

    def setUp(self):
        self.env = get_test_env()
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")

    # ---------------------------------------------------------------- #

    def _draft_pe(self, amount=1234.0, cheque_no=None, payment_type="Receive"):
        """An on-account draft Payment Entry paid by cheque."""
        cheque_no = cheque_no or f"PE-{frappe.generate_hash(length=8)}"
        pe = frappe.get_doc(
            {
                "doctype": "Payment Entry",
                "payment_type": payment_type,
                "company": self.env["company"],
                "posting_date": today(),
                "mode_of_payment": "Cheque",
                "party_type": "Customer" if payment_type == "Receive" else "Supplier",
                "party": self.env["customer"] if payment_type == "Receive" else self.env["supplier"],
                "paid_from": self.env["debtors"] if payment_type == "Receive" else self.env["bank_gl_account"],
                "paid_to": self.env["bank_gl_account"] if payment_type == "Receive" else self.env["creditors"],
                "paid_amount": amount,
                "received_amount": amount,
                "source_exchange_rate": 1,
                "target_exchange_rate": 1,
                "reference_no": cheque_no,
                "reference_date": add_days(today(), 30),
            }
        )
        pe.flags.ignore_permissions = True
        pe.insert(ignore_permissions=True)
        return pe

    def _cheque_for(self, pe_name):
        return frappe.db.get_value(
            "Cheque",
            {"reference_doctype": "Payment Entry", "reference_name": pe_name, "docstatus": ["<", 2]},
            "name",
        )

    # ---------------------------------------------------------------- #

    def test_auto_create_fires_once(self):
        """§4.5.1 — a draft PE paid by cheque spawns exactly one Draft Cheque."""
        pe = self._draft_pe()
        first = self._cheque_for(pe.name)
        self.assertTrue(first, "no Cheque was created for the Payment Entry")
        self.assertEqual(frappe.db.get_value("Cheque", first, "docstatus"), 0, "cheque should be Draft")

        # Saving again must not spawn a second one.
        pe.reload()
        pe.remarks = "touched"
        pe.flags.ignore_permissions = True
        pe.save(ignore_permissions=True)

        cheques = frappe.get_all(
            "Cheque",
            filters={"reference_doctype": "Payment Entry", "reference_name": pe.name, "docstatus": ["<", 2]},
            pluck="name",
        )
        self.assertEqual(cheques, [first], f"expected exactly one cheque, got {cheques}")

    def test_auto_create_maps_direction_and_fields(self):
        pe = self._draft_pe(amount=4321.0)
        cheque = frappe.get_doc("Cheque", self._cheque_for(pe.name))

        self.assertEqual(cheque.cheque_type, "Incoming", "Receive should map to Incoming")
        self.assertEqual(flt(cheque.amount), 4321.0)
        self.assertEqual(cheque.cheque_no, pe.reference_no)
        # Frappe hands back a date object; the PE still holds the string we set.
        self.assertEqual(getdate(cheque.due_date), getdate(pe.reference_date))
        self.assertEqual(cheque.party, pe.party)

    def test_outgoing_payment_maps_to_outgoing_cheque(self):
        """A Pay entry is backed by a cheque we wrote."""
        pe = self._draft_pe(payment_type="Pay")
        # Outgoing cheques need a Cheque Book leaf, which the PE cannot know
        # about, so no cheque is auto-created — the user starts from the Cheque.
        self.assertIsNone(self._cheque_for(pe.name))

    def test_draft_pe_edits_sync_onto_the_draft_cheque(self):
        """§4.5.1 — while both are drafts the cheque follows the PE."""
        pe = self._draft_pe(amount=1000.0)
        cheque_name = self._cheque_for(pe.name)

        pe.reload()
        pe.paid_amount = 2500.0
        pe.received_amount = 2500.0
        pe.reference_date = add_days(today(), 45)
        pe.flags.ignore_permissions = True
        pe.save(ignore_permissions=True)

        cheque = frappe.get_doc("Cheque", cheque_name)
        self.assertEqual(flt(cheque.amount), 2500.0, "amount did not sync")
        self.assertEqual(
            getdate(cheque.due_date), getdate(add_days(today(), 45)), "due_date did not sync"
        )

    def test_amount_mismatch_throws_on_cheque_save(self):
        """§4.5.3 — the PE is what posts, so a disagreement is a hard error."""
        pe = self._draft_pe(amount=1000.0)
        cheque = frappe.get_doc("Cheque", self._cheque_for(pe.name))

        cheque.amount = 999.0
        cheque.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            cheque.save(ignore_permissions=True)

    def test_clear_submits_the_draft_pe_when_permitted(self):
        """§4.5.2 — clearing the cheque is what posts the collection."""
        pe = self._draft_pe(amount=1500.0)
        cheque = frappe.get_doc("Cheque", self._cheque_for(pe.name))
        cheque.drawee_bank = self.env["drawee_bank"]
        cheque.bank_account = self.env["bank_account"]
        cheque.flags.ignore_permissions = True
        cheque.save(ignore_permissions=True)
        cheque.submit()

        e2e.act(cheque, "Deposit")
        e2e.act(cheque, "Clear")

        self.assertEqual(cheque.status, "Cleared")
        self.assertEqual(
            frappe.db.get_value("Payment Entry", pe.name, "docstatus"),
            1,
            "the linked Payment Entry was not submitted on clearance",
        )
        self.assertFalse(
            frappe.db.get_value("Cheque", cheque.name, "pe_pending_submission"),
            "pe_pending_submission should be clear when the PE actually posted",
        )

    def test_clear_degrades_to_todo_when_user_cannot_approve(self):
        """§4.5.2 — never force past the approval gate; raise a ToDo instead."""
        pe = self._draft_pe(amount=1600.0)
        cheque = frappe.get_doc("Cheque", self._cheque_for(pe.name))
        cheque.drawee_bank = self.env["drawee_bank"]
        cheque.bank_account = self.env["bank_account"]
        cheque.flags.ignore_permissions = True
        cheque.save(ignore_permissions=True)
        cheque.submit()
        e2e.act(cheque, "Deposit")

        frappe.set_user(PE_CLERK_USER)
        try:
            cheque.reload()
            cheque._settle_on_clear_for_test = True
            from frappe.model.workflow import apply_workflow

            apply_workflow(cheque, "Clear")
        finally:
            frappe.set_user("Administrator")

        cheque.reload()
        self.assertEqual(cheque.status, "Cleared", "the cheque itself must still clear")
        self.assertEqual(
            frappe.db.get_value("Payment Entry", pe.name, "docstatus"),
            0,
            "the PE must NOT be force-submitted past the approval gate",
        )
        self.assertTrue(
            frappe.db.get_value("Cheque", cheque.name, "pe_pending_submission"),
            "pe_pending_submission flag was not set",
        )
        todos = frappe.get_all(
            "ToDo",
            filters={"reference_type": "Payment Entry", "reference_name": pe.name, "status": "Open"},
            pluck="name",
        )
        self.assertTrue(todos, "no ToDo was raised for the pending Payment Entry")

    # ---- clearance requires something that posts (D2, amended) ------- #

    def test_clear_is_blocked_without_an_accounting_document(self):
        """Refused, not warned: a cheque with nothing linked would be recorded
        as collected while the ledger never hears about it."""
        cheque = e2e.make_incoming(with_payment_entry=False)
        e2e.act(cheque, "Deposit")

        with self.assertRaises(frappe.ValidationError):
            e2e.act(cheque, "Clear")

        cheque.reload()
        self.assertEqual(cheque.status, "Deposited", "the cheque must not have moved")

    def test_cash_clear_is_blocked_without_an_accounting_document(self):
        cheque = e2e.make_incoming(
            with_payment_entry=False,
            clearance_type="Cash",
            cash_account=self.env["cash_account"],
        )
        with self.assertRaises(frappe.ValidationError):
            e2e.act(cheque, "Cash Clear")

        cheque.reload()
        self.assertEqual(cheque.status, "Received")

    def test_ui_path_is_blocked_too(self):
        """change_cheque_status writes with db.set_value, so it needs its own
        gate — otherwise the block would be a form-only illusion."""
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque import change_cheque_status

        cheque = e2e.make_incoming(with_payment_entry=False)
        e2e.act(cheque, "Deposit")

        with self.assertRaises(frappe.ValidationError):
            change_cheque_status(cheque.name, "Cleared")

        self.assertEqual(frappe.db.get_value("Cheque", cheque.name, "status"), "Deposited")

    def test_system_manager_can_override_and_it_is_logged(self):
        cheque = e2e.make_incoming(with_payment_entry=False)
        e2e.act(cheque, "Deposit")

        e2e.set_fields(
            cheque,
            clearance_override=1,
            clearance_override_reason="Collected in cash at the branch; posted by journal in the legacy system.",
        )
        e2e.act(cheque, "Clear")

        self.assertEqual(cheque.status, "Cleared")
        self.assertFalse(cheque.clearance_je, "the override must not conjure a posting")

        notes = frappe.get_all(
            "Cheque Event",
            filters={"parent": cheque.name, "event_type": "Note"},
            pluck="notes",
        )
        override_note = next((n for n in notes if "Override authorised by" in (n or "")), None)
        self.assertIsNotNone(override_note, f"override not recorded on the timeline; got {notes}")
        self.assertIn(frappe.session.user, override_note, "the note must name who overrode")
        self.assertIn("legacy system", override_note, "the note must carry the reason")

    def test_override_requires_a_reason(self):
        cheque = e2e.make_incoming(with_payment_entry=False)
        with self.assertRaises(frappe.ValidationError):
            e2e.set_fields(cheque, clearance_override=1)

    def test_override_is_refused_to_a_non_system_manager(self):
        cheque = e2e.make_incoming(with_payment_entry=False)
        frappe.set_user(PE_CLERK_USER)
        try:
            with self.assertRaises(frappe.PermissionError):
                e2e.set_fields(
                    cheque, clearance_override=1, clearance_override_reason="let me through"
                )
        finally:
            frappe.set_user("Administrator")

    def test_a_journal_entry_also_satisfies_the_gate(self):
        """§4.5 names the Payment Entry, but a cheque carrying a v1.1.x
        clearance Journal Entry is equally backed by something that posted."""
        from cheque_tracker.cheque_tracker.doctype.cheque import cheque_financial

        cheque = e2e.make_incoming(with_payment_entry=False)
        je = frappe.get_doc(
            {
                "doctype": "Journal Entry",
                "voucher_type": "Bank Entry",
                "company": self.env["company"],
                "posting_date": today(),
                "cheque_no": cheque.cheque_no,
                "cheque_date": today(),
                "accounts": [
                    {"account": self.env["bank_gl_account"], "debit_in_account_currency": cheque.amount},
                    {
                        "account": self.env["debtors"],
                        "credit_in_account_currency": cheque.amount,
                        "party_type": "Customer",
                        "party": self.env["customer"],
                    },
                ],
            }
        )
        je.flags.ignore_permissions = True
        je.insert(ignore_permissions=True)
        cheque.db_set("clearance_je", je.name, update_modified=False)
        cheque.reload()

        self.assertIsNotNone(cheque_financial.linked_accounting_document(cheque))
        e2e.act(cheque, "Deposit")
        e2e.act(cheque, "Clear")
        self.assertEqual(cheque.status, "Cleared")

    def test_normal_pe_linked_path_still_clears(self):
        """The ordinary case must be untouched by the gate."""
        pe = self._draft_pe(amount=2100.0)
        cheque = frappe.get_doc("Cheque", self._cheque_for(pe.name))
        cheque.drawee_bank = self.env["drawee_bank"]
        cheque.bank_account = self.env["bank_account"]
        cheque.flags.ignore_permissions = True
        cheque.save(ignore_permissions=True)
        cheque.submit()

        e2e.act(cheque, "Deposit")
        e2e.act(cheque, "Clear")

        self.assertEqual(cheque.status, "Cleared")
        self.assertFalse(cheque.clearance_override, "no override should have been needed")
        self.assertEqual(frappe.db.get_value("Payment Entry", pe.name, "docstatus"), 1)

    def test_no_clearance_journal_entry_is_created(self):
        """D2 — the tracker posts no GL in v1.2; the PE is the only posting doc."""
        pe = self._draft_pe(amount=1700.0)
        cheque = frappe.get_doc("Cheque", self._cheque_for(pe.name))
        cheque.drawee_bank = self.env["drawee_bank"]
        cheque.bank_account = self.env["bank_account"]
        cheque.flags.ignore_permissions = True
        cheque.save(ignore_permissions=True)
        cheque.submit()
        e2e.act(cheque, "Deposit")

        je_before = frappe.db.count("Journal Entry")
        e2e.act(cheque, "Clear")
        je_after = frappe.db.count("Journal Entry")

        self.assertEqual(je_before, je_after, "a Journal Entry was posted — double-posting risk")
        self.assertFalse(frappe.db.get_value("Cheque", cheque.name, "clearance_je"))

    def test_submitted_pe_only_gets_clearance_date(self):
        pe = self._draft_pe(amount=1800.0)
        cheque = frappe.get_doc("Cheque", self._cheque_for(pe.name))
        cheque.drawee_bank = self.env["drawee_bank"]
        cheque.bank_account = self.env["bank_account"]
        cheque.flags.ignore_permissions = True
        cheque.save(ignore_permissions=True)
        cheque.submit()

        # Approve the PE up-front so it is already submitted when the cheque clears.
        pe.reload()
        from frappe.model.workflow import apply_workflow

        apply_workflow(pe, "Approve")
        self.assertEqual(frappe.db.get_value("Payment Entry", pe.name, "docstatus"), 1)

        e2e.act(cheque, "Deposit")
        e2e.act(cheque, "Clear")

        self.assertEqual(cheque.status, "Cleared")
        self.assertEqual(
            frappe.db.get_value("Payment Entry", pe.name, "clearance_date"),
            cheque.cleared_date,
            "clearance_date was not stamped on the already-submitted PE",
        )


# ====================================================================== #
#  §4.1 — migration                                                      #
# ====================================================================== #


class TestStatusVocabularyMigration(FrappeTestCase):
    """The v3_3 patch must move outgoing cheques off the incoming vocabulary."""

    def test_outgoing_received_becomes_issued(self):
        from cheque_tracker.patches.v3_3 import split_status_vocabulary

        cheque = e2e.make_outgoing()
        # Regress it to the v1.1.x state, exactly as a deployed site holds it.
        frappe.db.set_value("Cheque", cheque.name, "status", "Received", update_modified=False)
        frappe.db.sql(
            """UPDATE `tabCheque Event` SET event_type = 'Received'
               WHERE parent = %s AND event_type = 'Issued'""",
            cheque.name,
        )

        split_status_vocabulary.execute()

        self.assertEqual(
            frappe.db.get_value("Cheque", cheque.name, "status"),
            "Issued",
            "outgoing cheque was not migrated off the incoming vocabulary",
        )
        events = frappe.get_all(
            "Cheque Event", filters={"parent": cheque.name}, pluck="event_type"
        )
        self.assertNotIn("Received", events, "outgoing timeline still says 'Received'")

    def test_incoming_is_left_alone(self):
        from cheque_tracker.patches.v3_3 import split_status_vocabulary

        cheque = e2e.make_incoming()
        self.assertEqual(cheque.status, "Received")

        split_status_vocabulary.execute()

        self.assertEqual(
            frappe.db.get_value("Cheque", cheque.name, "status"),
            "Received",
            "incoming cheques must keep their vocabulary",
        )

    def test_migration_is_idempotent(self):
        from cheque_tracker.patches.v3_3 import split_status_vocabulary

        cheque = e2e.make_outgoing()
        split_status_vocabulary.execute()
        first = frappe.db.get_value("Cheque", cheque.name, "status")
        split_status_vocabulary.execute()
        second = frappe.db.get_value("Cheque", cheque.name, "status")
        self.assertEqual(first, "Issued")
        self.assertEqual(second, "Issued")


# ====================================================================== #
#  v1.3.1 — reversing a clearance, and editing references after submit   #
# ====================================================================== #


class TestUnclear(FrappeTestCase):
    """Cleared is no longer terminal, but reversing it is a System Manager act
    and it does NOT unwind the ledger."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_payment_entry_workflow()
        ensure_pe_users()

    def setUp(self):
        self.env = get_test_env()
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")

    def _cleared_incoming(self):
        cheque = e2e.make_incoming()
        e2e.act(cheque, "Deposit")
        e2e.act(cheque, "Clear")
        self.assertEqual(cheque.status, "Cleared")
        return cheque

    def _notes(self, cheque_name):
        return frappe.get_all(
            "Cheque Event",
            filters={"parent": cheque_name, "event_type": "Note"},
            pluck="notes",
        )

    def test_incoming_unclears_back_to_deposited(self):
        cheque = self._cleared_incoming()
        self.assertTrue(cheque.cleared_date)

        e2e.set_fields(cheque, unclear_reason="Bank reversed the credit — cheque returned unpaid.")
        e2e.act(cheque, "Un-clear")

        self.assertEqual(cheque.status, "Deposited")
        self.assertFalse(
            frappe.db.get_value("Cheque", cheque.name, "cleared_date"),
            "cleared_date must be cleared on un-clear",
        )

        note = next((n for n in self._notes(cheque.name) if "Clearance reversed" in (n or "")), None)
        self.assertIsNotNone(note, f"un-clear not on the timeline: {self._notes(cheque.name)}")
        self.assertIn(frappe.session.user, note, "the note must name who reversed it")
        self.assertIn("returned unpaid", note, "the note must carry the reason")

    def test_outgoing_unclears_back_to_handed_over(self):
        cheque = e2e.make_outgoing()
        e2e.act(cheque, "Hand Over")
        e2e.act(cheque, "Clear")
        self.assertEqual(cheque.status, "Cleared")

        e2e.set_fields(cheque, unclear_reason="Posted against the wrong cheque.")
        e2e.act(cheque, "Un-clear")

        self.assertEqual(cheque.status, "Handed Over")
        self.assertFalse(frappe.db.get_value("Cheque", cheque.name, "cleared_date"))

    def test_unclear_requires_a_reason(self):
        cheque = self._cleared_incoming()
        with self.assertRaises(frappe.ValidationError):
            e2e.act(cheque, "Un-clear")

        cheque.reload()
        self.assertEqual(cheque.status, "Cleared", "the cheque must not have moved")

    def test_unclear_is_refused_to_a_non_system_manager(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque import change_cheque_status

        cheque = self._cleared_incoming()
        e2e.set_fields(cheque, unclear_reason="attempt by a treasury user")

        frappe.set_user(PE_CLERK_USER)
        try:
            with self.assertRaises(frappe.PermissionError):
                change_cheque_status(cheque.name, "Deposited")
        finally:
            frappe.set_user("Administrator")

        self.assertEqual(frappe.db.get_value("Cheque", cheque.name, "status"), "Cleared")

    def test_submitted_payment_entry_is_left_alone_and_flagged(self):
        """Cancelling a submitted PE reverses GL entries and can break its own
        downstream links — that decision belongs to a human, so we say so
        instead of guessing."""
        cheque = self._cleared_incoming()
        pe_name = cheque.reference_name
        self.assertEqual(
            frappe.db.get_value("Payment Entry", pe_name, "docstatus"), 1, "PE should have posted"
        )

        e2e.set_fields(cheque, unclear_reason="Credited to the wrong account.")
        e2e.act(cheque, "Un-clear")

        self.assertEqual(
            frappe.db.get_value("Payment Entry", pe_name, "docstatus"),
            1,
            "the Payment Entry must NOT be auto-cancelled",
        )
        flagged = next(
            (n for n in self._notes(cheque.name) if "left submitted on un-clear" in (n or "")), None
        )
        self.assertIsNotNone(
            flagged, f"the still-posted PE must be recorded: {self._notes(cheque.name)}"
        )

    def test_cannot_unclear_an_incoming_cheque_to_handed_over(self):
        from cheque_tracker.cheque_tracker.doctype.cheque.cheque import change_cheque_status

        cheque = self._cleared_incoming()
        e2e.set_fields(cheque, unclear_reason="wrong target")
        with self.assertRaises(frappe.ValidationError):
            change_cheque_status(cheque.name, "Handed Over")


class TestReferenceEditAfterSubmit(FrappeTestCase):
    """Attaching a missing accounting reference is routine; repointing one on a
    submitted cheque moves the money to a different document after the fact."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_payment_entry_workflow()
        ensure_pe_users()

    def setUp(self):
        self.env = get_test_env()
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_reference_fields_are_editable_after_submit(self):
        meta = frappe.get_meta("Cheque")
        for field in ("reference_doctype", "reference_name"):
            self.assertTrue(
                meta.get_field(field).allow_on_submit,
                f"{field} must be editable after submit (production bridges this with a Property Setter)",
            )

    def test_filling_an_empty_reference_is_allowed(self):
        cheque = e2e.make_incoming(with_payment_entry=False)
        self.assertFalse(cheque.reference_name)

        pe = e2e.attach_payment_entry(cheque)

        self.assertEqual(
            frappe.db.get_value("Cheque", cheque.name, "reference_name"),
            pe.name,
            "attaching a missing reference must be allowed",
        )

    def test_overwriting_a_reference_is_refused_to_a_non_system_manager(self):
        cheque = e2e.make_incoming()
        original = cheque.reference_name
        self.assertTrue(original)

        frappe.set_user(PE_CLERK_USER)
        try:
            cheque.reload()
            cheque.reference_name = None
            with self.assertRaises(frappe.PermissionError):
                cheque.save()
        finally:
            frappe.set_user("Administrator")

        self.assertEqual(frappe.db.get_value("Cheque", cheque.name, "reference_name"), original)

    def test_system_manager_may_repoint_a_reference(self):
        cheque = e2e.make_incoming()
        self.assertTrue(cheque.reference_name)

        cheque.reload()
        cheque.reference_doctype = None
        cheque.reference_name = None
        cheque.flags.ignore_permissions = True
        cheque.save(ignore_permissions=True)

        self.assertFalse(frappe.db.get_value("Cheque", cheque.name, "reference_name"))

    def test_amount_validation_still_applies_when_attaching(self):
        """The edit relaxation must not open a hole in §4.5.3."""
        cheque = e2e.make_incoming(with_payment_entry=False)
        pe = e2e.attach_payment_entry(cheque)

        # Drift the cheque away from the PE it now points at.
        cheque.reload()
        cheque.amount = flt(cheque.amount) + 500
        cheque.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            cheque.save(ignore_permissions=True)
