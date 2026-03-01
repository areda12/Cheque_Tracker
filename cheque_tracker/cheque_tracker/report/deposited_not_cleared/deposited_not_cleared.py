# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _
from frappe.utils import date_diff, getdate, today


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"fieldname": "name",           "label": _("Cheque"),       "fieldtype": "Link",     "options": "Cheque",    "width": 150},
        {"fieldname": "cheque_no",      "label": _("Cheque No"),    "fieldtype": "Data",                             "width": 120},
        {"fieldname": "cheque_type",    "label": _("Type"),         "fieldtype": "Data",                             "width": 90},
        {"fieldname": "party",          "label": _("Party"),        "fieldtype": "Data",                             "width": 160},
        {"fieldname": "amount",         "label": _("Amount"),       "fieldtype": "Currency",                         "width": 130},
        {"fieldname": "currency",       "label": _("Currency"),     "fieldtype": "Link",     "options": "Currency",  "width": 80},
        {"fieldname": "due_date",       "label": _("Due Date"),     "fieldtype": "Date",                             "width": 110},
        {"fieldname": "status",         "label": _("Status"),       "fieldtype": "Data",                             "width": 100},
        {"fieldname": "age_days",       "label": _("Age (Days)"),   "fieldtype": "Int",                              "width": 90},
        {"fieldname": "bucket_0_7",     "label": _("0-7 Days"),     "fieldtype": "Currency",                         "width": 110},
        {"fieldname": "bucket_8_14",    "label": _("8-14 Days"),    "fieldtype": "Currency",                         "width": 110},
        {"fieldname": "bucket_15_30",   "label": _("15-30 Days"),   "fieldtype": "Currency",                         "width": 115},
        {"fieldname": "bucket_over_30", "label": _(">30 Days"),     "fieldtype": "Currency",                         "width": 110},
    ]


def get_data(filters):
    conds  = ["status IN ('Deposited','Presented')", "docstatus = 1"]
    values = {}

    if filters.get("company"):
        conds.append("company = %(company)s")
        values["company"] = filters["company"]

    rows = frappe.db.sql(
        f"""
        SELECT name, cheque_no, cheque_type, party, amount, currency, due_date, status
        FROM  `tabCheque`
        WHERE {" AND ".join(conds)}
        ORDER BY due_date ASC
        """,
        values,
        as_dict=True,
    )

    today_date = getdate(today())
    result = []
    for row in rows:
        age = date_diff(today_date, row.due_date) if row.due_date else 0
        amt = float(row.get("amount") or 0)
        result.append({
            **row,
            "age_days":       age,
            "bucket_0_7":     amt if 0 <= age <= 7  else 0,
            "bucket_8_14":    amt if 8 <= age <= 14 else 0,
            "bucket_15_30":   amt if 15 <= age <= 30 else 0,
            "bucket_over_30": amt if age > 30        else 0,
        })
    return result
