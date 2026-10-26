"""v1.3.1 — put CHQ-2026-00004 back on the leaf it was written on.

CHQ-2026-00004 (160,000 EGP to SUP-129) was written on cheque 528201382 of
CHQBK-2026-00001, dated 2026-10-18, handed over, then returned unpresented when
the supplier was paid by transfer instead. Leaf reservation at the time ignored
the number it was given and took the first Unused leaf, so the record says
528201380 (e75ge51640) and stamped that leaf Issued with the day it was keyed
in, 2026-10-26.

This puts it right:

* the cheque carries cheque_no 528201382 and leaf e75gf78oeh;
* e75gf78oeh is Cancelled (the cheque came back — the leaf will never be paid),
  linked to the cheque, issued_on 2026-10-18;
* e75ge51640 (528201380) is Unused again and unlinked — that paper was never
  written;
* the book's counters are recomputed and the correction is logged on the
  cheque's timeline.

Idempotent: it computes the target state and writes only what differs, so a
second run finds nothing to do. Guarded: it touches nothing unless every record
matches the incident — same cheque, book, both leaves and both numbers — and
neither leaf is held by any other cheque. On any other site (eei-test, a dev
bench, a test site whose own naming series produced a CHQ-2026-00004) it is a
no-op.

Writes go through frappe.db rather than a document save: patches.txt carries
no section headers, so this runs pre_model_sync, before the v1.3.1 doctype
changes are synced (frappe/modules/patch_handler.py). Nothing here needs a
column that does not exist yet.
"""

import frappe
from frappe.utils import getdate, now_datetime

from cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book import refresh_book_counters
from cheque_tracker.cheque_tracker.doctype.cheque_leaf.cheque_leaf import get_leaf

CORRECTION = {
    "cheque": "CHQ-2026-00004",
    "cheque_book": "CHQBK-2026-00001",
    "wrong_leaf": "e75ge51640",
    "wrong_no": "528201380",
    "right_leaf": "e75gf78oeh",
    "right_no": "528201382",
    "issued_on": "2026-10-18",
}

_UNUSED = {
    "leaf_status": "Unused",
    "cheque": None,
    "reserved_by": None,
    "reserved_on": None,
    "issued_on": None,
    "void_reason": None,
}


def execute():
    outcome = correct_leaf(**CORRECTION)
    if outcome != "absent":
        print(f"[cheque_tracker] {CORRECTION['cheque']} leaf correction: {outcome}")


def correct_leaf(cheque, cheque_book, wrong_leaf, wrong_no, right_leaf, right_no, issued_on):
    """Move *cheque* from *wrong_leaf* to *right_leaf*, if and only if the
    records still describe the incident. Returns one of:

    ``absent``    — no such cheque on this site;
    ``mismatch``  — the records are not the ones this correction is for;
    ``already``   — already corrected, nothing written;
    ``corrected`` — the correction was applied.
    """
    doc = frappe.db.get_value(
        "Cheque",
        cheque,
        ["name", "cheque_type", "docstatus", "status", "cheque_book", "cheque_leaf", "cheque_no"],
        as_dict=True,
    )
    if not doc:
        return "absent"

    wrong = get_leaf(wrong_leaf)
    right = get_leaf(right_leaf)
    if not _is_the_incident(doc, wrong, right, cheque, cheque_book, wrong_no, right_no):
        return "mismatch"

    issued_on = getdate(issued_on)
    right_status = "Cancelled" if doc.status == "Returned" else "Issued"
    written = []

    if (doc.cheque_leaf, doc.cheque_no) != (right_leaf, right_no):
        frappe.db.set_value("Cheque", cheque, {"cheque_leaf": right_leaf, "cheque_no": right_no})
        written.append(f"cheque_no {doc.cheque_no} -> {right_no}")

    if (right.leaf_status, right.cheque, right.issued_on and getdate(right.issued_on)) != (
        right_status,
        cheque,
        issued_on,
    ):
        values = {"leaf_status": right_status, "cheque": cheque, "issued_on": issued_on}
        if right.cheque != cheque:
            # First take-over: the reservation is the one made for this cheque.
            values.update(reserved_by=wrong.reserved_by, reserved_on=wrong.reserved_on)
        if right_status == "Cancelled" and not right.void_reason:
            values["void_reason"] = f"Cheque {cheque} returned to the company — never presented."
        frappe.db.set_value("Cheque Leaf", right_leaf, values)
        written.append(f"leaf {right_no} -> {right_status}, issued_on {issued_on}")

    if any(wrong.get(field) for field in ("cheque", "reserved_by", "reserved_on", "issued_on")) or (
        wrong.leaf_status != "Unused"
    ):
        frappe.db.set_value("Cheque Leaf", wrong_leaf, _UNUSED)
        written.append(f"leaf {wrong_no} -> Unused")

    if not written:
        return "already"

    refresh_book_counters(cheque_book)
    _log_on_timeline(
        cheque,
        f"Cheque Leaf corrected by patch: {wrong_no} ({wrong_leaf}) -> {right_no} ({right_leaf}). "
        f"The cheque was written on {right_no}, dated {frappe.utils.format_date(issued_on)}; leaf "
        f"reservation had ignored that number and taken the first Unused leaf. {right_no} is "
        f"{right_status}; {wrong_no} is back to Unused. Changes: {'; '.join(written)}.",
    )
    frappe.clear_document_cache("Cheque", cheque)
    return "corrected"


def _is_the_incident(doc, wrong, right, cheque, cheque_book, wrong_no, right_no):
    if doc.cheque_type != "Outgoing" or doc.docstatus != 1 or doc.cheque_book != cheque_book:
        return False
    if not (wrong and right):
        return False
    if wrong.cheque_book != cheque_book or right.cheque_book != cheque_book:
        return False
    if wrong.cheque_no != wrong_no or right.cheque_no != right_no:
        return False
    if doc.cheque_leaf not in (wrong.name, right.name):
        return False
    # Never take a leaf from — or free a leaf of — another cheque.
    if (wrong.cheque or cheque) != cheque or (right.cheque or cheque) != cheque:
        return False
    # An unlinked leaf must still be blank paper; anything else means it was
    # used or voided since — the wrong leaf after the correction, say, taken
    # by a later cheque that was then cancelled — and this correction is no
    # longer the right one.
    if not right.cheque and right.leaf_status != "Unused":
        return False
    if not wrong.cheque and wrong.leaf_status != "Unused":
        return False
    return True


def _log_on_timeline(cheque, notes):
    """Append a Note to the cheque's Event Timeline without saving the cheque.

    A document save would run the full controller in the middle of a migrate;
    one child row is all that is needed.
    """
    idx = frappe.db.sql(
        "SELECT COALESCE(MAX(idx), 0) FROM `tabCheque Event` "
        "WHERE parent = %s AND parenttype = 'Cheque' AND parentfield = 'events'",
        cheque,
    )[0][0]
    event = frappe.new_doc("Cheque Event")
    event.update(
        {
            "parent": cheque,
            "parenttype": "Cheque",
            "parentfield": "events",
            "idx": int(idx) + 1,
            "docstatus": 1,
            "event_type": "Note",
            "event_datetime": now_datetime(),
            "notes": notes,
        }
    )
    # db_insert stamps creation / owner itself when they are blank.
    event.db_insert()
