"""§3.3 — fixture correctness, migrate-idempotency and the repair patch.

The dashboard fixtures reverted to broken values on 30/07 and 09/08 in
production. These tests pin the corrected state (BUILD_INSTRUCTIONS §3.1) from
three directions:

* the shipped JSON matches the spec;
* the live records match the spec — and because `bench run-tests` runs against a
  migrated site, that is also the post-migrate assertion §3.3 asks for
  (the two-migrate loop is driven from the command line, see ACCEPTANCE_REPORT);
* the repair patch turns a site carrying the OLD broken values into a correct
  one, and is idempotent.
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from cheque_tracker.patches.v3_2 import repair_dashboard_fixtures
from cheque_tracker.tests import verify_fixtures

# The exact broken values that shipped in v1.1.5 — reproduced here so the patch
# test starts from the state real sites are in.
BROKEN_OVERDUE_OUTGOING = json.dumps(
	[
		["Cheque", "cheque_type", "=", "Outgoing"],
		["Cheque", "docstatus", "=", 1],
		["Cheque", "status", "in", ["Received", "In Safe", "Deposited"]],
		["Cheque", "due_date", "<", "Today"],
	]
)
BROKEN_BOUNCED_INCOMING = json.dumps(
	[["Cheque", "cheque_type", "=", "Incoming"], ["Cheque", "status", "=", "Bounced"]]
)


class TestShippedFixtures(FrappeTestCase):
	def test_shipped_fixture_files_match_spec(self):
		failures = verify_fixtures.assert_shipped_fixtures_match()
		self.assertEqual(failures, {}, f"shipped fixtures violate §3.1: {failures}")

	def test_live_number_cards_match_spec(self):
		failures = verify_fixtures.assert_number_cards()
		self.assertEqual(failures, {}, f"live Number Cards violate §3.1: {failures}")

	def test_live_dashboard_charts_match_spec(self):
		failures = verify_fixtures.assert_dashboard_charts()
		self.assertEqual(failures, {}, f"live Dashboard Charts violate §3.1: {failures}")

	def test_all_cards_and_two_charts_exist(self):
		self.assertEqual(len(verify_fixtures.NUMBER_CARDS), 11)
		self.assertEqual(len(verify_fixtures.DASHBOARD_CHARTS), 2)
		for name in verify_fixtures.NUMBER_CARDS:
			self.assertTrue(frappe.db.exists("Number Card", name), f"{name} missing")
		for name in verify_fixtures.DASHBOARD_CHARTS:
			self.assertTrue(frappe.db.exists("Dashboard Chart", name), f"{name} missing")

	def test_overdue_cards_have_no_literal_today(self):
		"""The literal string 'Today' reaches MariaDB verbatim and matches nothing."""
		for name in verify_fixtures.OVERDUE_CARDS:
			filters = frappe.db.get_value("Number Card", name, "filters_json")
			self.assertNotIn("Today", filters or "", f"{name} still carries a literal 'Today'")
			dynamic = frappe.db.get_value("Number Card", name, "dynamic_filters_json")
			self.assertEqual(
				json.loads(dynamic or "[]"),
				verify_fixtures.OVERDUE_DYNAMIC_FILTER,
				f"{name} dynamic_filters_json",
			)

	def test_outgoing_cards_use_the_outgoing_vocabulary(self):
		"""§3.1.1 + §4.1 — outgoing cheques disappeared from these cards, and the
		root cause was that they were filtered with incoming statuses."""
		for name in ("Active Outgoing", "Pending Payable", "Due This Week Outgoing", "Overdue Outgoing"):
			filters = json.loads(frappe.db.get_value("Number Card", name, "filters_json"))
			status_clause = next(row for row in filters if row[1] == "status")
			self.assertIn("Handed Over", status_clause[3], f"{name} omits Handed Over")
			self.assertIn("Issued", status_clause[3], f"{name} omits Issued")
			self.assertNotIn("Deposited", status_clause[3], f"{name} keeps incoming-only Deposited")
			self.assertNotIn("Received", status_clause[3], f"{name} keeps incoming-only Received")

	def test_endorsed_is_not_counted_as_pending_receivable(self):
		"""§4.3 — once endorsed the cheque is no longer our receivable."""
		filters = json.loads(frappe.db.get_value("Number Card", "Pending Receivable", "filters_json"))
		status_clause = next(row for row in filters if row[1] == "status")
		self.assertNotIn("Endorsed", status_clause[3])
		self.assertTrue(frappe.db.exists("Number Card", "Endorsed"), "Endorsed card missing")


class TestRepairPatch(FrappeTestCase):
	"""§3.3 — seed a site with the OLD broken values, run the patch, assert corrected."""

	def _restore(self, doctype, name, values):
		frappe.db.set_value(doctype, name, values)

	def test_patch_repairs_broken_number_cards(self):
		good_overdue = frappe.db.get_value(
			"Number Card", "Overdue Outgoing", ["filters_json", "dynamic_filters_json"], as_dict=True
		)
		good_bounced = frappe.db.get_value("Number Card", "Bounced Incoming", "filters_json")
		self.addCleanup(
			self._restore,
			"Number Card",
			"Overdue Outgoing",
			{
				"filters_json": good_overdue.filters_json,
				"dynamic_filters_json": good_overdue.dynamic_filters_json,
			},
		)
		self.addCleanup(self._restore, "Number Card", "Bounced Incoming", {"filters_json": good_bounced})

		# Regress both cards to the v1.1.5 values.
		frappe.db.set_value(
			"Number Card",
			"Overdue Outgoing",
			{"filters_json": BROKEN_OVERDUE_OUTGOING, "dynamic_filters_json": None},
		)
		frappe.db.set_value("Number Card", "Bounced Incoming", {"filters_json": BROKEN_BOUNCED_INCOMING})

		self.assertNotEqual(verify_fixtures.assert_number_cards(), {}, "regression setup did not take")

		repair_dashboard_fixtures.execute()

		self.assertEqual(
			verify_fixtures.assert_number_cards(),
			{},
			"patch did not restore the cards to §3.1",
		)

	def test_patch_recreates_chart_when_set_once_field_differs(self):
		"""chart_type is set_only_once — an in-place update raises, so the patch
		must delete and reinsert under the same name."""
		original = frappe.get_doc("Dashboard Chart", "Cheques Over Time").as_dict()
		self.addCleanup(self._reinstate_chart, original)

		# Recreate the chart as it shipped in v1.1.5: Count on issue_date.
		frappe.delete_doc("Dashboard Chart", "Cheques Over Time", force=True, ignore_permissions=True)
		broken = frappe.get_doc(
			{
				"doctype": "Dashboard Chart",
				"chart_name": "Cheques Over Time",
				"chart_type": "Count",
				"document_type": "Cheque",
				"based_on": "issue_date",
				"type": "Line",
				"timeseries": 1,
				"timespan": "Last Year",
				"time_interval": "Monthly",
				"is_public": 1,
				"module": "Cheque Tracker",
				"currency": "EGP",
				"filters_json": json.dumps([["Cheque", "docstatus", "=", 1]]),
			}
		)
		broken.flags.ignore_permissions = True
		broken.insert(ignore_permissions=True)
		self.assertEqual(broken.name, "Cheques Over Time")

		repair_dashboard_fixtures.execute()

		repaired = frappe.db.get_value(
			"Dashboard Chart",
			"Cheques Over Time",
			["chart_type", "based_on", "value_based_on"],
			as_dict=True,
		)
		self.assertEqual(repaired.chart_type, "Sum")
		self.assertEqual(repaired.based_on, "due_date")
		self.assertEqual(repaired.value_based_on, "amount")

	def test_patch_is_idempotent(self):
		"""A second run must change nothing — migrate runs it on every deploy."""
		repair_dashboard_fixtures.execute()
		first = verify_fixtures.collect_failures()
		repair_dashboard_fixtures.execute()
		second = verify_fixtures.collect_failures()
		self.assertEqual(first, {})
		self.assertEqual(second, {})

	def _reinstate_chart(self, original):
		if frappe.db.exists("Dashboard Chart", original["name"]):
			frappe.delete_doc(
				"Dashboard Chart", original["name"], force=True, ignore_permissions=True
			)
		doc = frappe.get_doc(original)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
