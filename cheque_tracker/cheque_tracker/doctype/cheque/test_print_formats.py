# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""§5.5 — acceptance for the three v1.3 bilingual print formats.

Two things are under test and they fail in different ways:

1. The Arabic tafqeet helper, as pure arithmetic. A wrong noun form here is a
   grammatical error on a legal receipt that nobody would notice until a bank
   rejected the paper, so the agreement table is asserted case by case rather
   than smoke-tested at one value.

2. The formats themselves, rendered through `frappe.get_print` — the same call
   the desk print view makes. A Jinja error in a print format does not surface
   until someone clicks Print, which is exactly when it costs the most.

The amount-in-words helper reaches Jinja through a `hooks.py` `jinja.methods`
entry that this branch does not own. The tests therefore cover BOTH sides of
that: rendering with the global registered (proving the call site is correct for
when the hook lands) and rendering without it (proving the guard degrades to
figures instead of 500-ing).
"""

import glob
import os
import re
import shutil

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from cheque_tracker.cheque_tracker.doctype.cheque.arabic_words import (
    cheque_amount_in_arabic_words,
    integer_in_words,
)
from cheque_tracker.tests import e2e
from cheque_tracker.tests.utils import get_test_env

# The name the `jinja.methods` hook entry will publish. Kept in one place so the
# tests and the templates cannot drift apart.
JINJA_GLOBAL = "cheque_amount_in_arabic_words"

RECEIPT_VOUCHER = "Cheque Receipt Voucher"
CUSTODY_HANDOVER = "Custody Handover"
DEPOSIT_SLIP = "Deposit Slip"

NAVY = "#2F2E78"
CYAN = "#28ABE2"


def _register_jinja_global():
    """Publish the helper the way hooks.py `jinja.methods` would.

    frappe.utils.jinja.get_jinja_hooks() copies each hooked function into
    jenv.globals under its own __name__; this reproduces that exactly, so the
    templates are exercised against the real call signature.
    """
    frappe.get_jenv().globals[JINJA_GLOBAL] = cheque_amount_in_arabic_words


def _unregister_jinja_global():
    frappe.get_jenv().globals.pop(JINJA_GLOBAL, None)


def _print(doctype, name, print_format):
    return frappe.get_print(doctype, name, print_format=print_format, no_letterhead=1)


def _assert_no_jinja_leftovers(case, html, print_format):
    """An undefined name renders as literal `{{ ... }}` under DebugUndefined
    rather than raising, so a broken guard would otherwise pass silently."""
    for marker in ("{{", "{%", "Traceback (most recent call last)"):
        case.assertNotIn(
            marker, html, f"{print_format} left {marker!r} in its output — the template did not render"
        )


class TestArabicAmountInWords(FrappeTestCase):
    """Pure arithmetic — no site data touched."""

    def test_zero(self):
        self.assertEqual(cheque_amount_in_arabic_words(0, "EGP"), "صفر جنيه")
        self.assertEqual(cheque_amount_in_arabic_words(0.0, "EGP"), "صفر جنيه")

    def test_one_uses_the_singular_with_the_noun_leading(self):
        self.assertEqual(cheque_amount_in_arabic_words(1, "EGP"), "جنيه واحد")

    def test_two_uses_the_dual_and_drops_the_numeral(self):
        self.assertEqual(cheque_amount_in_arabic_words(2, "EGP"), "جنيهان")

    def test_eleven_takes_the_accusative_singular(self):
        self.assertEqual(cheque_amount_in_arabic_words(11, "EGP"), "أحد عشر جنيهاً")

    def test_hundred_takes_the_singular(self):
        self.assertEqual(cheque_amount_in_arabic_words(100, "EGP"), "مائة جنيه")

    def test_thousand_takes_the_singular(self):
        self.assertEqual(cheque_amount_in_arabic_words(1000, "EGP"), "ألف جنيه")

    def test_two_thousand_uses_the_dual_in_construct_state(self):
        # "ألفان" loses its nun before the counted noun: ألفا جنيه, not ألفان جنيه.
        self.assertEqual(cheque_amount_in_arabic_words(2000, "EGP"), "ألفا جنيه")

    def test_upper_boundary_with_piastres(self):
        self.assertEqual(
            cheque_amount_in_arabic_words(999999999.99, "EGP"),
            "تسعمائة وتسعة وتسعون مليوناً وتسعمائة وتسعة وتسعون ألفاً "
            "وتسعمائة وتسعة وتسعون جنيهاً وتسعة وتسعون قرشاً",
        )

    def test_amount_with_piastres(self):
        self.assertEqual(
            cheque_amount_in_arabic_words(5000.75, "EGP"),
            "خمسة آلاف جنيه وخمسة وسبعون قرشاً",
        )

    def test_piastre_agreement_mirrors_the_pound_agreement(self):
        for value, expected in (
            (0.01, "قرش واحد"),
            (0.02, "قرشان"),
            (0.03, "ثلاثة قروش"),
            (0.11, "أحد عشر قرشاً"),
            (0.50, "خمسون قرشاً"),
        ):
            with self.subTest(value=value):
                self.assertEqual(cheque_amount_in_arabic_words(value, "EGP"), expected)

    def test_full_agreement_table(self):
        """One case per branch of the تمييز rules, plus the scale words."""
        cases = {
            3: "ثلاثة جنيهات",
            10: "عشرة جنيهات",
            20: "عشرون جنيهاً",
            21: "واحد وعشرون جنيهاً",
            22: "اثنان وعشرون جنيهاً",
            99: "تسعة وتسعون جنيهاً",
            101: "مائة وجنيه واحد",
            102: "مائة وجنيهان",
            110: "مائة وعشرة جنيهات",
            200: "مائتا جنيه",
            300: "ثلاثمائة جنيه",
            1001: "ألف وجنيه واحد",
            1002: "ألف وجنيهان",
            3000: "ثلاثة آلاف جنيه",
            11000: "أحد عشر ألف جنيه",
            20119: "عشرون ألفاً ومائة وتسعة عشر جنيهاً",
            73000: "ثلاثة وسبعون ألف جنيه",
            200000: "مائتا ألف جنيه",
            1000000: "مليون جنيه",
            2000000: "مليونا جنيه",
            3000000: "ثلاثة ملايين جنيه",
            1234567: "مليون ومائتان وأربعة وثلاثون ألفاً وخمسمائة وسبعة وستون جنيهاً",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(cheque_amount_in_arabic_words(value, "EGP"), expected)

    def test_waw_joins_the_last_term(self):
        words = cheque_amount_in_arabic_words(1234.56, "EGP")
        self.assertEqual(words, "ألف ومائتان وأربعة وثلاثون جنيهاً وستة وخمسون قرشاً")
        self.assertIn("وستة وخمسون قرشاً", words)

    def test_rounds_half_up_to_piastres(self):
        self.assertEqual(cheque_amount_in_arabic_words(0.005, "EGP"), "قرش واحد")
        self.assertEqual(cheque_amount_in_arabic_words(1.004, "EGP"), "جنيه واحد")

    def test_other_supported_currency(self):
        self.assertEqual(cheque_amount_in_arabic_words(5.25, "USD"), "خمسة دولارات وخمسة وعشرون سنتاً")

    def test_unsupported_currency_returns_empty_rather_than_guessing(self):
        # Printing a guessed noun on a legal receipt is worse than printing the
        # figure alone, so the format is told to fall back instead.
        self.assertEqual(cheque_amount_in_arabic_words(100, "JPY"), "")

    def test_currency_defaults_to_egp(self):
        self.assertEqual(cheque_amount_in_arabic_words(12), "اثنا عشر جنيهاً")

    def test_never_raises_on_junk_input(self):
        for junk in (None, "", "abc", object()):
            with self.subTest(junk=junk):
                self.assertIsInstance(cheque_amount_in_arabic_words(junk, "EGP"), str)

    def test_negative_amounts_are_marked_not_silently_flipped(self):
        self.assertEqual(cheque_amount_in_arabic_words(-2, "EGP"), "سالب جنيهان")

    def test_integer_in_words_boundaries(self):
        self.assertEqual(integer_in_words(0), "صفر")
        self.assertEqual(integer_in_words(1), "واحد")
        self.assertEqual(integer_in_words(2), "اثنان")
        self.assertEqual(integer_in_words(11), "أحد عشر")
        self.assertEqual(integer_in_words(100), "مائة")
        self.assertEqual(integer_in_words(1000), "ألف")
        self.assertEqual(integer_in_words(2000), "ألفان")
        self.assertEqual(
            integer_in_words(999999999),
            "تسعمائة وتسعة وتسعون مليوناً وتسعمائة وتسعة وتسعون ألفاً وتسعمائة وتسعة وتسعون",
        )
        # Out of range must be empty, never a partial number.
        self.assertEqual(integer_in_words(-1), "")
        self.assertEqual(integer_in_words(10**12), "")


class TestPrintFormatDefinitions(FrappeTestCase):
    """§5.1 requires these to ship as app files, not database-only records."""

    def test_all_three_ship_as_standard_app_files(self):
        expected = {
            RECEIPT_VOUCHER: ("Cheque", "cheque_receipt_voucher"),
            CUSTODY_HANDOVER: ("Cheque", "custody_handover"),
            DEPOSIT_SLIP: ("Cheque Batch", "deposit_slip"),
        }
        base = frappe.get_app_path("cheque_tracker", "cheque_tracker", "print_format")

        for name, (doc_type, folder) in expected.items():
            with self.subTest(print_format=name):
                self.assertTrue(frappe.db.exists("Print Format", name), f"{name} was not imported")
                fmt = frappe.get_doc("Print Format", name)
                self.assertEqual(fmt.standard, "Yes")
                self.assertEqual(fmt.module, "Cheque Tracker")
                self.assertEqual(fmt.doc_type, doc_type)
                self.assertEqual(fmt.print_format_type, "Jinja")
                self.assertFalse(fmt.disabled)
                self.assertFalse(fmt.raw_printing)
                self.assertFalse(fmt.custom_format)

                # frappe.www.printview.get_print_format reads the sibling .html
                # for a non-custom module, which is why `html` may stay empty.
                for extension in ("json", "html"):
                    path = os.path.join(base, folder, f"{folder}.{extension}")
                    self.assertTrue(os.path.exists(path), f"missing {path}")

    def test_page_sizes_match_the_spec(self):
        """A5 for the two vouchers, A4 for the slip.

        The v16 Print Format doctype has no page_size field; the page size is
        declared on the bare `.print-format` selector, which
        frappe.utils.pdf.read_options_from_html lifts into wkhtmltopdf options.
        """
        base = frappe.get_app_path("cheque_tracker", "cheque_tracker", "print_format")
        for folder, size in (
            ("cheque_receipt_voucher", "A5"),
            ("custody_handover", "A5"),
            ("deposit_slip", "A4"),
        ):
            with self.subTest(folder=folder):
                with open(os.path.join(base, folder, f"{folder}.html")) as handle:
                    source = handle.read()
                self.assertIn(f".print-format {{ page-size: {size};", source)

    def test_no_external_assets_are_fetched_at_print_time(self):
        """PDF rendering has no network — a remote font or image would stall."""
        base = frappe.get_app_path("cheque_tracker", "cheque_tracker", "print_format")
        for folder in ("cheque_receipt_voucher", "custody_handover", "deposit_slip"):
            with self.subTest(folder=folder):
                with open(os.path.join(base, folder, f"{folder}.html")) as handle:
                    source = handle.read()
                for marker in ("@import", "http://", "https://", "<script"):
                    self.assertNotIn(marker, source, f"{folder} pulls in {marker}")


class ChequePrintFormatTestCase(FrappeTestCase):
    """Shared fixture: the canonical env plus the jinja global the hook will add."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = get_test_env()
        _register_jinja_global()
        cls.addClassCleanup(_unregister_jinja_global)


class TestChequeReceiptVoucher(ChequePrintFormatTestCase):

    def test_renders_with_arabic_particulars_and_amount_in_words(self):
        cheque = e2e.make_incoming(self.env, amount=73000.25)
        html = _print("Cheque", cheque.name, RECEIPT_VOUCHER)

        _assert_no_jinja_leftovers(self, html, RECEIPT_VOUCHER)

        # Bilingual headings
        self.assertIn("إيصال استلام شيك", html)
        self.assertIn("CHEQUE RECEIPT VOUCHER", html)

        # RTL wrapper and the EEI design tokens
        self.assertIn("direction: rtl", html)
        self.assertIn(NAVY, html)
        self.assertIn(CYAN, html)
        self.assertIn("lh-rule", html)
        self.assertIn('class="docno-box"', html)
        self.assertIn('class="sigs"', html)

        # Particulars required by §5.1
        self.assertIn(cheque.name, html)
        self.assertIn(cheque.cheque_no, html)
        self.assertIn(self.env["drawee_bank"], html)
        self.assertIn(self.env["customer"], html)
        self.assertIn(frappe.utils.formatdate(cheque.due_date, "dd/MM/yyyy"), html)
        self.assertIn(frappe.utils.fmt_money(73000.25, 2, cheque.currency), html)

        # Amount in Arabic words, and the two signature blocks
        self.assertIn("ثلاثة وسبعون ألف جنيه وخمسة وعشرون قرشاً", html)
        self.assertIn("المُسلِّم", html)
        self.assertIn("أمين الخزينة", html)

    def test_shows_the_linked_payment_entry(self):
        cheque = e2e.make_incoming(self.env, submit=False)
        payment_entry = self._make_draft_payment_entry(cheque.amount)
        cheque.reference_doctype = "Payment Entry"
        cheque.reference_name = payment_entry
        cheque.flags.ignore_permissions = True
        cheque.save(ignore_permissions=True)
        cheque.submit()

        html = _print("Cheque", cheque.name, RECEIPT_VOUCHER)
        _assert_no_jinja_leftovers(self, html, RECEIPT_VOUCHER)
        self.assertIn("قيد السداد", html)
        self.assertIn(payment_entry, html)

    def test_degrades_to_figures_when_the_jinja_hook_is_absent(self):
        """The helper is published by a hooks.py entry this branch does not own.

        Without it the global is undefined, and the format must print the figure
        rather than raise — a voucher missing its words can be reprinted, a 500
        at the print button cannot.
        """
        cheque = e2e.make_incoming(self.env, amount=1234.56)
        _unregister_jinja_global()
        try:
            html = _print("Cheque", cheque.name, RECEIPT_VOUCHER)
        finally:
            _register_jinja_global()

        _assert_no_jinja_leftovers(self, html, RECEIPT_VOUCHER)
        self.assertNotIn("ألف ومائتان وأربعة وثلاثون جنيهاً", html)
        self.assertIn("فقط:", html)
        self.assertIn(frappe.utils.fmt_money(1234.56, 2, cheque.currency), html)

    def _make_draft_payment_entry(self, amount):
        payment_entry = frappe.new_doc("Payment Entry")
        payment_entry.update(
            {
                "payment_type": "Receive",
                "company": self.env["company"],
                "posting_date": today(),
                "party_type": "Customer",
                "party": self.env["customer"],
                "paid_amount": amount,
                "received_amount": amount,
                "source_exchange_rate": 1,
                "target_exchange_rate": 1,
                "paid_from": self.env["debtors"],
                "paid_to": self.env["bank_gl_account"],
                "reference_no": frappe.generate_hash(length=8),
                "reference_date": today(),
            }
        )
        payment_entry.flags.ignore_permissions = True
        payment_entry.insert(ignore_permissions=True)
        return payment_entry.name


class TestCustodyHandover(ChequePrintFormatTestCase):

    def test_renders_internal_handover_with_two_signature_blocks(self):
        cheque = e2e.make_incoming(self.env, amount=5000.75)
        cheque.hand_over(self.env["treasury_user"], location="خزينة المقر الرئيسي")
        cheque.reload()

        html = _print("Cheque", cheque.name, CUSTODY_HANDOVER)
        _assert_no_jinja_leftovers(self, html, CUSTODY_HANDOVER)

        self.assertIn("محضر تسليم شيك", html)
        self.assertIn("CHEQUE CUSTODY HANDOVER", html)
        self.assertIn("direction: rtl", html)
        self.assertIn(NAVY, html)

        # From / To, read off the Cheque Event trail
        self.assertIn("المُسلِّم", html)
        self.assertIn("المُستلِم", html)
        self.assertIn(frappe.utils.get_fullname(self.env["treasury_user"]), html)
        self.assertIn("موظف — Internal", html)
        self.assertIn("خزينة المقر الرئيسي", html)

        # Cheque particulars + amount in words
        self.assertIn(cheque.cheque_no, html)
        self.assertIn(self.env["drawee_bank"], html)
        self.assertIn(frappe.utils.fmt_money(5000.75, 2, cheque.currency), html)
        self.assertIn("خمسة آلاف جنيه وخمسة وسبعون قرشاً", html)

        # Exactly two signature boxes
        self.assertEqual(html.count('class="sig-box"'), 2)

    def test_renders_an_external_holder(self):
        """current_holder is a User link and the controller clears it once
        custody leaves the company, so an external handover must fall back to
        external_holder or the record names nobody."""
        cheque = e2e.make_outgoing(self.env, amount=20119)
        e2e.set_fields(cheque, external_holder="مندوب شركة الكهرباء")
        e2e.act(cheque, "Hand Over")

        html = _print("Cheque", cheque.name, CUSTODY_HANDOVER)
        _assert_no_jinja_leftovers(self, html, CUSTODY_HANDOVER)

        self.assertIn("مندوب شركة الكهرباء", html)
        self.assertIn("جهة خارجية — External", html)
        self.assertIn("عشرون ألفاً ومائة وتسعة عشر جنيهاً", html)


class TestDepositSlip(ChequePrintFormatTestCase):

    def _make_batch(self, amounts):
        cheques = [e2e.make_incoming(self.env, amount=amount) for amount in amounts]
        batch = frappe.new_doc("Cheque Batch")
        batch.batch_date = today()
        batch.company = self.env["company"]
        batch.bank_account = self.env["bank_account"]
        for cheque in cheques:
            batch.append("items", {"cheque": cheque.name})
        batch.flags.ignore_permissions = True
        batch.insert(ignore_permissions=True)
        batch.submit()
        return batch, cheques

    def test_renders_member_cheques_with_correct_totals(self):
        amounts = [1000.50, 2500.25, 300.00]
        batch, cheques = self._make_batch(amounts)

        html = _print("Cheque Batch", batch.name, DEPOSIT_SLIP)
        _assert_no_jinja_leftovers(self, html, DEPOSIT_SLIP)

        self.assertIn("قسيمة إيداع", html)
        self.assertIn("CHEQUE DEPOSIT SLIP", html)
        self.assertIn(batch.name, html)

        # Bank + account header
        bank = frappe.db.get_value("Bank Account", self.env["bank_account"], "bank")
        self.assertIn(bank, html)
        self.assertIn(self.env["bank_account"], html)
        self.assertIn(frappe.utils.formatdate(batch.batch_date, "dd/MM/yyyy"), html)

        # Every member cheque: no, drawer, drawee bank, due date, amount
        currency = frappe.db.get_value("Company", self.env["company"], "default_currency")
        for cheque in cheques:
            self.assertIn(cheque.cheque_no, html)
            self.assertIn(frappe.utils.formatdate(cheque.due_date, "dd/MM/yyyy"), html)
            self.assertIn(frappe.utils.fmt_money(cheque.amount, 2, currency), html)
        self.assertIn(cheques[0].drawer_name, html)
        self.assertIn(self.env["drawee_bank"], html)

        # Count and sum — the printed total must be the sum of the printed lines
        # and must also agree with what the controller stored.
        total = sum(amounts)
        self.assertEqual(flt(batch.total_amount, 2), flt(total, 2))
        self.assertEqual(batch.total_cheques, len(amounts))
        self.assertIn(f"عدد الشيكات: {len(amounts)}", html)
        self.assertIn(frappe.utils.fmt_money(total, 2, currency), html)
        self.assertIn("ثلاثة آلاف وثمانمائة جنيه وخمسة وسبعون قرشاً", html)

        # Signature blocks named in EEI_PRINT_DESIGN_REFERENCE §2
        self.assertIn("أمين الخزينة", html)
        self.assertIn("موظف البنك", html)
        self.assertEqual(html.count('class="sig-box"'), 2)

    def test_flags_a_stored_total_that_disagrees_with_the_lines(self):
        """A stale stored total is an accounting error, not a rounding quirk —
        the teller must see it on the paper rather than reconcile blind."""
        batch, _ = self._make_batch([1000.00, 2000.00])
        frappe.db.set_value("Cheque Batch", batch.name, "total_amount", 9999.00, update_modified=False)

        html = _print("Cheque Batch", batch.name, DEPOSIT_SLIP)
        _assert_no_jinja_leftovers(self, html, DEPOSIT_SLIP)
        self.assertIn("لا يطابق مجموع الشيكات المدرجة", html)

    def test_no_discrepancy_banner_on_a_healthy_batch(self):
        batch, _ = self._make_batch([1000.00, 2000.00])
        html = _print("Cheque Batch", batch.name, DEPOSIT_SLIP)
        self.assertNotIn("لا يطابق مجموع الشيكات المدرجة", html)


class TestPrintFormatsAsPdf(ChequePrintFormatTestCase):
    """EEI_PRINT_DESIGN_REFERENCE §3 asks for a PDF render too. wkhtmltopdf is
    not installed on every bench, so this skips rather than fails there — the
    HTML assertions above are the load-bearing ones."""

    def test_all_three_produce_a_pdf(self):
        if not shutil.which("wkhtmltopdf"):
            self.skipTest("wkhtmltopdf is not installed on this bench")

        cheque = e2e.make_incoming(self.env, amount=4321.05)
        batch = frappe.new_doc("Cheque Batch")
        batch.batch_date = today()
        batch.company = self.env["company"]
        batch.bank_account = self.env["bank_account"]
        batch.append("items", {"cheque": cheque.name})
        batch.flags.ignore_permissions = True
        batch.insert(ignore_permissions=True)
        batch.submit()

        for doctype, name, print_format in (
            ("Cheque", cheque.name, RECEIPT_VOUCHER),
            ("Cheque", cheque.name, CUSTODY_HANDOVER),
            ("Cheque Batch", batch.name, DEPOSIT_SLIP),
        ):
            with self.subTest(print_format=print_format):
                pdf = frappe.get_print(
                    doctype, name, print_format=print_format, as_pdf=True, no_letterhead=1
                )
                self.assertTrue(pdf)
                self.assertTrue(pdf.startswith(b"%PDF"))


# ====================================================================== #
#  v1.3.1 — Cairo is bundled, not fetched                                #
# ====================================================================== #

class TestBundledCairoFont(FrappeTestCase):
    """Frappe Cloud's wkhtmltopdf has no external egress, so a hosted-font
    @import silently fails and the PDF falls back to a Latin face — Arabic then
    renders in whatever the substitution picks. Site-local assets load fine,
    which the letterhead image proves.
    """

    FORMAT_DIR = os.path.join(
        frappe.get_app_path("cheque_tracker"), "cheque_tracker", "print_format"
    )
    FONT_DIR = os.path.join(frappe.get_app_path("cheque_tracker"), "public", "fonts")
    EXPECTED_FACES = ("Cairo-Regular.ttf", "Cairo-SemiBold.ttf", "Cairo-Bold.ttf")

    def _format_html(self):
        paths = glob.glob(os.path.join(self.FORMAT_DIR, "*", "*.html"))
        self.assertEqual(len(paths), 3, f"expected 3 print formats, found {paths}")
        return {os.path.basename(p): open(p, encoding="utf-8").read() for p in paths}

    def test_no_hosted_font_reference_remains(self):
        """Nothing may reach out to a font CDN at render time."""
        for name, html in self._format_html().items():
            for needle in ("fonts.googleapis", "fonts.gstatic", "//fonts.google"):
                self.assertNotIn(needle, html, f"{name} still references {needle}")

    def test_every_format_declares_the_three_faces(self):
        for name, html in self._format_html().items():
            self.assertIn("@font-face", html, f"{name} declares no @font-face")
            for face in self.EXPECTED_FACES:
                self.assertIn(
                    f"/assets/cheque_tracker/fonts/{face}",
                    html,
                    f"{name} does not reference {face}",
                )

    def test_font_files_exist_at_the_referenced_paths(self):
        """A @font-face pointing at a missing file fails exactly as silently as
        the CDN did, so assert the bytes are actually shipped."""
        referenced = set()
        for html in self._format_html().values():
            referenced |= set(re.findall(r"/assets/cheque_tracker/fonts/([A-Za-z0-9\-_.]+)", html))

        self.assertTrue(referenced, "no font assets referenced at all")
        for filename in sorted(referenced):
            path = os.path.join(self.FONT_DIR, filename)
            self.assertTrue(os.path.exists(path), f"{filename} referenced but not shipped ({path})")
            self.assertGreater(os.path.getsize(path), 10_000, f"{filename} looks truncated")

    def test_faces_are_truetype_not_woff(self):
        """wkhtmltopdf embeds QtWebKit, which cannot read woff2."""
        for name, html in self._format_html().items():
            self.assertNotIn("woff", html.lower(), f"{name} references a woff face")
            self.assertIn("format('truetype')", html, f"{name} does not declare truetype")

    def test_shipped_faces_are_static_not_variable(self):
        """A variable TTF renders at one weight under QtWebKit, so SemiBold and
        Bold would be indistinguishable in the PDF."""
        from fontTools.ttLib import TTFont

        for face in self.EXPECTED_FACES:
            path = os.path.join(self.FONT_DIR, face)
            font = TTFont(path, lazy=True)
            self.assertNotIn("fvar", font, f"{face} is a variable font")
            font.close()

    def test_licence_is_shipped(self):
        self.assertTrue(
            os.path.exists(os.path.join(self.FONT_DIR, "OFL.txt")),
            "Cairo is SIL OFL — the licence must ship with the fonts",
        )

    def test_cairo_is_first_in_the_stack_with_fallbacks(self):
        for name, html in self._format_html().items():
            self.assertRegex(
                html,
                r"font-family:\s*'Cairo'\s*,\s*'Segoe UI'\s*,\s*Tahoma",
                f"{name} lost the Cairo-first stack or its fallbacks",
            )
