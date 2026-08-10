"""v1.1.6 — repair the Number Card / Dashboard Chart fixtures on deployed sites.

Shipping corrected JSON (BUILD_INSTRUCTIONS §3.1) is enough for a *fresh* install,
because core `sync_fixtures()` force-imports every file in `cheque_tracker/fixtures/`
on each migrate (delete-then-reinsert, frappe/modules/import_file.py:229-238).  It is
NOT enough for a site whose records were hot-patched by hand, and it cannot fix
"Cheques Over Time" at all: `Dashboard Chart.chart_type` is `set_only_once`
(frappe/desk/doctype/dashboard_chart/dashboard_chart.json:64), so flipping Count → Sum
raises `frappe.CannotChangeConstantError`.  That chart must be deleted and recreated
under the same name — the Workspace `charts` child row references it by name, so the
name has to be preserved.

The canonical values are read from the shipped fixture files rather than restated here,
so this patch and the fixtures can never drift apart.

Runs pre_model_sync (patches.txt carries no section headers — see
frappe/modules/patch_handler.py:106-110), which is safe: every field touched already
exists on core Number Card / Dashboard Chart in v16.
"""

import json
import os

import frappe

# Fields on Dashboard Chart that cannot be changed after insert.
_SET_ONLY_ONCE = ("chart_type", "document_type", "report_name")

# Fields this patch owns on each doctype.  Anything not listed is left alone.
_CARD_FIELDS = (
	"filters_json",
	"dynamic_filters_json",
	"function",
	"aggregate_function_based_on",
)
_CHART_FIELDS = (
	"group_by_type",
	"group_by_based_on",
	"aggregate_function_based_on",
	"based_on",
	"value_based_on",
	"filters_json",
	"timespan",
	"time_interval",
	"timeseries",
	"type",
	"currency",
)


def execute():
	repaired = _repair_number_cards()
	repaired += _repair_dashboard_charts()
	if repaired:
		frappe.clear_cache()


def _load_fixture(filename):
	path = os.path.join(frappe.get_app_path("cheque_tracker"), "fixtures", filename)
	if not os.path.exists(path):
		return []
	with open(path) as handle:
		return json.load(handle)


def _norm(value):
	"""Treat None and "" as the same absent value when diffing."""
	return value if value not in ("", None) else None


def _repair_number_cards():
	"""Update in place — Number Card has no set-once fields."""
	repaired = 0
	for card in _load_fixture("number_card.json"):
		name = card.get("name")
		if not name or not frappe.db.exists("Number Card", name):
			# Absent on this site: sync_fixtures will create it from the same JSON.
			continue

		desired = {field: _norm(card.get(field)) for field in _CARD_FIELDS}
		current = frappe.db.get_value("Number Card", name, list(desired), as_dict=True) or {}
		changed = {
			field: value for field, value in desired.items() if _norm(current.get(field)) != value
		}
		if not changed:
			continue

		fields = sorted(changed)
		frappe.db.set_value("Number Card", name, changed)
		repaired += 1
		print(f"[cheque_tracker] repaired Number Card {name}: {fields}")

	return repaired


def _repair_dashboard_charts():
	repaired = 0
	for chart in _load_fixture("dashboard_chart.json"):
		name = chart.get("name")
		if not name or not frappe.db.exists("Dashboard Chart", name):
			continue

		locked = {field: chart.get(field) for field in _SET_ONLY_ONCE if chart.get(field)}
		current_locked = frappe.db.get_value("Dashboard Chart", name, list(locked), as_dict=True) or {}

		if any(_norm(current_locked.get(f)) != _norm(v) for f, v in locked.items()):
			_recreate_chart(name, chart)
			repaired += 1
			continue

		desired = {field: _norm(chart.get(field)) for field in _CHART_FIELDS}
		current = frappe.db.get_value("Dashboard Chart", name, list(desired), as_dict=True) or {}
		changed = {
			field: value for field, value in desired.items() if _norm(current.get(field)) != value
		}
		if not changed:
			continue

		fields = sorted(changed)
		frappe.db.set_value("Dashboard Chart", name, changed)
		repaired += 1
		print(f"[cheque_tracker] repaired Dashboard Chart {name}: {fields}")

	return repaired


def _recreate_chart(name, fixture):
	"""Delete + reinsert under the same name (set-once field changed).

	`force=True` bypasses the link-existence check so the Workspace `charts` child
	row referencing this chart does not block the delete; the row keeps pointing at
	the same name, which the reinsert restores (autoname is `field:chart_name`).
	"""
	frappe.delete_doc(
		"Dashboard Chart",
		name,
		force=True,
		ignore_permissions=True,
		ignore_missing=True,
	)

	doc = frappe.get_doc(dict(fixture))
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	if doc.name != name:
		frappe.throw(
			f"Dashboard Chart recreated as {doc.name!r}, expected {name!r} — "
			"the Workspace chart reference would dangle."
		)

	print(f"[cheque_tracker] recreated Dashboard Chart {name} (set-once field changed)")
