"""Assert the live Number Card / Dashboard Chart state matches BUILD_INSTRUCTIONS §3.1.

Two callers:

* `cheque_tracker.tests.test_fixtures` runs it inside the suite.
* `bench --site <site> execute cheque_tracker.tests.verify_fixtures.run` runs it
  between two `bench migrate` invocations — the §3.3 idempotency proof.  The
  fixtures reverted on 30/07 and 09/08 in production, which is why this check
  exists as a standalone command and not only as a unit test.

The expectations below are written out from the spec rather than read back from
`fixtures/*.json`.  Comparing the database to the shipped file would pass
happily if the shipped file itself were wrong; the whole point is to pin the
values §3.1 asks for.  `assert_shipped_fixtures_match()` then checks the JSON
against the same expectations, so file and database are both held to the spec.
"""

import json
import os

import frappe

# ---------------------------------------------------------------------------
# §3.1 expectations
# ---------------------------------------------------------------------------

INCOMING_ACTIVE_STATUSES = ["Received", "In Safe", "Deposited"]
# §3.1.1 fixed "Handed Over" being missing here; §4.1 then split the vocabulary,
# so an active outgoing cheque is Issued / In Safe / Handed Over / Presented and
# never borrows an incoming status. "Endorsed" is deliberately absent from the
# incoming list: once endorsed the cheque is no longer our receivable (§4.3), and
# it gets its own card.
OUTGOING_ACTIVE_STATUSES = ["Issued", "In Safe", "Handed Over", "Presented"]

# §3.1.2 — a literal "Today" is not a Frappe keyword; it reaches MariaDB as the
# string 'Today' and matches nothing. Date-relative filters belong in
# dynamic_filters_json, which is eval'd client-side.
OVERDUE_DYNAMIC_FILTER = [["Cheque", "due_date", "<", "frappe.datetime.get_today()"]]

NUMBER_CARDS = {
	"Active Incoming":        {"cheque_type": "Incoming", "statuses": INCOMING_ACTIVE_STATUSES},
	"Active Outgoing":        {"cheque_type": "Outgoing", "statuses": OUTGOING_ACTIVE_STATUSES},
	"Due This Week Incoming": {"cheque_type": "Incoming", "statuses": INCOMING_ACTIVE_STATUSES},
	"Due This Week Outgoing": {"cheque_type": "Outgoing", "statuses": OUTGOING_ACTIVE_STATUSES},
	"Overdue Incoming":       {"cheque_type": "Incoming", "statuses": INCOMING_ACTIVE_STATUSES},
	"Overdue Outgoing":       {"cheque_type": "Outgoing", "statuses": OUTGOING_ACTIVE_STATUSES},
	"Pending Receivable":     {"cheque_type": "Incoming", "statuses": INCOMING_ACTIVE_STATUSES},
	"Pending Payable":        {"cheque_type": "Outgoing", "statuses": OUTGOING_ACTIVE_STATUSES},
	"Bounced Incoming":       {"cheque_type": "Incoming", "status_equals": "Bounced"},
	"Bounced Outgoing":       {"cheque_type": "Outgoing", "status_equals": "Bounced"},
	"Endorsed":               {"cheque_type": "Incoming", "status_equals": "Endorsed"},
}

OVERDUE_CARDS = ("Overdue Incoming", "Overdue Outgoing")
SUM_CARDS = {"Pending Receivable", "Pending Payable"}

DASHBOARD_CHARTS = {
	# §3.1.4
	"Cheque Status Distribution": {
		"chart_type": "Group By",
		"group_by_type": "Sum",
		"group_by_based_on": "status",
		"aggregate_function_based_on": "amount",
	},
	# §3.1.5 — due_date, not issue_date: issue_date is optional and often blank,
	# due_date is mandatory so every cheque plots.
	"Cheques Over Time": {
		"chart_type": "Sum",
		"based_on": "due_date",
		"value_based_on": "amount",
	},
}


class FixtureMismatch(AssertionError):
	pass


def _filters(raw):
	if not raw:
		return []
	return json.loads(raw) if isinstance(raw, str) else raw


def _clause(filters, fieldname, operator=None):
	for row in filters:
		if len(row) >= 3 and row[1] == fieldname and (operator is None or row[2] == operator):
			return row
	return None


def _check_card(name, spec, filters_json, dynamic_filters_json, function, aggregate_on):
	problems = []
	filters = _filters(filters_json)
	dynamic = _filters(dynamic_filters_json)

	type_clause = _clause(filters, "cheque_type", "=")
	if not type_clause or type_clause[3] != spec["cheque_type"]:
		problems.append(f"cheque_type filter is {type_clause!r}, expected {spec['cheque_type']!r}")

	# §3.1.3 — the Bounced cards were missing docstatus, so they counted drafts
	# and cancelled cheques too.
	docstatus_clause = _clause(filters, "docstatus", "=")
	if not docstatus_clause or docstatus_clause[3] != 1:
		problems.append(f"docstatus filter is {docstatus_clause!r}, expected == 1")

	if "statuses" in spec:
		status_clause = _clause(filters, "status", "in")
		if not status_clause:
			problems.append("no status 'in' filter")
		elif list(status_clause[3]) != spec["statuses"]:
			problems.append(f"status filter is {status_clause[3]!r}, expected {spec['statuses']!r}")
	else:
		status_clause = _clause(filters, "status", "=")
		if not status_clause or status_clause[3] != spec["status_equals"]:
			problems.append(f"status filter is {status_clause!r}, expected == {spec['status_equals']!r}")

	if name in OVERDUE_CARDS:
		if _clause(filters, "due_date"):
			problems.append("due_date must not appear in filters_json (§3.1.2)")
		if dynamic != OVERDUE_DYNAMIC_FILTER:
			problems.append(
				f"dynamic_filters_json is {dynamic!r}, expected {OVERDUE_DYNAMIC_FILTER!r}"
			)

	if name in SUM_CARDS:
		if function != "Sum":
			problems.append(f"function is {function!r}, expected 'Sum'")
		if aggregate_on != "amount":
			problems.append(f"aggregate_function_based_on is {aggregate_on!r}, expected 'amount'")

	return problems


def assert_number_cards():
	"""All 10 cards exist in the database and match §3.1."""
	failures = {}
	for name, spec in NUMBER_CARDS.items():
		if not frappe.db.exists("Number Card", name):
			failures[name] = ["missing from the site"]
			continue

		row = frappe.db.get_value(
			"Number Card",
			name,
			["filters_json", "dynamic_filters_json", "function", "aggregate_function_based_on"],
			as_dict=True,
		)
		problems = _check_card(
			name,
			spec,
			row.filters_json,
			row.dynamic_filters_json,
			row.function,
			row.aggregate_function_based_on,
		)
		if problems:
			failures[name] = problems

	return failures


def assert_dashboard_charts():
	failures = {}
	for name, expected in DASHBOARD_CHARTS.items():
		if not frappe.db.exists("Dashboard Chart", name):
			failures[name] = ["missing from the site"]
			continue

		row = frappe.db.get_value("Dashboard Chart", name, list(expected), as_dict=True)
		problems = [
			f"{field} is {row.get(field)!r}, expected {value!r}"
			for field, value in expected.items()
			if row.get(field) != value
		]
		if problems:
			failures[name] = problems

	return failures


def assert_shipped_fixtures_match():
	"""The on-disk fixtures must satisfy the same spec as the database.

	Without this, a wrong fixture file plus a correct database would pass — and
	the next `bench migrate` would force-import the wrong file straight over the
	top (frappe/utils/fixtures.py:28-43).
	"""
	failures = {}
	base = os.path.join(frappe.get_app_path("cheque_tracker"), "fixtures")

	with open(os.path.join(base, "number_card.json")) as handle:
		cards = {card["name"]: card for card in json.load(handle)}

	for name, spec in NUMBER_CARDS.items():
		card = cards.get(name)
		if not card:
			failures[f"number_card.json::{name}"] = ["missing from the shipped fixture"]
			continue
		problems = _check_card(
			name,
			spec,
			card.get("filters_json"),
			card.get("dynamic_filters_json"),
			card.get("function"),
			card.get("aggregate_function_based_on"),
		)
		if problems:
			failures[f"number_card.json::{name}"] = problems

	with open(os.path.join(base, "dashboard_chart.json")) as handle:
		charts = {chart["name"]: chart for chart in json.load(handle)}

	for name, expected in DASHBOARD_CHARTS.items():
		chart = charts.get(name)
		if not chart:
			failures[f"dashboard_chart.json::{name}"] = ["missing from the shipped fixture"]
			continue
		problems = [
			f"{field} is {chart.get(field)!r}, expected {value!r}"
			for field, value in expected.items()
			if chart.get(field) != value
		]
		if problems:
			failures[f"dashboard_chart.json::{name}"] = problems

	return failures


def collect_failures():
	failures = {}
	failures.update(assert_number_cards())
	failures.update(assert_dashboard_charts())
	failures.update(assert_shipped_fixtures_match())
	return failures


def run():
	"""bench execute entry point — raises FixtureMismatch on any discrepancy."""
	failures = collect_failures()

	total = len(NUMBER_CARDS) + len(DASHBOARD_CHARTS)
	if failures:
		lines = [f"{name}: {'; '.join(problems)}" for name, problems in sorted(failures.items())]
		message = "\n  ".join(lines)
		print(f"\n[verify_fixtures] FAIL — {len(failures)} of {total} checks failed:\n  {message}\n")
		raise FixtureMismatch(message)

	print(
		f"[verify_fixtures] OK — {len(NUMBER_CARDS)} Number Cards and "
		f"{len(DASHBOARD_CHARTS)} Dashboard Charts match §3.1, "
		"in the database and in the shipped fixtures."
	)
	return {"checked": total, "failures": 0}
