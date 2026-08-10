# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class ChequeBook(Document):
    # ------------------------------------------------------------------ #
    #  Life-cycle hooks                                                    #
    # ------------------------------------------------------------------ #

    def validate(self):
        self._validate_bank_account_company()
        self._compute_pattern_example()
        if self.sequence_type == "Numeric":
            self._compute_leaves_count()

    def before_submit(self):
        self._validate_bank_account_company()

    def on_submit(self):
        self._generate_leaves()
        self._refresh_counters()
        self.db_set("status", "Active", update_modified=False)

    def on_cancel(self):
        self._cancel_unused_leaves()
        self._refresh_counters()
        self.db_set("status", "Cancelled", update_modified=False)

    # ------------------------------------------------------------------ #
    #  Validations                                                         #
    # ------------------------------------------------------------------ #

    def _validate_bank_account_company(self):
        if not (self.bank_account and self.company):
            return
        ba_company = frappe.db.get_value("Bank Account", self.bank_account, "company")
        if ba_company and ba_company != self.company:
            frappe.throw(
                _(
                    "Bank Account {0} belongs to Company {1}, not {2}."
                ).format(self.bank_account, ba_company, self.company),
                frappe.ValidationError,
            )

    def _compute_pattern_example(self):
        if self.sequence_type == "Alphanumeric Pattern":
            prefix = self.prefix or ""
            suffix = self.suffix or ""
            digits = cint(self.digits_count) or 6
            self.pattern_example = f"{prefix}{'1'.zfill(digits)}{suffix}"

    def _compute_leaves_count(self):
        try:
            start = int(self.start_cheque_no)
            end   = int(self.end_cheque_no)
        except (ValueError, TypeError):
            frappe.throw(
                _("For Numeric sequence, Start and End Cheque No must be integers."),
                frappe.ValidationError,
            )
            return
        if end < start:
            frappe.throw(
                _("End Cheque No must be greater than or equal to Start Cheque No."),
                frappe.ValidationError,
            )
        self.leaves_count = end - start + 1

    # ------------------------------------------------------------------ #
    #  Leaf generation                                                     #
    # ------------------------------------------------------------------ #

    def _generate_leaves(self):
        if self.sequence_type == "Numeric":
            self._generate_range_leaves()
        else:
            self._generate_range_leaves()   # same logic; prefix/suffix differ

    def _generate_range_leaves(self):
        try:
            start = int(self.start_cheque_no)
            end   = int(self.end_cheque_no)
        except (ValueError, TypeError):
            frappe.throw(
                _("Start and End Cheque No must be integers."),
                frappe.ValidationError,
            )
            return

        digits = cint(self.digits_count)
        prefix = self.prefix or ""
        suffix = self.suffix or ""

        for num in range(start, end + 1):
            cheque_no = self._fmt(num, digits, prefix, suffix)
            leaf = frappe.new_doc("Cheque Leaf")
            leaf.cheque_book   = self.name
            leaf.company       = self.company
            leaf.bank_account  = self.bank_account
            leaf.cheque_no     = cheque_no
            leaf.leaf_status   = "Unused"
            leaf.flags.ignore_permissions = True
            # Bulk generation: refresh the counters once after the loop
            # (on_submit) instead of once per leaf.
            leaf.flags.skip_counter_refresh = True
            leaf.insert()

    @staticmethod
    def _fmt(num, digits, prefix, suffix):
        num_str = str(num).zfill(digits) if digits else str(num)
        return f"{prefix}{num_str}{suffix}"

    # ------------------------------------------------------------------ #
    #  Counter refresh (called after every leaf state change)             #
    # ------------------------------------------------------------------ #

    def _refresh_counters(self):
        counters = refresh_book_counters(self.name)
        # Keep the in-memory doc consistent with what was just written.
        for field, value in counters.items():
            self.set(field, value)

    def _cancel_unused_leaves(self):
        frappe.db.sql(
            """
            UPDATE `tabCheque Leaf`
            SET    leaf_status = 'Cancelled', modified = NOW()
            WHERE  cheque_book = %s AND leaf_status = 'Unused'
            """,
            self.name,
        )


# ------------------------------------------------------------------ #
#  Counter refresh — module level so every leaf state change can call #
#  it without loading the Cheque Book document (§3.2.8)               #
# ------------------------------------------------------------------ #

_COUNTER_FIELDS = {
    "unused_leaves":    "Unused",
    "issued_leaves":    "Issued",
    "voided_leaves":    "Voided",
    "cancelled_leaves": "Cancelled",
}


def refresh_book_counters(cheque_book: str) -> dict:
    """Recompute the stored leaf counters for *cheque_book* from the leaves.

    The counters used to be recomputed only when someone opened the Cheque Book
    form (get_book_counters), so the list view — and anything else reading the
    stored fields — showed values that were stale the moment a leaf moved.
    Every reserve / issue / void / cancel / manual edit now calls this.

    Returns the counter values written, so callers holding a Cheque Book doc
    can resync their in-memory copy.
    """
    if not cheque_book:
        return {}

    counts = frappe.db.sql(
        """
        SELECT leaf_status, COUNT(*) AS cnt
        FROM   `tabCheque Leaf`
        WHERE  cheque_book = %s
        GROUP  BY leaf_status
        """,
        cheque_book,
        as_dict=True,
    )
    by_status = {row.leaf_status: row.cnt for row in counts}
    if not counts and not frappe.db.exists("Cheque Book", cheque_book):
        return {}

    updates = {field: by_status.get(status, 0) for field, status in _COUNTER_FIELDS.items()}
    frappe.db.set_value("Cheque Book", cheque_book, updates, update_modified=False)

    # Auto-exhaust: nothing left to hand out and something was consumed.
    if by_status.get("Unused", 0) == 0 and by_status.get("Reserved", 0) == 0:
        consumed = sum(by_status.get(s, 0) for s in ("Issued", "Voided", "Cancelled"))
        if consumed and frappe.db.get_value("Cheque Book", cheque_book, "status") == "Active":
            frappe.db.set_value(
                "Cheque Book", cheque_book, "status", "Exhausted", update_modified=False
            )
            updates["status"] = "Exhausted"

    return updates


# ------------------------------------------------------------------ #
#  doc_events entry points (wired via hooks.py)                       #
# ------------------------------------------------------------------ #

def on_submit(doc, method=None):
    pass   # handled inside ChequeBook.on_submit()


def on_cancel(doc, method=None):
    pass   # handled inside ChequeBook.on_cancel()


# ------------------------------------------------------------------ #
#  Whitelisted API                                                    #
# ------------------------------------------------------------------ #

@frappe.whitelist()
def get_book_counters(cheque_book):
    """Return live leaf counters – used by dashboard cards."""
    doc = frappe.get_doc("Cheque Book", cheque_book)
    # C2: _refresh_counters writes via db_set (bypasses Frappe's
    # write-perm check). Enforce write permission explicitly before
    # triggering the side-effect, otherwise a read-only role like
    # Cheque Auditor could call this whitelisted endpoint and cause
    # persistent counter writes.
    frappe.has_permission("Cheque Book", "write", doc=doc, throw=True)
    doc._refresh_counters()
    return {
        "status":           frappe.db.get_value("Cheque Book", cheque_book, "status"),
        "unused_leaves":    frappe.db.get_value("Cheque Book", cheque_book, "unused_leaves"),
        "issued_leaves":    frappe.db.get_value("Cheque Book", cheque_book, "issued_leaves"),
        "voided_leaves":    frappe.db.get_value("Cheque Book", cheque_book, "voided_leaves"),
        "cancelled_leaves": frappe.db.get_value("Cheque Book", cheque_book, "cancelled_leaves"),
    }
