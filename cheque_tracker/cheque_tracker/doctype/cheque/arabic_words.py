# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""Arabic amount-in-words (تفقيط) for the v1.3 cheque print formats.

Why this module exists
----------------------
`frappe.utils.money_in_words` cannot produce Arabic. It composes the phrase as
``_(currency) + in_words(...)``, and ``in_words`` hands the number to num2words
using ``frappe.local.lang`` as the locale — so an English desk session prints
English words onto an Arabic voucher no matter what the document says. Worse,
the fraction noun it appends comes straight from the Currency master, and the
seeded value for EGP on this bench is literally ``Piastre[F]``. A cheque receipt
is a legal acknowledgement in Egypt; the figure carries no weight without the
words, so neither an English phrase nor a stray ``[F]`` is acceptable.

The grammar therefore lives here rather than being patched around a
general-purpose helper.

Grammar (classical تمييز agreement, matching Egyptian bank tafqeet):

* count 1         → noun singular, noun leads       ``جنيه واحد``
* count 2         → noun dual, numeral dropped      ``جنيهان``
* count 3..10     → جمع قلة, numeral leads          ``ثلاثة جنيهات``
* count 11..99    → singular accusative (منصوب)     ``أحد عشر جنيهاً``
* count % 100 = 0 → singular                        ``مائة جنيه``

Dropping the numeral in the 1/2 cases is what keeps 102 from rendering as
"مائة واثنان جنيهان" (which reads "one hundred and two two pounds"); the noun's
own dual form carries the count instead: ``مائة وجنيهان``.

Scale words (ألف/مليون/مليار) follow the same agreement, except that a scale
count of 101 must NOT drop its numeral — "مائة وألف" would read as 1,100 rather
than 101,000 — so the drop applies only to counts of exactly 1 and 2 there.

Every group, including the last, is joined by واو.

Deliberately dependency-free: no `frappe` import, no database access. This runs
inside PDF rendering, where an exception means a blank legal document, and it is
unit-tested as pure arithmetic in ``test_print_formats.py``.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

ZERO = "صفر"
NEGATIVE = "سالب"

ONES = ("", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة")
TEENS = (
    "عشرة",
    "أحد عشر",
    "اثنا عشر",
    "ثلاثة عشر",
    "أربعة عشر",
    "خمسة عشر",
    "ستة عشر",
    "سبعة عشر",
    "ثمانية عشر",
    "تسعة عشر",
)
TENS = ("", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون")
HUNDREDS = (
    "",
    "مائة",
    "مائتان",
    "ثلاثمائة",
    "أربعمائة",
    "خمسمائة",
    "ستمائة",
    "سبعمائة",
    "ثمانمائة",
    "تسعمائة",
)

# Noun forms are always (singular, dual, plural, singular-accusative).
SCALES = (
    (10**9, ("مليار", "ملياران", "مليارات", "ملياراً")),
    (10**6, ("مليون", "مليونان", "ملايين", "مليوناً")),
    (10**3, ("ألف", "ألفان", "آلاف", "ألفاً")),
)

# The largest integer the scale table can spell. §5.1 only demands
# 0–999,999,999.99; the extra مليار group is headroom, not a promise.
MAX_SUPPORTED = 10**12 - 1

# Only currencies whose fraction is a clean 1/100 and whose Arabic noun forms
# are unambiguous. Anything else returns "" so the print format falls back to
# figures — printing a guessed noun on a receipt is worse than printing none.
CURRENCY_FORMS = {
    "EGP": (
        ("جنيه", "جنيهان", "جنيهات", "جنيهاً"),
        ("قرش", "قرشان", "قروش", "قرشاً"),
    ),
    "SAR": (
        ("ريال", "ريالان", "ريالات", "ريالاً"),
        ("هللة", "هللتان", "هللات", "هللةً"),
    ),
    "AED": (
        ("درهم", "درهمان", "دراهم", "درهماً"),
        ("فلس", "فلسان", "فلوس", "فلساً"),
    ),
    "USD": (
        ("دولار", "دولاران", "دولارات", "دولاراً"),
        ("سنت", "سنتان", "سنتات", "سنتاً"),
    ),
    # يورو is indeclinable in Arabic — the same form serves every count.
    "EUR": (
        ("يورو", "يورو", "يورو", "يورو"),
        ("سنت", "سنتان", "سنتات", "سنتاً"),
    ),
}

DEFAULT_CURRENCY = "EGP"


def _construct(numeral: str) -> str:
    """Put the last word of a numeral into construct state (حالة الإضافة).

    When the counted noun follows the numeral directly it is مضاف إليه, so the
    word in front of it loses its tanween ("ألفاً" → "ألف") and a dual loses its
    nun ("مائتان" → "مائتا", "ألفان" → "ألفا"). Skipping this prints
    "أحد عشر ألفاً جنيه" for 11,000 where a reader expects "أحد عشر ألف جنيه".

    Only ever applied to the final word, because an earlier scale word is
    separated from the noun by واو and keeps its full form:
    "مليونان وثلاثة جنيهات" is right as it stands.
    """
    if not numeral:
        return numeral

    head, separator, last = numeral.rpartition(" ")
    if last.endswith("اً"):
        # Tanween fath is written alif + fathatan; both go, or "ألفاً" would
        # become "ألفا" (the dual) instead of "ألف".
        last = last[:-2]
    elif last.endswith("ً"):
        last = last[:-1]
    elif last.endswith("ان"):
        last = last[:-1]

    return f"{head}{separator}{last}"


def _three_digits_in_words(number: int) -> str:
    """Spell 1–999. Callers guarantee the range."""
    hundreds, remainder = divmod(number, 100)
    parts = []

    if hundreds:
        parts.append(HUNDREDS[hundreds])

    if remainder:
        if remainder < 10:
            parts.append(ONES[remainder])
        elif remainder < 20:
            parts.append(TEENS[remainder - 10])
        else:
            units, tens = remainder % 10, remainder // 10
            # "واحد وعشرون" — the unit precedes the ten, joined by واو.
            parts.append(f"{ONES[units]} و{TENS[tens]}" if units else TENS[tens])

    return " و".join(parts)


def _scale_phrase(count: int, forms: tuple) -> str:
    """Spell `count` thousands / millions / milliards with noun agreement.

    Unlike `_counted_phrase`, the numeral is dropped only for an exact 1 or 2.
    A count of 101 must keep it: "مائة وألف" would be read as 1,100.
    """
    singular, dual, plural, accusative = forms

    if count == 1:
        return singular
    if count == 2:
        return dual

    last_two = count % 100
    if 3 <= last_two <= 10:
        return f"{_construct(_three_digits_in_words(count))} {plural}"
    if last_two >= 11:
        # تمييز منصوب, not an إضافة — the numeral keeps its full form.
        return f"{_three_digits_in_words(count)} {accusative}"
    return f"{_construct(_three_digits_in_words(count))} {singular}"


def integer_in_words(number) -> str:
    """Spell a non-negative integer in Arabic. Return "" if out of range."""
    try:
        number = int(number)
    except (TypeError, ValueError):
        return ""

    if number < 0 or number > MAX_SUPPORTED:
        return ""
    if number == 0:
        return ZERO

    parts = []
    remainder = number
    for value, forms in SCALES:
        count, remainder = divmod(remainder, value)
        if count:
            parts.append(_scale_phrase(count, forms))

    if remainder:
        parts.append(_three_digits_in_words(remainder))

    return " و".join(parts)


def _counted_phrase(count: int, forms: tuple) -> str:
    """Spell `count` of a counted noun (pounds, piastres) with full agreement."""
    singular, dual, plural, accusative = forms
    last_two = count % 100

    if last_two == 1:
        head = count - 1
        tail = f"{singular} واحد"
        return tail if not head else f"{integer_in_words(head)} و{tail}"

    if last_two == 2:
        head = count - 2
        return dual if not head else f"{integer_in_words(head)} و{dual}"

    if 3 <= last_two <= 10:
        return f"{_construct(integer_in_words(count))} {plural}"

    if last_two >= 11:
        # تمييز منصوب, not an إضافة — the numeral keeps its full form.
        return f"{integer_in_words(count)} {accusative}"

    # last_two == 0 — includes zero itself ("صفر جنيه") and round hundreds.
    return f"{_construct(integer_in_words(count))} {singular}"


def cheque_amount_in_arabic_words(amount, currency: str | None = None) -> str:
    """Return an amount as Arabic words, or "" when it cannot be spelled safely.

    Exposed to Jinja through the `jinja.methods` hook so the print formats can
    call it. It never raises and never touches the database: a failure here must
    degrade to figures, not blank the voucher.

    "" is returned for an unsupported currency or an out-of-range amount, and the
    print formats treat that as "print the figure only".
    """
    try:
        value = Decimal(str(amount if amount is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return ""

    forms = CURRENCY_FORMS.get((currency or DEFAULT_CURRENCY).strip().upper())
    if not forms:
        return ""
    main_forms, fraction_forms = forms

    negative = value < 0
    value = abs(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    main_count = int(value)
    if main_count > MAX_SUPPORTED:
        return ""
    fraction_count = int((value - main_count) * 100)

    if main_count == 0 and fraction_count == 0:
        phrase = _counted_phrase(0, main_forms)
    elif main_count == 0:
        phrase = _counted_phrase(fraction_count, fraction_forms)
    elif fraction_count == 0:
        phrase = _counted_phrase(main_count, main_forms)
    else:
        phrase = (
            f"{_counted_phrase(main_count, main_forms)}"
            f" و{_counted_phrase(fraction_count, fraction_forms)}"
        )

    return f"{NEGATIVE} {phrase}" if negative else phrase
