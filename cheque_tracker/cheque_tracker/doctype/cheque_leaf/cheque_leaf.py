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

    return {"name": leaf.name, "cheque_no": leaf.cheque_no}


def release_leaf(leaf_name: str, status: str = "Voided", void_reason: str = ""):
    """Set a Reserved or Issued leaf to Voided / Cancelled."""
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


def mark_leaf_issued(leaf_name: str):
    """Transition a Reserved leaf to Issued."""
    frappe.db.set_value(
        "Cheque Leaf",
        leaf_name,
        {
            "leaf_status": "Issued",
            "issued_on":   today(),
        },
    )
