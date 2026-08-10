"""v1.2 — split the incoming/outgoing status vocabulary (BUILD_INSTRUCTIONS §4.1).

Until now an *issued* outgoing cheque landed in **Received**, because both
directions shared one status list and `before_submit` forced "Received". That is
the root cause of the outgoing dashboard-filter bugs and of a good deal of user
confusion: "Received" reads as "we got a cheque", which is the opposite of what
happened.

This patch moves existing data onto the new vocabulary and re-applies the
dashboard fixtures, which now filter on it.

Runs pre_model_sync — `patches.txt` carries no section headers, so every patch in
this app runs before `sync_all()` (frappe/modules/patch_handler.py:106-110).
That is why the status is rewritten with `frappe.db.set_value`: the Cheque
doctype's Select options do not yet include "Issued" at this point, and a
document save would fail `_validate_selects`.
"""

import frappe

# (cheque_type, old status) → new status
_STATUS_MIGRATION = {
    ("Outgoing", "Received"): "Issued",
}


def execute():
    migrated = _migrate_statuses()
    _migrate_event_types()
    _reapply_dashboard_fixtures()

    if migrated:
        frappe.clear_cache(doctype="Cheque")


def _migrate_statuses():
    """Outgoing cheques sitting in an incoming status move to their own.

    Incoming cheques are deliberately untouched: their vocabulary did not change.
    """
    total = 0
    for (cheque_type, old_status), new_status in _STATUS_MIGRATION.items():
        names = frappe.get_all(
            "Cheque",
            filters={"cheque_type": cheque_type, "status": old_status},
            pluck="name",
        )
        for name in names:
            # update_modified=False: this is a vocabulary rename, not a business
            # event. Bumping `modified` would make every migrated cheque look
            # edited today in the list view and in any "recently changed" report.
            frappe.db.set_value("Cheque", name, "status", new_status, update_modified=False)

        if names:
            print(
                f"[cheque_tracker] {len(names)} {cheque_type} cheque(s): "
                f"{old_status} -> {new_status}"
            )
        total += len(names)

    return total


def _migrate_event_types():
    """The timeline rows keep their own copy of the status.

    A submit event logged before v1.2 says "Received" on an outgoing cheque. Left
    alone the audit trail would contradict the document it belongs to.
    """
    rows = frappe.db.sql(
        """
        SELECT ce.name
        FROM   `tabCheque Event` ce
        JOIN   `tabCheque` c ON c.name = ce.parent
        WHERE  ce.parenttype = 'Cheque'
          AND  c.cheque_type = 'Outgoing'
          AND  ce.event_type = 'Received'
        """,
        as_dict=True,
    )
    for row in rows:
        frappe.db.set_value("Cheque Event", row.name, "event_type", "Issued", update_modified=False)

    if rows:
        print(f"[cheque_tracker] {len(rows)} outgoing Cheque Event(s): Received -> Issued")

    return len(rows)


def _reapply_dashboard_fixtures():
    """Re-run the v3_2 repair against the now-updated fixture files.

    v3_2 already ran on these sites and will not run again, but its helpers read
    the canonical values straight from `fixtures/*.json` — which this release
    changed — so calling them is exactly the right repair for the new vocabulary.
    """
    from cheque_tracker.patches.v3_2 import repair_dashboard_fixtures

    repair_dashboard_fixtures.execute()
