"""Align the Cheque Workflow on existing installs:

  • Drop the 'Presented' state and every transition that touches it.
  • Add the missing 'Deposited → Return → Returned' transition.
  • Migrate any Cheque rows currently in 'Presented' status back to
    'Deposited' so they can re-enter the simplified workflow.

The fixture JSON is the source of truth for fresh installs; this
patch covers existing installations where the workflow already exists
in the database and won't auto-update from the fixture diff alone.
"""

import frappe


def execute():
    _migrate_presented_cheques()
    _patch_workflow()
    _delete_presented_workflow_state()
    _delete_present_action_master()
    frappe.clear_cache(doctype="Cheque")


def _migrate_presented_cheques():
    """Reset any Cheque still sitting in 'Presented' to 'Deposited'."""
    rows = frappe.get_all("Cheque", filters={"status": "Presented"}, pluck="name")
    for name in rows:
        frappe.db.set_value("Cheque", name, "status", "Deposited", update_modified=False)
    if rows:
        frappe.log_error(
            f"align_cheque_workflow: reset {len(rows)} cheque(s) from "
            f"'Presented' to 'Deposited': {rows}",
            "Cheque Tracker migration",
        )


def _patch_workflow():
    if not frappe.db.exists("Workflow", "Cheque Workflow"):
        return

    wf = frappe.get_doc("Workflow", "Cheque Workflow")

    wf.states = [s for s in wf.states if s.state != "Presented"]

    wf.transitions = [
        t for t in wf.transitions
        if t.state != "Presented" and t.next_state != "Presented"
    ]

    has_deposited_return = any(
        t.state == "Deposited" and t.action == "Return" and t.next_state == "Returned"
        for t in wf.transitions
    )
    if not has_deposited_return:
        wf.append("transitions", {
            "state":               "Deposited",
            "action":              "Return",
            "next_state":          "Returned",
            "allowed":             "Treasury User",
            "allow_self_approval": 1,
        })

    wf.flags.ignore_permissions = True
    wf.save()


def _delete_presented_workflow_state():
    if frappe.db.exists("Workflow State", "Presented"):
        frappe.delete_doc(
            "Workflow State", "Presented",
            ignore_permissions=True, force=True,
        )


def _delete_present_action_master():
    if frappe.db.exists("Workflow Action Master", "Present"):
        frappe.delete_doc(
            "Workflow Action Master", "Present",
            ignore_permissions=True, force=True,
        )
