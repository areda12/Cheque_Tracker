"""Add Outgoing-cheque workflow path on existing installs.

  • Insert the 'Handed Over' state (doc_status=1, Treasury User).
  • Make every existing transition cheque_type-conditional so Incoming
    uses Deposit→Cleared and Outgoing uses Hand Over→Cleared.
  • Add the new Outgoing transitions (Hand Over from Received/In Safe;
    Clear/Bounce/Return from Handed Over).
  • Replace stays unconditional.

The fixture JSON is the source of truth for fresh installs; this
patch covers existing installations where the workflow already exists
in the database and won't auto-update from the fixture diff alone.
"""

import frappe


# Desired Outgoing transitions, keyed by (state, action) for idempotent insertion.
_OUTGOING_TRANSITIONS = {
    ("Received",    "Hand Over"): {
        "next_state": "Handed Over", "allowed": "Treasury User",
        "condition": "doc.cheque_type == 'Outgoing'",
    },
    ("In Safe",     "Hand Over"): {
        "next_state": "Handed Over", "allowed": "Treasury User",
        "condition": "doc.cheque_type == 'Outgoing'",
    },
    ("Handed Over", "Clear"): {
        "next_state": "Cleared", "allowed": "Accounts User",
        "condition": "doc.cheque_type == 'Outgoing'",
    },
    ("Handed Over", "Bounce"): {
        "next_state": "Bounced", "allowed": "Treasury User",
        "condition": "doc.cheque_type == 'Outgoing'",
    },
    ("Handed Over", "Return"): {
        "next_state": "Returned", "allowed": "Treasury User",
        "condition": "doc.cheque_type == 'Outgoing'",
    },
}

# Conditions to enforce on existing transitions. Empty string means leave
# the existing condition alone (used for shared shared-direction rows).
_INCOMING_CONDITION = "doc.cheque_type == 'Incoming'"
_INCOMING_DEPOSIT_CONDITION = "doc.cheque_type == 'Incoming' and doc.bank_account"

_TRANSITION_CONDITIONS = {
    ("Draft",     "Receive"):    _INCOMING_CONDITION,
    ("Draft",     "Submit"):     "doc.cheque_type == 'Outgoing'",
    ("Received",  "Deposit"):    _INCOMING_DEPOSIT_CONDITION,
    ("In Safe",   "Deposit"):    _INCOMING_DEPOSIT_CONDITION,
    ("Deposited", "Clear"):      _INCOMING_CONDITION,
    ("Deposited", "Bounce"):     _INCOMING_CONDITION,
    ("Deposited", "Return"):     _INCOMING_CONDITION,
}


def execute():
    _ensure_handed_over_state()
    _patch_workflow()
    frappe.clear_cache(doctype="Cheque")


def _ensure_handed_over_state():
    if not frappe.db.exists("Workflow State", "Handed Over"):
        ws = frappe.new_doc("Workflow State")
        ws.workflow_state_name = "Handed Over"
        ws.style = "Warning"
        ws.flags.ignore_permissions = True
        ws.insert()
    if not frappe.db.exists("Workflow Action Master", "Hand Over"):
        wa = frappe.new_doc("Workflow Action Master")
        wa.workflow_action_name = "Hand Over"
        wa.flags.ignore_permissions = True
        wa.insert()


def _patch_workflow():
    if not frappe.db.exists("Workflow", "Cheque Workflow"):
        return

    wf = frappe.get_doc("Workflow", "Cheque Workflow")

    # Add Handed Over to states if missing.
    if not any(s.state == "Handed Over" for s in wf.states):
        wf.append("states", {
            "state":       "Handed Over",
            "doc_status":  "1",
            "allow_edit":  "Treasury User",
        })

    # Update conditions on existing transitions.
    for t in wf.transitions:
        cond = _TRANSITION_CONDITIONS.get((t.state, t.action))
        if cond is not None:
            t.condition = cond

    # Append missing Outgoing transitions.
    existing = {(t.state, t.action) for t in wf.transitions}
    for (state, action), spec in _OUTGOING_TRANSITIONS.items():
        if (state, action) in existing:
            continue
        wf.append("transitions", {
            "state":               state,
            "action":              action,
            "next_state":          spec["next_state"],
            "allowed":             spec["allowed"],
            "allow_self_approval": 1,
            "condition":           spec["condition"],
        })

    wf.flags.ignore_permissions = True
    wf.save()
