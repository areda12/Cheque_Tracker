# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""§5.3.2 — who is holding which cheque, and for how long.

Custody is the control this app exists for: a cheque sitting in someone's drawer
for six weeks is the failure mode, and it is invisible on the list view. Rows are
ordered by holder so the report reads as one block per custodian, oldest cheque
first inside each block.

**Age is measured from the transition into the current state**, not from
`received_date` — a cheque received in January and moved to the safe in March has
been in the safe for weeks, not months. That transition is the timestamp on the
matching Cheque Event (`event_type` mirrors the status, see cheque.py
STATUS_EVENT_MAP); the dates on the document are the fallback for rows written
before the timeline existed.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, getdate, today

# §4.1 vocabulary. "In custody" means the cheque is still live and someone —
# an employee, the bank, an endorsee, a payee — is holding it on our behalf.
# Cleared / Bounced / Returned / Replaced / Cancelled are settled and drop out.
INCOMING_CUSTODY_STATUSES = ("Received", "In Safe", "Deposited", "Endorsed")
OUTGOING_CUSTODY_STATUSES = ("Issued", "In Safe", "Handed Over", "Presented")

# Custody left the company but no external_holder was recorded: still a real
# custody position, so it gets its own bucket rather than being dropped.
UNASSIGNED = "(Unassigned)"


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"fieldname": "holder",           "label": _("Holder"),          "fieldtype": "Data",                                "width": 200},
        {"fieldname": "holder_type",      "label": _("Holder Type"),     "fieldtype": "Data",                                "width": 110},
        {"fieldname": "holder_user",      "label": _("User"),            "fieldtype": "Link",     "options": "User",         "width": 170},
        {"fieldname": "name",             "label": _("Cheque"),          "fieldtype": "Link",     "options": "Cheque",       "width": 150},
        {"fieldname": "cheque_no",        "label": _("Cheque No"),       "fieldtype": "Data",                                "width": 120},
        {"fieldname": "cheque_type",      "label": _("Type"),            "fieldtype": "Data",                                "width": 90},
        {"fieldname": "status",           "label": _("Status"),          "fieldtype": "Data",                                "width": 110},
        {"fieldname": "party_type",       "label": _("Party Type"),      "fieldtype": "Data",                                "width": 100},
        {"fieldname": "party",            "label": _("Party"),           "fieldtype": "Data",                                "width": 160},
        {"fieldname": "amount",           "label": _("Amount"),          "fieldtype": "Currency", "options": "currency",     "width": 130},
        {"fieldname": "currency",         "label": _("Currency"),        "fieldtype": "Link",     "options": "Currency",     "width": 80},
        {"fieldname": "due_date",         "label": _("Due Date"),        "fieldtype": "Date",                                "width": 110},
        {"fieldname": "custody_since",    "label": _("In Custody Since"), "fieldtype": "Date",                               "width": 130},
        {"fieldname": "age_days",         "label": _("Age (Days)"),      "fieldtype": "Int",                                 "width": 100},
        {"fieldname": "custody_location", "label": _("Custody Location"), "fieldtype": "Data",                               "width": 150},
        {"fieldname": "company",          "label": _("Company"),         "fieldtype": "Link",     "options": "Company",      "width": 150},
    ]


def get_data(filters):
    conds = [
        "c.docstatus = 1",
        # An Incoming cheque must not be counted as in custody because it sits in
        # an outgoing-only state (and vice versa) — the two vocabularies do not
        # cross (§4.1), so the pairing is enforced here rather than by status alone.
        "((c.cheque_type = 'Incoming' AND c.status IN %(incoming)s)"
        " OR (c.cheque_type = 'Outgoing' AND c.status IN %(outgoing)s))",
    ]
    values = {"incoming": INCOMING_CUSTODY_STATUSES, "outgoing": OUTGOING_CUSTODY_STATUSES}

    if filters.get("company"):
        conds.append("c.company = %(company)s");         values["company"]     = filters["company"]
    if filters.get("cheque_type"):
        conds.append("c.cheque_type = %(cheque_type)s"); values["cheque_type"] = filters["cheque_type"]
    if filters.get("holder"):
        conds.append("(c.current_holder = %(holder)s OR c.external_holder LIKE %(holder_like)s)")
        values["holder"] = filters["holder"]
        values["holder_like"] = f"%{filters['holder']}%"

    rows = frappe.db.sql(
        f"""
        SELECT c.name, c.cheque_no, c.cheque_type, c.status, c.party_type, c.party,
               c.amount, c.currency, c.due_date, c.received_date, c.issue_date,
               c.current_holder, c.external_holder, c.custody_location, c.company,
               c.creation,
               (SELECT MAX(ce.event_datetime)
                  FROM `tabCheque Event` ce
                 WHERE ce.parent = c.name
                   AND ce.parenttype = 'Cheque'
                   AND ce.event_type = c.status) AS state_entered_on
        FROM  `tabCheque` c
        WHERE {" AND ".join(conds)}
        """,
        values,
        as_dict=True,
    )

    today_date = getdate(today())
    full_names = _user_full_names(rows)
    data = []

    for row in rows:
        holder, holder_type = _resolve_holder(row, full_names)
        custody_since = _custody_since(row)
        data.append(
            {
                "holder":           holder,
                "holder_type":      holder_type,
                "holder_user":      row.current_holder,
                "name":             row.name,
                "cheque_no":        row.cheque_no,
                "cheque_type":      row.cheque_type,
                "status":           row.status,
                "party_type":       row.party_type,
                "party":            row.party,
                "amount":           row.amount,
                "currency":         row.currency,
                "due_date":         row.due_date,
                "custody_since":    custody_since,
                "age_days":         date_diff(today_date, custody_since),
                "custody_location": row.custody_location,
                "company":          row.company,
            }
        )

    # Holder first so the report reads as one block per custodian; oldest cheque
    # at the top of each block, because that is the one to chase.
    data.sort(key=lambda r: (r["holder"], -r["age_days"], r["name"]))
    return data


def _user_full_names(rows):
    """One query for every internal holder, instead of one per row."""
    users = {row.current_holder for row in rows if row.current_holder}
    if not users:
        return {}
    return dict(
        frappe.get_all(
            "User", filters={"name": ("in", list(users))}, fields=["name", "full_name"], as_list=True
        )
    )


def _resolve_holder(row, full_names):
    if row.current_holder:
        return full_names.get(row.current_holder) or row.current_holder, _("User")
    if row.external_holder:
        return row.external_holder, _("External")
    return UNASSIGNED, _("Unassigned")


def _custody_since(row):
    """When the cheque entered the state it is in now.

    Falls back to the document's own dates for rows with no timeline (cheques
    created before §3.2.6 started logging workflow transitions), and finally to
    creation, so the column is never empty and `age_days` is never a guess about
    an unknown date.
    """
    if row.state_entered_on:
        return getdate(row.state_entered_on)

    fallback = row.received_date if row.cheque_type == "Incoming" else row.issue_date
    return getdate(fallback or row.creation)
