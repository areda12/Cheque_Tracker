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
#  Lookup                                                              #
# ─────────────────────────────────────────────────────────────────── #

_LEAF_COLUMNS = (
    "name, cheque_book, cheque_no, leaf_status, cheque, reserved_by, "
    "reserved_on, issued_on, void_reason"
)


def get_leaf(leaf_name: str, for_update: bool = False):
    """One leaf row as a dict, or None. `for_update` takes the row lock."""
    if not leaf_name:
        return None
    rows = frappe.db.sql(
        f"SELECT {_LEAF_COLUMNS} FROM `tabCheque Leaf` WHERE name = %s"
        + (" FOR UPDATE" if for_update else ""),
        leaf_name,
        as_dict=True,
    )
    return rows[0] if rows else None


def cheque_numbers_match(typed: str, leaf_no: str) -> bool:
    """True when *typed* names the leaf numbered *leaf_no*.

    Banks print the number zero-padded on the MICR line (000528201382) while the
    book stores whatever the range generated (528201382), so all-digit numbers
    compare without their leading zeros.
    """
    typed = (typed or "").strip()
    leaf_no = (leaf_no or "").strip()
    if typed == leaf_no:
        return True
    if typed.isdigit() and leaf_no.isdigit():
        return typed.lstrip("0") == leaf_no.lstrip("0") and bool(typed.lstrip("0"))
    return False


def find_leaf_by_number(cheque_book: str, cheque_no: str):
    """The leaf of *cheque_book* carrying *cheque_no*, or None.

    Exact match first; failing that, an all-digit number matches without its
    leading zeros (see cheque_numbers_match) — but only when exactly one leaf
    of the book fits, so a typo can never pick between two candidates.
    """
    cheque_no = (cheque_no or "").strip()
    if not (cheque_book and cheque_no):
        return None

    name = frappe.db.get_value(
        "Cheque Leaf", {"cheque_book": cheque_book, "cheque_no": cheque_no}, "name"
    )
    if name:
        return get_leaf(name)

    if not cheque_no.isdigit() or not cheque_no.lstrip("0"):
        return None
    matches = frappe.db.sql(
        """
        SELECT name
        FROM   `tabCheque Leaf`
        WHERE  cheque_book = %s
          AND  TRIM(LEADING '0' FROM cheque_no) = %s
        """,
        (cheque_book, cheque_no.lstrip("0")),
        as_dict=True,
    )
    return get_leaf(matches[0].name) if len(matches) == 1 else None


def describe_unavailable(leaf) -> str:
    """Why *leaf* cannot be handed to a cheque, in words a clerk can act on."""
    if leaf.leaf_status in ("Reserved", "Issued") and leaf.cheque:
        return _("Cheque Leaf {0} is already {1} for {2}.").format(
            leaf.cheque_no, _(leaf.leaf_status), leaf.cheque
        )
    if leaf.void_reason:
        return _("Cheque Leaf {0} is {1} ({2}).").format(
            leaf.cheque_no, _(leaf.leaf_status), leaf.void_reason
        )
    return _("Cheque Leaf {0} is {1}.").format(leaf.cheque_no, _(leaf.leaf_status))


# ─────────────────────────────────────────────────────────────────── #
#  Concurrency-safe leaf reservation                                   #
# ─────────────────────────────────────────────────────────────────── #

def reserve_leaf(cheque_book: str, cheque_name: str, user: str, leaf_name: str = None) -> dict:
    """
    Atomically reserve a leaf of *cheque_book* for *cheque_name*.

    With *leaf_name* it is that exact leaf, which must be Unused and belong to
    *cheque_book* — the leaf the clerk actually wrote. Without it, the first
    Unused leaf in number order.

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
    frappe.ValidationError  – no leaf available, the chosen leaf is not
    Unused / not in this book, or a concurrency conflict.
    """
    if leaf_name:
        leaf = get_leaf(leaf_name, for_update=True)
        if not leaf:
            frappe.throw(
                _("Cheque Leaf {0} does not exist.").format(leaf_name),
                frappe.ValidationError,
            )
        if leaf.cheque_book != cheque_book:
            frappe.throw(
                _("Cheque Leaf {0} belongs to Cheque Book {1}, not {2}.").format(
                    leaf.cheque_no, leaf.cheque_book, cheque_book
                ),
                frappe.ValidationError,
            )
        if leaf.leaf_status != "Unused":
            frappe.throw(
                describe_unavailable(leaf)
                + " "
                + _(
                    "Pick an Unused leaf of {0}, or leave Cheque Leaf and Cheque No "
                    "blank to take the next Unused one."
                ).format(cheque_book),
                frappe.ValidationError,
                title=_("Cheque Leaf not available"),
            )
    else:
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


def free_leaf(leaf_name: str, cheque_name: str):
    """Put a leaf back to Unused and unlink it from *cheque_name*.

    Only a leaf linked to *cheque_name* is freed. A stale pointer must never
    hand another cheque's leaf back to the pool, and an unlinked leaf is left
    alone too: the pre-tracker Voided leaves carry no cheque link at all.
    Returns True when the leaf was freed.
    """
    leaf = get_leaf(leaf_name, for_update=True)
    if not leaf or not cheque_name or leaf.cheque != cheque_name:
        return False
    frappe.db.set_value(
        "Cheque Leaf",
        leaf_name,
        {
            "leaf_status": "Unused",
            "cheque":      None,
            "reserved_by": None,
            "reserved_on": None,
            "issued_on":   None,
            "void_reason": None,
        },
    )
    _refresh_counters(leaf.cheque_book)
    return True


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


def cancel_leaf(leaf_name: str, void_reason: str = ""):
    """Mark a leaf Cancelled while it stays linked to its cheque.

    Used when an outgoing cheque comes back to us: the paper is dead, but the
    cheque document is still submitted and still the record of what was
    written on that leaf, so — unlike release_leaf — the link is kept.
    """
    cheque_book = frappe.db.get_value("Cheque Leaf", leaf_name, "cheque_book")
    frappe.db.set_value(
        "Cheque Leaf",
        leaf_name,
        {"leaf_status": "Cancelled", "void_reason": void_reason},
    )
    _refresh_counters(cheque_book)


def mark_leaf_issued(leaf_name: str, issued_on=None):
    """Transition a Reserved leaf to Issued.

    *issued_on* is the date written on the cheque (its issue_date). It only
    falls back to today for callers that have no cheque behind them — a cheque
    recorded a week after it was written was still issued a week ago.
    """
    cheque_book = frappe.db.get_value("Cheque Leaf", leaf_name, "cheque_book")
    frappe.db.set_value(
        "Cheque Leaf",
        leaf_name,
        {
            "leaf_status": "Issued",
            "issued_on":   issued_on or today(),
        },
    )
    _refresh_counters(cheque_book)


def transfer_leaf(
    old_leaf: str,
    new_leaf: str,
    cheque_name: str,
    user: str,
    leaf_status: str,
    issued_on=None,
    void_reason: str = None,
) -> dict:
    """Move a submitted cheque from *old_leaf* to *new_leaf* (Reassign Leaf).

    Both rows are locked first. *new_leaf* must be Unused and in the same book
    as *old_leaf*; *old_leaf* must belong to *cheque_name*. The new leaf takes
    over the cheque in *leaf_status* (Issued, or Cancelled for a returned
    cheque); the old one goes back to Unused, unlinked.

    Returns the new leaf's name and number, and whether the old leaf was freed
    (it is not when it was no longer linked to the cheque).
    """
    old = get_leaf(old_leaf, for_update=True)
    new = get_leaf(new_leaf, for_update=True)
    if not new:
        frappe.throw(_("Cheque Leaf {0} does not exist.").format(new_leaf), frappe.ValidationError)
    if old and old.cheque and old.cheque != cheque_name:
        frappe.throw(
            _("Cheque Leaf {0} is held by {1}, not {2}.").format(old.cheque_no, old.cheque, cheque_name),
            frappe.ValidationError,
        )
    if old and new.cheque_book != old.cheque_book:
        frappe.throw(
            _("Cheque Leaf {0} belongs to Cheque Book {1}, not {2}.").format(
                new.cheque_no, new.cheque_book, old.cheque_book
            ),
            frappe.ValidationError,
        )
    if new.leaf_status != "Unused":
        frappe.throw(describe_unavailable(new), frappe.ValidationError, title=_("Cheque Leaf not available"))

    now = now_datetime()
    frappe.db.sql(
        """
        UPDATE `tabCheque Leaf`
        SET    leaf_status = %s,
               cheque      = %s,
               reserved_by = %s,
               reserved_on = %s,
               issued_on   = %s,
               void_reason = %s,
               modified    = %s,
               modified_by = %s
        WHERE  name        = %s
          AND  leaf_status = 'Unused'
        """,
        (leaf_status, cheque_name, user, now, issued_on, void_reason, now, user, new.name),
    )
    rows_affected = frappe.db.sql("SELECT ROW_COUNT() AS r", as_dict=True)[0].r
    if int(rows_affected) != 1:
        frappe.throw(
            _("Concurrent reservation conflict on Cheque Leaf {0}. Please retry.").format(new.cheque_no),
            frappe.ValidationError,
        )

    freed = free_leaf(old.name, cheque_name) if old else False
    _refresh_counters(new.cheque_book)

    return {"name": new.name, "cheque_no": new.cheque_no, "old_freed": freed}
