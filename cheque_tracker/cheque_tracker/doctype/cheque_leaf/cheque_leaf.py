# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today


class ChequeLeaf(Document):
    def before_insert(self):
        # App-level duplicate guard (DB unique index is the real enforcer)
        if frappe.db.exists(
            "Cheque Leaf",
            {"cheque_book": self.cheque_book, "cheque_no": self.cheque_no},
        ):
            frappe.throw(
                _(
                    "Cheque Leaf {0} already exists in Cheque Book {1}."
                ).format(self.cheque_no, self.cheque_book),
                frappe.ValidationError,
            )

    def on_update(self):
        """Catch manual edits to leaf_status made through the desk form.

        Bulk generation sets `skip_counter_refresh` and refreshes once at the
        end instead of once per leaf (§3.2.8).
        """
        if self.flags.get("skip_counter_refresh"):
            return
        _refresh_counters(self.cheque_book)

    def on_trash(self):
        _refresh_counters(self.cheque_book)


def _refresh_counters(cheque_book: str):
    """Local import keeps cheque_book.py free of a cheque_leaf import cycle."""
    from cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book import (
        refresh_book_counters,
    )

    refresh_book_counters(cheque_book)


# ─────────────────────────────────────────────────────────────────── #
#  Concurrency-safe leaf reservation                                   #
# ─────────────────────────────────────────────────────────────────── #

def reserve_leaf(cheque_book: str, cheque_name: str, user: str) -> dict:
    """
    Atomically reserve the first Unused leaf for *cheque_book*.

    Algorithm
    ---------
    1. ``SELECT … FOR UPDATE`` locks the row, serialising concurrent callers.
    2. ``UPDATE … WHERE leaf_status='Unused'`` acts as a double-check;
       if another transaction already changed the status, ``ROW_COUNT()``
       returns 0 and we raise rather than silently succeed.

    Returns
    -------
    dict with keys ``name`` and ``cheque_no``.

    Raises
    ------
    frappe.ValidationError  – no leaf available or concurrency conflict.
    """
    result = frappe.db.sql(
        """
        SELECT name, cheque_no
        FROM   `tabCheque Leaf`
        WHERE  cheque_book  = %s
          AND  leaf_status  = 'Unused'
        ORDER  BY cheque_no
        LIMIT  1
        FOR UPDATE
        """,
        cheque_book,
        as_dict=True,
    )

    if not result:
        frappe.throw(
            _(
                "No unused cheque leaves available in Cheque Book {0}."
            ).format(cheque_book),
            frappe.ValidationError,
        )

    leaf = result[0]
    now  = now_datetime()

    frappe.db.sql(
        """
        UPDATE `tabCheque Leaf`
        SET    leaf_status  = 'Reserved',
               reserved_by  = %s,
               reserved_on  = %s,
               cheque       = %s,
               modified     = %s,
               modified_by  = %s
        WHERE  name        = %s
          AND  leaf_status = 'Unused'
        """,
        (user, now, cheque_name, now, user, leaf.name),
    )

    rows_affected = frappe.db.sql("SELECT ROW_COUNT() AS r", as_dict=True)[0].r
    if int(rows_affected) != 1:
        frappe.throw(
            _(
                "Concurrent reservation conflict on Cheque Book {0}. "
                "Please retry."
            ).format(cheque_book),
            frappe.ValidationError,
        )

    # These helpers write with frappe.db.set_value / raw SQL, which bypass the
    # ORM and therefore ChequeLeaf.on_update — refresh explicitly (§3.2.8).
    _refresh_counters(cheque_book)

    return {"name": leaf.name, "cheque_no": leaf.cheque_no}


def release_leaf(leaf_name: str, status: str = "Voided", void_reason: str = ""):
    """Set a Reserved or Issued leaf to Voided / Cancelled."""
    cheque_book = frappe.db.get_value("Cheque Leaf", leaf_name, "cheque_book")
    frappe.db.set_value(
        "Cheque Leaf",
        leaf_name,
        {
            "leaf_status":  status,
            "void_reason":  void_reason,
            "cheque":       None,
            "reserved_by":  None,
            "reserved_on":  None,
        },
    )
    _refresh_counters(cheque_book)


def mark_leaf_issued(leaf_name: str):
    """Transition a Reserved leaf to Issued."""
    cheque_book = frappe.db.get_value("Cheque Leaf", leaf_name, "cheque_book")
    frappe.db.set_value(
        "Cheque Leaf",
        leaf_name,
        {
            "leaf_status": "Issued",
            "issued_on":   today(),
        },
    )
    _refresh_counters(cheque_book)
