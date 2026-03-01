# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"fieldname": "name",         "label": _("Cheque"),       "fieldtype": "Link",   "options": "Cheque",       "width": 150},
        {"fieldname": "cheque_type",  "label": _("Type"),         "fieldtype": "Data",                              "width": 90},
        {"fieldname": "cheque_no",    "label": _("Cheque No"),    "fieldtype": "Data",                              "width": 120},
        {"fieldname": "party_type",   "label": _("Party Type"),   "fieldtype": "Data",                              "width": 100},
        {"fieldname": "party",        "label": _("Party"),        "fieldtype": "Data",                              "width": 160},
        {"fieldname": "amount",       "label": _("Amount"),       "fieldtype": "Currency",                          "width": 130},
        {"fieldname": "currency",     "label": _("Currency"),     "fieldtype": "Link",   "options": "Currency",     "width": 80},
        {"fieldname": "due_date",     "label": _("Due Date"),     "fieldtype": "Date",                              "width": 110},
        {"fieldname": "drawee_bank",  "label": _("Drawee Bank"),  "fieldtype": "Link",   "options": "Bank",         "width": 140},
        {"fieldname": "bank_account", "label": _("Bank Account"), "fieldtype": "Link",   "options": "Bank Account", "width": 160},
        {"fieldname": "company",      "label": _("Company"),      "fieldtype": "Link",   "options": "Company",      "width": 140},
        {"fieldname": "remarks",      "label": _("Remarks"),      "fieldtype": "Data",                              "width": 200},
    ]


def get_data(filters):
    conds  = ["status = 'Bounced'", "docstatus = 1"]
    values = {}

    if filters.get("company"):
        conds.append("company = %(company)s");       values["company"]    = filters["company"]
    if filters.get("cheque_type"):
        conds.append("cheque_type = %(cheque_type)s"); values["cheque_type"] = filters["cheque_type"]
    if filters.get("from_date"):
        conds.append("due_date >= %(from_date)s");   values["from_date"]  = filters["from_date"]
    if filters.get("to_date"):
        conds.append("due_date <= %(to_date)s");     values["to_date"]    = filters["to_date"]
    if filters.get("party"):
        conds.append("party = %(party)s");           values["party"]      = filters["party"]

    return frappe.db.sql(
        f"""
        SELECT name, cheque_type, cheque_no, party_type, party,
               amount, currency, due_date, drawee_bank, bank_account, company, remarks
        FROM  `tabCheque`
        WHERE {" AND ".join(conds)}
        ORDER BY due_date DESC
        """,
        values,
        as_dict=True,
    )
