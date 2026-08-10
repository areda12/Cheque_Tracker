# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.translate import (
    get_all_translations,
    get_translation_dict_from_file,
    get_translations_from_csv,
)

APP = "cheque_tracker"
LANG = "ar"

# Floor, not a target. Raising it every time a string is added turns the test
# into a changelog; it exists to catch a truncated or half-written csv.
MIN_ENTRIES = 180

# Terminology anchors. These are the accounting terms the Egyptian treasury
# staff actually use -- a wrong term here mis-describes a posting, so they are
# pinned rather than left to whoever edits the csv next.
#
# Only strings the app OWNS may appear here. Cheque, Bounced, Cleared, Deposit,
# Due Date and friends were dropped from ar.csv because frappe/erpnext already
# translate them and Frappe's translation namespace is site-wide -- overriding
# them changes the Arabic for every other app on the site. See
# tests/verify_translations.py and DECISIONS.md D10.
ANCHORS = {
    "Cheque Book": "دفتر شيكات",
    "Endorsed": "مُظهَّر",
    "Endorse": "تظهير",
    "Hand Over": "تسليم",
    "Cash Clear": "تحصيل نقدي",
    "Re-deposit": "إعادة إيداع",
    "Bounce Reason": "سبب الارتجاع",
    "Insufficient Funds": "عدم كفاية الرصيد",
    "Cheque Tracker Settings": "إعدادات متابعة الشيكات",
}


def _csv_path():
    return os.path.join(frappe.get_app_path(APP, "translations"), f"{LANG}.csv")


class TestArabicTranslations(FrappeTestCase):

    def test_csv_file_exists(self):
        self.assertTrue(os.path.exists(_csv_path()), f"missing {_csv_path()}")

    def test_file_parses(self):
        # throw=True makes the loader raise on any row that is not a
        # source/translation (/context) tuple, so a malformed line fails here
        # instead of silently dropping a string at runtime.
        translations = get_translation_dict_from_file(_csv_path(), LANG, APP, throw=True)
        self.assertTrue(translations)

    def test_minimum_entry_count(self):
        translations = get_translations_from_csv(LANG, APP)
        self.assertGreaterEqual(
            len(translations),
            MIN_ENTRIES,
            f"only {len(translations)} Arabic entries; the csv looks truncated",
        )

    def test_no_untranslated_entries(self):
        translations = get_translations_from_csv(LANG, APP)
        untranslated = [src for src, tgt in translations.items() if not tgt.strip()]
        self.assertEqual(untranslated, [], "entries left with an empty translation")

    def test_terminology_anchors(self):
        translations = get_translations_from_csv(LANG, APP)
        for source, expected in ANCHORS.items():
            self.assertEqual(translations.get(source), expected, f"wrong Arabic for {source!r}")

    def test_no_entry_shadows_frappe_or_erpnext(self):
        """The rule: cheque_tracker translates only what core does not.

        Frappe's translation namespace is flat, so an entry here rewrites that
        string for every app on the site.
        """
        from cheque_tracker.tests import verify_translations

        collisions = verify_translations.find_collisions(LANG)
        self.assertIsNotNone(
            collisions, "no core Arabic catalogue found - cannot verify shadowing"
        )
        self.assertEqual(
            collisions, [], f"these entries shadow frappe/erpnext core: {collisions}"
        )

    def _covered(self):
        """Sources that have Arabic from SOMEWHERE — ours or core's.

        The completeness checks below exist to catch a new status or action
        shipping with no Arabic at all. Since the app deliberately no longer
        translates strings core already covers, "covered" has to mean the union;
        asserting only against our csv would fail for every string we correctly
        left to core.
        """
        from cheque_tracker.tests import verify_translations

        ours = set(get_translations_from_csv(LANG, APP) or {})
        return ours | verify_translations.core_source_strings(LANG)

    def test_every_cheque_status_is_translated(self):
        covered = self._covered()
        statuses = frappe.get_meta("Cheque").get_field("status").options.split("\n")
        missing = [s.strip() for s in statuses if s.strip() and s.strip() not in covered]
        self.assertEqual(missing, [], "cheque statuses with no Arabic anywhere")

    def test_every_workflow_action_is_translated(self):
        covered = self._covered()
        actions = frappe.get_all("Workflow Action Master", pluck="name")
        # Only the actions this app ships; other apps own their own vocabulary.
        ours = {
            "Receive", "Issue", "Move to Safe", "Deposit", "Cash Clear", "Endorse",
            "Hand Over", "Present", "Clear", "Bounce", "Re-deposit", "Return",
            "Replace", "Cancel Cheque",
        }
        missing = sorted(a for a in ours.intersection(actions) if a not in covered)
        self.assertEqual(missing, [], "workflow actions with no Arabic anywhere")

    def test_every_bounce_reason_is_translated(self):
        covered = self._covered()
        reasons = frappe.get_meta("Cheque").get_field("bounce_reason").options.split("\n")
        missing = [r.strip() for r in reasons if r.strip() and r.strip() not in covered]
        self.assertEqual(missing, [], "bounce reasons with no Arabic anywhere")

    def test_entries_reach_the_merged_runtime_dictionary(self):
        # get_translations_from_csv reads the file directly; this proves the same
        # strings survive the app merge that actually feeds _() at runtime.
        merged = get_all_translations(LANG)
        for source, expected in ANCHORS.items():
            self.assertEqual(merged.get(source), expected, f"{source!r} lost in the merged dict")
