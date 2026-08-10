# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""§5.3.1 — the treasury maturity ladder.

Monthly buckets over `due_date`: what comes in, what goes out, the net for the
month, and the running cash position. The cumulative column is the point of the
report — a month can look harmless on its own and still be the one that takes
the position negative.

Two deliberate choices:

* **Months are contiguous.** A month with no cheque still gets a row, because a
  cumulative series with holes in the x-axis lies about when the position turns.
* **Cancelled / Replaced / Returned cheques are excluded.** No cash will ever
  move on them: a replaced cheque's flow is already represented by the
  replacement, and counting both would double the month.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_first_day, getdate

# A ladder projects cash that will actually move. These three statuses are
# terminal-dead: the cheque is off the table and its money either never arrives
# or arrives under the name of a different cheque (Replaced → the replacement).
DEAD_STATUSES = ("Cancelled", "Replaced", "Returned")

# Guard against a filter range like 1900→2999 generating 13,000 empty rows.
# Past the cap the axis falls back to the months the data actually occupies.
MAX_MONTHS = 240


def execute(filters=None):
    filters = filters or {}
    currency = _company_currency(filters.get("company"))
    rows = _fetch(filters)
    buckets = _bucket_by_month(rows)
    data = _build_ladder(_month_axis(filters, buckets), buckets, currency)
    return get_columns(), data, None, get_chart(data, currency)


def get_columns():
    return [
        {"fieldname": "month",              "label": _("Month"),          "fieldtype": "Data",                            "width": 100},
        {"fieldname": "incoming_count",     "label": _("Incoming #"),     "fieldtype": "Int",                             "width": 100},
        {"fieldname": "incoming_amount",    "label": _("Incoming"),       "fieldtype": "Currency", "options": "currency", "width": 150},
        {"fieldname": "outgoing_count",     "label": _("Outgoing #"),     "fieldtype": "Int",                             "width": 100},
        {"fieldname": "outgoing_amount",    "label": _("Outgoing"),       "fieldtype": "Currency", "options": "currency", "width": 150},
        {"fieldname": "net_amount",         "label": _("Net"),            "fieldtype": "Currency", "options": "currency", "width": 150},
        {"fieldname": "cumulative_amount",  "label": _("Cumulative Net"), "fieldtype": "Currency", "options": "currency", "width": 160},
        {"fieldname": "currency",           "label": _("Currency"),       "fieldtype": "Link",     "options": "Currency", "width": 90},
    ]


def _company_currency(company):
    if company:
        return frappe.get_cached_value("Company", company, "default_currency")
    return frappe.defaults.get_global_default("currency")


def _fetch(filters):
    conds = [
        "docstatus = 1",
        "due_date IS NOT NULL",
        "status NOT IN %(dead)s",
    ]
    values = {"dead": DEAD_STATUSES}

    if filters.get("company"):
        conds.append("company = %(company)s");         values["company"]     = filters["company"]
    if filters.get("cheque_type"):
        conds.append("cheque_type = %(cheque_type)s"); values["cheque_type"] = filters["cheque_type"]
    if filters.get("from_date"):
        conds.append("due_date >= %(from_date)s");     values["from_date"]   = filters["from_date"]
    if filters.get("to_date"):
        conds.append("due_date <= %(to_date)s");       values["to_date"]     = filters["to_date"]

    return frappe.db.sql(
        f"""
        SELECT name, cheque_type, amount, due_date
        FROM  `tabCheque`
        WHERE {" AND ".join(conds)}
        ORDER BY due_date ASC
        """,
        values,
        as_dict=True,
    )


def _month_key(date_value):
    return getdate(date_value).strftime("%Y-%m")


def _bucket_by_month(rows):
    """{'2026-08': {'incoming_count': .., 'incoming_amount': .., ...}}"""
    buckets = {}
    for row in rows:
        bucket = buckets.setdefault(
            _month_key(row.due_date),
            {"incoming_count": 0, "incoming_amount": 0.0, "outgoing_count": 0, "outgoing_amount": 0.0},
        )
        side = "incoming" if row.cheque_type == "Incoming" else "outgoing"
        bucket[f"{side}_count"] += 1
        bucket[f"{side}_amount"] += flt(row.amount)
    return buckets


def _month_axis(filters, buckets):
    """Every month the ladder must show, chronologically.

    The filter range wins when given — an empty month inside the range the
    treasurer asked for is itself information. Otherwise the axis spans the data.
    """
    if filters.get("from_date") and filters.get("to_date"):
        start, end = get_first_day(getdate(filters["from_date"])), get_first_day(getdate(filters["to_date"]))
        if start <= end:
            months, cursor = [], start
            while cursor <= end and len(months) < MAX_MONTHS:
                months.append(cursor.strftime("%Y-%m"))
                cursor = get_first_day(add_months(cursor, 1))
            if cursor > end:
                return months
            # Range too wide to enumerate — fall through to the data-driven axis.

    if not buckets:
        return []

    start = get_first_day(getdate(min(buckets) + "-01"))
    end = get_first_day(getdate(max(buckets) + "-01"))
    months, cursor = [], start
    while cursor <= end and len(months) < MAX_MONTHS:
        months.append(cursor.strftime("%Y-%m"))
        cursor = get_first_day(add_months(cursor, 1))
    return months


def _build_ladder(months, buckets, currency):
    empty = {"incoming_count": 0, "incoming_amount": 0.0, "outgoing_count": 0, "outgoing_amount": 0.0}
    data, running = [], 0.0

    for month in months:
        bucket = buckets.get(month, empty)
        net = flt(bucket["incoming_amount"]) - flt(bucket["outgoing_amount"])
        running += net
        data.append(
            {
                "month":             month,
                "incoming_count":    bucket["incoming_count"],
                "incoming_amount":   flt(bucket["incoming_amount"]),
                "outgoing_count":    bucket["outgoing_count"],
                "outgoing_amount":   flt(bucket["outgoing_amount"]),
                "net_amount":        net,
                "cumulative_amount": running,
                "currency":          currency,
            }
        )
    return data


def get_chart(data, currency):
    if not data:
        return None

    return {
        "data": {
            "labels": [row["month"] for row in data],
            "datasets": [
                {"name": _("Incoming"),       "chartType": "bar",  "values": [row["incoming_amount"] for row in data]},
                {"name": _("Outgoing"),       "chartType": "bar",  "values": [row["outgoing_amount"] for row in data]},
                {"name": _("Cumulative Net"), "chartType": "line", "values": [row["cumulative_amount"] for row in data]},
            ],
        },
        "type": "axis-mixed",
        "fieldtype": "Currency",
        # `options` names the key the formatter reads the currency code off the
        # chart dict with (frappe/public/js/frappe/model/meta.js:319).
        "options": "currency",
        "currency": currency,
        "barOptions": {"stacked": 0},
    }
