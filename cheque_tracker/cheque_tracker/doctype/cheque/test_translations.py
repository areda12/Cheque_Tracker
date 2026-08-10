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
MIN_ENTRIES = 200

# Terminology anchors. These are the accounting terms the Egyptian treasury
# staff actually use -- a wrong term here mis-describes a posting, so they are
# pinned rather than left to whoever edits the csv next.
ANCHORS = {
    "Cheque": "شيك",
    "Cheque Book": "دفتر شيكات",
    "Endorsed": "مُظهَّر",
    "Bounced": "مرتجع",
    "Cleared": "محصل",
    "Deposit": "إيداع",
    "Hand Over": "تسليم",
    "Custody": "العهدة",
    "Due Date": "تاريخ الاستحقاق",
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

    def test_every_cheque_status_is_translated(self):
        translations = get_translations_from_csv(LANG, APP)
        statuses = frappe.get_meta("Cheque").get_field("status").options.split("\n")
        missing = [s for s in statuses if s.strip() and not translations.get(s.strip())]
        self.assertEqual(missing, [], "cheque statuses with no Arabic")

    def test_every_workflow_action_is_translated(self):
        translations = get_translations_from_csv(LANG, APP)
        actions = frappe.get_all("Workflow Action Master", pluck="name")
        # Only the actions this app ships; other apps own their own vocabulary.
        ours = {
            "Receive", "Issue", "Move to Safe", "Deposit", "Cash Clear", "Endorse",
            "Hand Over", "Present", "Clear", "Bounce", "Re-deposit", "Return",
            "Replace", "Cancel Cheque",
        }
        missing = [a for a in ours.intersection(actions) if not translations.get(a)]
        self.assertEqual(missing, [], "workflow actions with no Arabic")

    def test_every_bounce_reason_is_translated(self):
        translations = get_translations_from_csv(LANG, APP)
        reasons = frappe.get_meta("Cheque").get_field("bounce_reason").options.split("\n")
        missing = [r for r in reasons if r.strip() and not translations.get(r.strip())]
        self.assertEqual(missing, [], "bounce reasons with no Arabic")

    def test_entries_reach_the_merged_runtime_dictionary(self):
        # get_translations_from_csv reads the file directly; this proves the same
        # strings survive the app merge that actually feeds _() at runtime.
        merged = get_all_translations(LANG)
        for source, expected in ANCHORS.items():
            self.assertEqual(merged.get(source), expected, f"{source!r} lost in the merged dict")
