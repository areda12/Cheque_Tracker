"""Assert that `translations/ar.csv` shadows nothing in frappe or erpnext core.

Frappe's translation namespace is flat and site-wide: an entry in an app's
`ar.csv` overrides that source string for **every** app on the site. So a
well-meaning correction to, say, "Clear" silently changes the Arabic for
ERPNext's Clear-a-field buttons too, and "Issue" — right for issuing a cheque —
becomes wrong for Material Issue and the Issue doctype.

The rule this file enforces is therefore: **cheque_tracker only translates
strings that frappe and erpnext do not**. Anything they already ship is their
call to make, right or wrong; ours to leave alone.

Runnable as a check as well as a test:

    bench --site <site> execute cheque_tracker.tests.verify_translations.run
"""

import csv
import os

import frappe

# Core catalogues. v16 core apps moved from CSV to gettext, so these are .po
# files — `frappe/locale/<lang>.po`, not `frappe/translations/<lang>.csv`.
CORE_APPS = ("frappe", "erpnext")
LANG = "ar"


class TranslationCollision(AssertionError):
	pass


def _unquote(raw):
	raw = raw.strip()
	if raw.startswith('"') and raw.endswith('"'):
		raw = raw[1:-1]
	return raw.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")


def _read_msgids(path):
	"""Every msgid in a .po file, including multi-line ones."""
	msgids = set()
	current = []
	inside = False

	with open(path, encoding="utf-8") as handle:
		for line in handle:
			line = line.rstrip("\n")
			if line.startswith("msgid "):
				if inside and current:
					msgids.add("".join(current))
				current, inside = [_unquote(line[len("msgid ") :])], True
			elif inside and line.lstrip().startswith('"'):
				current.append(_unquote(line))
			elif inside:
				if current:
					msgids.add("".join(current))
				current, inside = [], False

	if inside and current:
		msgids.add("".join(current))

	msgids.discard("")
	return msgids


def core_source_strings(lang=LANG):
	"""Union of every source string frappe and erpnext already translate."""
	strings = set()
	for app in CORE_APPS:
		try:
			app_path = frappe.get_app_path(app)
		except Exception:
			continue
		catalogue = os.path.join(app_path, "locale", f"{lang}.po")
		if os.path.exists(catalogue):
			strings |= _read_msgids(catalogue)
	return strings


def app_translation_rows(lang=LANG):
	path = os.path.join(frappe.get_app_path("cheque_tracker"), "translations", f"{lang}.csv")
	if not os.path.exists(path):
		return []
	with open(path, encoding="utf-8") as handle:
		return [row for row in csv.reader(handle) if row and row[0].strip()]


def find_collisions(lang=LANG):
	"""Source strings we translate that core already translates."""
	core = core_source_strings(lang)
	if not core:
		# No catalogue to compare against — report it rather than passing blind.
		return None
	return sorted({row[0] for row in app_translation_rows(lang)} & core)


def run():
	"""bench execute entry point — raises on any collision."""
	rows = app_translation_rows()
	core = core_source_strings()

	if not core:
		raise TranslationCollision(
			"No core Arabic catalogue found for frappe/erpnext — cannot verify that "
			"cheque_tracker's translations shadow nothing. Check "
			"<app>/locale/ar.po exists."
		)

	collisions = find_collisions()
	if collisions:
		listing = "\n  ".join(collisions)
		print(
			f"\n[verify_translations] FAIL — {len(collisions)} entr(ies) shadow "
			f"frappe/erpnext core:\n  {listing}\n"
		)
		raise TranslationCollision(
			f"{len(collisions)} translation(s) shadow core: {collisions}"
		)

	print(
		f"[verify_translations] OK — {len(rows)} app-unique entries, "
		f"none shadowing the {len(core)} strings frappe/erpnext already translate."
	)
	return {"entries": len(rows), "core_strings": len(core), "collisions": 0}
