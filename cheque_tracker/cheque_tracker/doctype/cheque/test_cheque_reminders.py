# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""Tests for the §4.6 daily reminder digest (cheque_tracker/tasks.py).

Emails are muted on this site (site_config mute_emails=1), so nothing is ever
delivered — Frappe still writes the Email Queue row, which is what these tests
assert on, alongside the dict the job returns.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from cheque_tracker.cheque_tracker.doctype.cheque.test_cheque_financial import (
    _get_or_create_test_bank,
)
from cheque_tracker.tasks import (
    DIGEST_CLAIM_PARENT,
    get_cheques_for_reminder,
    send_daily_cheque_reminders,
)
from cheque_tracker.tests.utils import get_test_env

REMINDER_DAYS = 3
NOTIFY_EMAILS = "treasury@eei.localhost, cfo@eei.localhost"


class TestChequeReminders(FrappeTestCase):

    def setUp(self):
        env = get_test_env()
        self.company = env["company"]
        self.customer = env["customer"]
        self.currency = env["currency"]
        self.treasury_user = env["treasury_user"]

        settings = frappe.get_single("Cheque Tracker Settings")
        settings.reminder_days = REMINDER_DAYS
        settings.notify_emails = NOTIFY_EMAILS
        settings.flags.ignore_permissions = True
        settings.save(ignore_permissions=True)

        # FrappeTestCase rolls back once per *class*, not per test, so the
        # per-day claim token a previous test wrote is still in the table and
        # would make this test's first run look like a repeat run.
        frappe.db.delete("DefaultValue", {"parent": DIGEST_CLAIM_PARENT})

    # ------------------------------------------------------------------ #
    #  helpers                                                            #
    # ------------------------------------------------------------------ #

    def _incoming(self, due_date):
        """A submitted Incoming cheque falling due on `due_date`."""
        chq = frappe.new_doc("Cheque")
        chq.cheque_type = "Incoming"
        chq.company     = self.company
        chq.party_type  = "Customer"
        chq.party       = self.customer
        chq.amount      = 1000
        chq.currency    = self.currency
        chq.due_date    = due_date
        chq.issue_date  = today()
        chq.cheque_no   = f"RMD-{frappe.generate_hash(length=8)}"
        chq.drawer_name = "Test Drawer"
        chq.drawee_bank = _get_or_create_test_bank()
        chq.flags.ignore_permissions = True
        chq.insert()
        chq = frappe.get_doc("Cheque", chq.name)
        chq.flags.ignore_permissions = True
        chq.submit()
        chq.reload()
        return chq

    def _force_status(self, cheque, status):
        """Park a cheque in a terminal status.

        The workflow reaches these through validated transitions that need
        matching accounting; the tracker itself writes them with db.set_value
        (cheque.py:181) because they are not in the status field's Select
        options. The digest reads the same column, so writing it directly is
        the honest way to set up the exclusion cases.
        """
        frappe.db.set_value("Cheque", cheque.name, "status", status)

    def _digest_names(self, as_of=None):
        overdue, upcoming = get_cheques_for_reminder(as_of=as_of, reminder_days=REMINDER_DAYS)
        return [row.name for row in overdue], [row.name for row in upcoming]

    # ------------------------------------------------------------------ #
    #  due-window selection                                               #
    # ------------------------------------------------------------------ #

    def test_window_splits_overdue_from_upcoming(self):
        overdue_chq = self._incoming(add_days(today(), -2))
        due_today = self._incoming(today())
        inside = self._incoming(add_days(today(), REMINDER_DAYS))

        overdue, upcoming = self._digest_names()

        self.assertIn(overdue_chq.name, overdue)
        # Due today is not late yet, so it belongs to the "due soon" half.
        self.assertIn(due_today.name, upcoming)
        self.assertIn(inside.name, upcoming)
        self.assertNotIn(overdue_chq.name, upcoming)
        self.assertNotIn(due_today.name, overdue)

    def test_cheques_beyond_the_window_are_not_reminded(self):
        outside = self._incoming(add_days(today(), REMINDER_DAYS + 1))

        overdue, upcoming = self._digest_names()

        self.assertNotIn(outside.name, overdue)
        self.assertNotIn(outside.name, upcoming)

    def test_draft_cheques_are_not_reminded(self):
        chq = frappe.new_doc("Cheque")
        chq.cheque_type = "Incoming"
        chq.company     = self.company
        chq.party_type  = "Customer"
        chq.party       = self.customer
        chq.amount      = 1000
        chq.currency    = self.currency
        chq.due_date    = today()
        chq.cheque_no   = f"RMD-DRAFT-{frappe.generate_hash(length=6)}"
        chq.drawee_bank = _get_or_create_test_bank()
        chq.flags.ignore_permissions = True
        chq.insert()

        overdue, upcoming = self._digest_names()

        self.assertEqual(chq.docstatus, 0)
        self.assertNotIn(chq.name, overdue + upcoming)

    # ------------------------------------------------------------------ #
    #  status exclusions                                                  #
    # ------------------------------------------------------------------ #

    def test_closed_statuses_are_excluded(self):
        closed = {}
        for status in ("Cleared", "Cancelled", "Replaced", "Returned"):
            chq = self._incoming(add_days(today(), -1))
            self._force_status(chq, status)
            closed[status] = chq.name

        overdue, upcoming = self._digest_names()
        reminded = overdue + upcoming

        for status, name in closed.items():
            self.assertNotIn(name, reminded, f"{status} cheque should not be reminded")

    def test_bounced_cheque_is_still_reminded(self):
        # The one live status that looks terminal but is not: a bounced cheque
        # is money still owed, so it must keep surfacing.
        chq = self._incoming(add_days(today(), -1))
        self._force_status(chq, "Bounced")

        overdue, upcoming = self._digest_names()

        self.assertIn(chq.name, overdue)

    def test_deposited_and_issued_cheques_are_reminded(self):
        deposited = self._incoming(today())
        self._force_status(deposited, "Deposited")
        issued = self._incoming(add_days(today(), -3))
        self._force_status(issued, "Issued")

        overdue, upcoming = self._digest_names()

        self.assertIn(deposited.name, upcoming)
        self.assertIn(issued.name, overdue)

    # ------------------------------------------------------------------ #
    #  one digest per day                                                 #
    # ------------------------------------------------------------------ #

    def _queued_digests(self):
        return set(
            frappe.get_all(
                "Email Queue",
                filters={"reference_doctype": "Cheque Tracker Settings"},
                pluck="name",
            )
        )

    def test_digest_is_sent_once_per_day(self):
        self._incoming(add_days(today(), -1))
        before = self._queued_digests()

        first = send_daily_cheque_reminders()
        second = send_daily_cheque_reminders()

        self.assertTrue(first["sent"], f"first run should send: {first}")
        self.assertIsNotNone(first["email_queue"])

        self.assertFalse(second["sent"])
        self.assertEqual(second["reason"], "already sent today")
        self.assertIsNone(second["email_queue"])

        queued = self._queued_digests() - before
        self.assertEqual(queued, {first["email_queue"]}, "exactly one digest may be queued per day")

    def test_second_run_still_reports_the_same_cheques(self):
        chq = self._incoming(add_days(today(), -1))

        send_daily_cheque_reminders()
        second = send_daily_cheque_reminders()

        # Suppressing the *send* must not blind the caller to what is due —
        # the sweep still reports, it just refuses to mail twice.
        self.assertIn(chq.name, second["overdue"])

    def test_a_different_day_is_claimed_separately(self):
        self._incoming(add_days(today(), -1))

        today_run = send_daily_cheque_reminders()
        yesterday_run = send_daily_cheque_reminders(as_of=add_days(today(), -1))

        self.assertTrue(today_run["sent"])
        self.assertTrue(yesterday_run["sent"], "the claim is per day, not global")
        self.assertNotEqual(today_run["email_queue"], yesterday_run["email_queue"])

    def test_no_digest_when_nothing_is_due(self):
        # Far future window end, so only cheques already outside every other
        # test's window remain — assert on the reason, not on emptiness of the
        # site, which other tests in this class populate.
        result = send_daily_cheque_reminders(as_of=add_days(today(), -3650))

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "nothing due")
        # A silent day must not burn the claim: a cheque submitted an hour
        # later still deserves its digest.
        self.assertFalse(
            frappe.db.exists("DefaultValue", f"digest-{result['date']}"),
            "an empty run must not claim the day",
        )

    # ------------------------------------------------------------------ #
    #  fan-out: email recipients, Desk notification, Slack guard          #
    # ------------------------------------------------------------------ #

    def test_recipients_are_parsed_from_settings(self):
        self._incoming(add_days(today(), -1))

        result = send_daily_cheque_reminders()

        self.assertEqual(result["recipients"], ["treasury@eei.localhost", "cfo@eei.localhost"])
        recipients = frappe.get_all(
            "Email Queue Recipient",
            filters={"parent": result["email_queue"]},
            pluck="recipient",
        )
        self.assertEqual(sorted(recipients), ["cfo@eei.localhost", "treasury@eei.localhost"])

    def test_treasury_users_get_a_desk_notification(self):
        self._incoming(add_days(today(), -1))

        result = send_daily_cheque_reminders()

        self.assertIn(self.treasury_user, result["notified_users"])
        self.assertTrue(
            frappe.db.exists(
                "Notification Log",
                {
                    "for_user": self.treasury_user,
                    "type": "Alert",
                    "document_type": "Cheque Tracker Settings",
                },
            ),
            "Treasury User should have a Notification Log row",
        )

    def test_slack_is_muted_on_a_developer_site(self):
        self._incoming(add_days(today(), -1))

        result = send_daily_cheque_reminders()

        # site_config sets developer_mode, and the guard must hold whether or
        # not a Slack Webhook URL happens to be configured here.
        self.assertEqual(result["slack"], "skipped: developer_mode")
