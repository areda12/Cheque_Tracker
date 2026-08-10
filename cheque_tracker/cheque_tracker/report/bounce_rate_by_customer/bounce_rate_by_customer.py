# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""§5.3.3 — which customers actually pay, per cheque handed over.

One row per customer: how many cheques they gave us in the window, how many
bounced, the rate, the money involved, and why the bounces happened. The reason
breakdown is the actionable half — "Insufficient Funds" is a credit-limit
conversation, "Signature Mismatch" is a paperwork one.

Two deliberate choices:

* **A cheque counts as bounced if it EVER bounced**, not only if it is sitting in
  Bounced right now. §4.4 made Bounced non-terminal (banks re-present PDCs), so a
  status-only count would quietly forgive every customer whose cheque was
  re-deposited and cleared on the second attempt — exactly the customers this
  report is for. The Cheque Event timeline is the evidence.
* **Incoming only.** An outgoing cheque to a customer that bounced is our
  failure, not theirs; scoring it against them would misread the ledger.
"""

import frappe
from frappe import _
from frappe.utils import flt

NOT_SPECIFIED = "not_specified"


def execute(filters=None):
    filters = filters or {}
    currency = _company_currency(filters.get("company"))
    reasons = _bounce_reasons()
    return get_columns(reasons), get_data(filters, reasons, currency)


def _bounce_reasons():
    """The reason vocabulary, read off the doctype so it cannot drift.

    Adding a reason to the Cheque select adds a column here on the next migrate;
    a hard-coded list would silently stop counting it.
    """
    options = frappe.get_meta("Cheque").get_field("bounce_reason").options or ""
    return [reason.strip() for reason in options.split("\n") if reason.strip()]


def get_columns(reasons):
    columns = [
        {"fieldname": "party",           "label": _("Customer"),        "fieldtype": "Link",     "options": "Customer",  "width": 200},
        {"fieldname": "total_cheques",   "label": _("Cheques"),         "fieldtype": "Int",                              "width": 90},
        {"fieldname": "total_amount",    "label": _("Total Value"),     "fieldtype": "Currency", "options": "currency",  "width": 140},
        {"fieldname": "bounced_cheques", "label": _("Bounced"),         "fieldtype": "Int",                              "width": 90},
        {"fieldname": "bounce_pct",      "label": _("Bounce %"),        "fieldtype": "Percent",  "precision": 2,         "width": 100},
        {"fieldname": "bounced_amount",  "label": _("Bounced Value"),   "fieldtype": "Currency", "options": "currency",  "width": 140},
        {"fieldname": "bounced_value_pct", "label": _("Bounced Value %"), "fieldtype": "Percent", "precision": 2,        "width": 130},
    ]

    columns += [
        {"fieldname": frappe.scrub(reason), "label": _(reason), "fieldtype": "Int", "width": 120}
        for reason in reasons
    ]
    # Bounces predating the §4.4 mandatory-reason rule have no reason recorded.
    # Without this bucket the breakdown would not add up to Bounced, and a reader
    # would trust a total that is short.
    columns.append({"fieldname": NOT_SPECIFIED, "label": _("Not Specified"), "fieldtype": "Int", "width": 120})
    columns.append({"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "width": 80})
    return columns


def _company_currency(company):
    if company:
        return frappe.get_cached_value("Company", company, "default_currency")
    return frappe.defaults.get_global_default("currency")


def get_data(filters, reasons, currency):
    rows = _fetch(filters)
    reason_fields = {reason: frappe.scrub(reason) for reason in reasons}
    per_party = {}

    for row in rows:
        party = per_party.setdefault(row.party, _empty_party_row(row.party, reason_fields, currency))
        party["total_cheques"] += 1
        party["total_amount"] += flt(row.amount)

        if not row.ever_bounced:
            continue

        party["bounced_cheques"] += 1
        party["bounced_amount"] += flt(row.amount)
        party[reason_fields.get(row.bounce_reason, NOT_SPECIFIED)] += 1

    data = list(per_party.values())
    for party in data:
        # A customer only reaches this report by having at least one cheque, but
        # the guard stays: a zero here would be a ZeroDivisionError in a report a
        # credit decision is made from.
        party["bounce_pct"] = _pct(party["bounced_cheques"], party["total_cheques"])
        party["bounced_value_pct"] = _pct(party["bounced_amount"], party["total_amount"])

    data.sort(key=lambda r: (-r["bounce_pct"], -r["bounced_amount"], r["party"]))
    return data


def _pct(part, whole):
    return flt((flt(part) / flt(whole)) * 100, 2) if flt(whole) else 0.0


def _empty_party_row(party, reason_fields, currency):
    row = {
        "party":             party,
        "total_cheques":     0,
        "total_amount":      0.0,
        "bounced_cheques":   0,
        "bounce_pct":        0.0,
        "bounced_amount":    0.0,
        "bounced_value_pct": 0.0,
        "currency":          currency,
    }
    row.update({fieldname: 0 for fieldname in reason_fields.values()})
    row[NOT_SPECIFIED] = 0
    return row


def _fetch(filters):
    conds = [
        "c.docstatus = 1",
        "c.party_type = 'Customer'",
        "c.cheque_type = 'Incoming'",
        "c.party IS NOT NULL",
        "c.party != ''",
    ]
    values = {}

    if filters.get("company"):
        conds.append("c.company = %(company)s");   values["company"]   = filters["company"]
    if filters.get("from_date"):
        conds.append("c.due_date >= %(from_date)s"); values["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conds.append("c.due_date <= %(to_date)s");   values["to_date"]   = filters["to_date"]

    return frappe.db.sql(
        f"""
        SELECT c.name, c.party, c.amount, c.status, c.bounce_reason,
               (c.status = 'Bounced'
                OR EXISTS (SELECT 1
                             FROM `tabCheque Event` ce
                            WHERE ce.parent = c.name
                              AND ce.parenttype = 'Cheque'
                              AND ce.event_type = 'Bounced')) AS ever_bounced
        FROM  `tabCheque` c
        WHERE {" AND ".join(conds)}
        """,
        values,
        as_dict=True,
    )
