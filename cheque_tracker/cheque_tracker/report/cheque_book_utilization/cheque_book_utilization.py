# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"fieldname": "name",             "label": _("Cheque Book"),   "fieldtype": "Link",  "options": "Cheque Book",  "width": 170},
        {"fieldname": "company",          "label": _("Company"),       "fieldtype": "Link",  "options": "Company",      "width": 150},
        {"fieldname": "bank_account",     "label": _("Bank Account"),  "fieldtype": "Link",  "options": "Bank Account", "width": 170},
        {"fieldname": "status",           "label": _("Status"),        "fieldtype": "Data",                             "width": 100},
        {"fieldname": "leaves_count",     "label": _("Total"),         "fieldtype": "Int",                              "width": 80},
        {"fieldname": "unused_leaves",    "label": _("Unused"),        "fieldtype": "Int",                              "width": 90},
        {"fieldname": "issued_leaves",    "label": _("Issued"),        "fieldtype": "Int",                              "width": 90},
        {"fieldname": "voided_leaves",    "label": _("Voided"),        "fieldtype": "Int",                              "width": 90},
        {"fieldname": "cancelled_leaves", "label": _("Cancelled"),     "fieldtype": "Int",                              "width": 100},
        {"fieldname": "utilization_pct",  "label": _("Utilization %"), "fieldtype": "Percent",                          "width": 120},
    ]


def get_data(filters):
    conds  = ["docstatus != 2"]
    values = {}

    if filters.get("company"):
        conds.append("company = %(company)s"); values["company"] = filters["company"]
    if filters.get("status"):
        conds.append("status = %(status)s");   values["status"]  = filters["status"]

    rows = frappe.db.sql(
        f"""
        SELECT name, company, bank_account, status,
               leaves_count, unused_leaves, issued_leaves,
               voided_leaves, cancelled_leaves
        FROM  `tabCheque Book`
        WHERE {" AND ".join(conds)}
        ORDER BY name
        """,
        values,
        as_dict=True,
    )

    result = []
    for row in rows:
        total  = int(row.get("leaves_count") or 0)
        issued = int(row.get("issued_leaves") or 0)
        result.append({
            **row,
            "utilization_pct": round((issued / total) * 100, 2) if total else 0.0,
        })
    return result
