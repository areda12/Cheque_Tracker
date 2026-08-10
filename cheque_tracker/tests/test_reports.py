# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""§5.5 — the three v1.3 Script Reports, executed the way the desk executes them.

Every test goes through `frappe.desk.query_report.run`, not through the report
module's `execute()`. Calling `execute()` directly would skip the half of the
stack that actually breaks in production: the Report doc must exist, be a Script
Report, resolve to a module on disk, and survive column normalisation and the
permission filter. A report that only passes when called as a Python function is
not a report.

The assertions are on row math, not on row counts:

* the ladder's monthly sums equal the sum of the cheques underneath them, and the
  cumulative column is the running total of the net column;
* custody age is measured from the transition into the current state, proven by
  backdating that event while leaving `received_date` where it was;
* the bounce rate equals bounced / total for a set whose answer is known by
  construction, including a cheque that bounced and was later cleared.

Isolation: each class builds its cheques inside a due-date window no other test
or seed touches (the ladder in 2031, the bounce report in 2032) or behind a
holder tag unique to the run, so "sum of the window" is exactly "sum of what this
test created" and the assertions can be equalities rather than ranges.
"""

import frappe
from frappe.desk.query_report import run
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate, now_datetime, today

from cheque_tracker.tests import e2e
from cheque_tracker.tests.utils import get_test_env

LADDER = "Cheque Maturity Ladder"
CUSTODY = "Cheques in Custody"
BOUNCE = "Bounce Rate by Customer"

REPORT_FOLDERS = {
    LADDER: "cheque_maturity_ladder",
    CUSTODY: "cheques_in_custody",
    BOUNCE: "bounce_rate_by_customer",
}


def _columns_by_fieldname(result):
    return {column["fieldname"]: column for column in result["columns"]}


def _amount_of(cheques):
    """Sum straight off the table — the number the report has to reproduce."""
    return flt(
        frappe.db.sql(
            "SELECT SUM(amount) FROM `tabCheque` WHERE name IN %(names)s",
            {"names": [cheque.name for cheque in cheques]},
        )[0][0]
    )


class TestReportsAreInstalled(FrappeTestCase):
    """The Report docs are DB rows created by migrate from the shipped JSON.

    A missing row here means the desk has no report at all, however correct the
    Python module is — and it is exactly what a mistyped `module` or a report
    folder that does not match `report_name` produces.
    """

    def test_all_three_reports_exist_as_standard_script_reports(self):
        for report_name in (LADDER, CUSTODY, BOUNCE):
            with self.subTest(report=report_name):
                self.assertTrue(
                    frappe.db.exists("Report", report_name),
                    f"Report {report_name!r} is not installed — run `bench migrate`",
                )
                doc = frappe.get_doc("Report", report_name)
                self.assertEqual(doc.report_type, "Script Report")
                self.assertEqual(doc.is_standard, "Yes")
                self.assertEqual(doc.module, "Cheque Tracker")
                self.assertEqual(doc.ref_doctype, "Cheque")
                self.assertFalse(doc.disabled)

    def test_reports_are_readable_by_the_treasury_roles(self):
        for report_name in (LADDER, CUSTODY, BOUNCE):
            with self.subTest(report=report_name):
                roles = {row.role for row in frappe.get_doc("Report", report_name).roles}
                self.assertIn("Treasury User", roles)
                self.assertIn("Cheque Auditor", roles)

    def test_module_path_resolves(self):
        # get_report_module_dotted_path scrubs report_name into the folder name;
        # a folder that does not match it fails at run time, not at migrate time.
        for report_name, folder in REPORT_FOLDERS.items():
            with self.subTest(report=report_name):
                self.assertEqual(frappe.scrub(report_name), folder)

    def test_a_treasury_user_can_actually_run_them(self):
        # Administrator bypasses both gates a real user hits: the report's own
        # role list and `report` permission on Cheque. Passing as Administrator
        # says nothing about whether the treasury can open the report.
        env = get_test_env()
        self.addCleanup(frappe.set_user, "Administrator")
        frappe.set_user(env["treasury_user"])

        for report_name in (LADDER, CUSTODY, BOUNCE):
            with self.subTest(report=report_name):
                result = run(report_name, filters={"company": env["company"]})
                self.assertIn("result", result)
                self.assertTrue(result["columns"])


# ====================================================================== #
#  §5.3.1 — Cheque Maturity Ladder                                        #
# ====================================================================== #


class TestChequeMaturityLadder(FrappeTestCase):
    """Buckets, net, and the running total, over a window nothing else occupies."""

    FROM_DATE = "2031-01-01"
    TO_DATE = "2031-12-31"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = get_test_env()

        # March: two incoming (12,000) against one outgoing (3,000) → net 9,000.
        cls.march_in = [
            e2e.make_incoming(cls.env, amount=5000, due_date="2031-03-15"),
            e2e.make_incoming(cls.env, amount=7000, due_date="2031-03-20"),
        ]
        cls.march_out = [e2e.make_outgoing(cls.env, amount=3000, due_date="2031-03-31")]
        # May incoming, June outgoing — so the cumulative line has to rise then fall.
        cls.may_in = [e2e.make_incoming(cls.env, amount=1000, due_date="2031-05-10")]
        cls.june_out = [e2e.make_outgoing(cls.env, amount=2000, due_date="2031-06-05")]

        cls.incoming = cls.march_in + cls.may_in
        cls.outgoing = cls.march_out + cls.june_out

        # Two cheques that must NOT reach the ladder: a returned one (no cash will
        # ever move) and an unsubmitted draft (docstatus 0). Both carry absurd
        # amounts so any leak shows up as an obviously wrong total.
        cls.returned = e2e.act(
            e2e.make_incoming(cls.env, amount=999_999, due_date="2031-03-05"), "Return"
        )
        cls.draft = e2e.make_incoming(cls.env, submit=False, amount=888_888, due_date="2031-04-01")

    def _run(self, **overrides):
        filters = {
            "company": self.env["company"],
            "from_date": self.FROM_DATE,
            "to_date": self.TO_DATE,
        }
        filters.update(overrides)
        return run(LADDER, filters=filters)

    def _by_month(self, result):
        return {row["month"]: row for row in result["result"]}

    def test_months_are_contiguous_across_the_filter_range(self):
        rows = self._run()["result"]
        self.assertEqual([row["month"] for row in rows], [f"2031-{m:02d}" for m in range(1, 13)])

    def test_monthly_buckets_equal_the_cheques_underneath_them(self):
        months = self._by_month(self._run())

        march = months["2031-03"]
        self.assertEqual(march["incoming_count"], 2)
        self.assertEqual(flt(march["incoming_amount"]), _amount_of(self.march_in))
        self.assertEqual(flt(march["incoming_amount"]), 12000.0)
        self.assertEqual(march["outgoing_count"], 1)
        self.assertEqual(flt(march["outgoing_amount"]), _amount_of(self.march_out))
        self.assertEqual(flt(march["net_amount"]), 9000.0)

        self.assertEqual(flt(months["2031-05"]["incoming_amount"]), 1000.0)
        self.assertEqual(flt(months["2031-06"]["outgoing_amount"]), 2000.0)

        # A month with nothing in it is a real row carrying real zeros.
        self.assertEqual(flt(months["2031-04"]["net_amount"]), 0.0)
        self.assertEqual(months["2031-04"]["incoming_count"], 0)

    def test_column_totals_equal_the_sum_of_the_underlying_cheques(self):
        rows = self._run()["result"]
        self.assertEqual(sum(flt(row["incoming_amount"]) for row in rows), _amount_of(self.incoming))
        self.assertEqual(sum(flt(row["outgoing_amount"]) for row in rows), _amount_of(self.outgoing))
        self.assertEqual(sum(flt(row["net_amount"]) for row in rows), 13000.0 - 5000.0)

    def test_returned_and_draft_cheques_never_reach_the_ladder(self):
        rows = self._run()["result"]
        # 999,999 landed in March and 888,888 in April; if either leaked, these
        # two months would not equal the numbers the submitted cheques justify.
        months = {row["month"]: row for row in rows}
        self.assertEqual(flt(months["2031-03"]["incoming_amount"]), 12000.0)
        self.assertEqual(flt(months["2031-04"]["incoming_amount"]), 0.0)
        self.assertEqual(self.returned.status, "Returned")
        self.assertEqual(self.draft.docstatus, 0)

    def test_cumulative_column_actually_accumulates(self):
        rows = self._run()["result"]

        running = 0.0
        for row in rows:
            running += flt(row["net_amount"])
            self.assertEqual(
                flt(row["cumulative_amount"]),
                running,
                f"cumulative broke at {row['month']}",
            )

        self.assertEqual(flt(rows[-1]["cumulative_amount"]), 8000.0)
        # The shape matters as much as the endpoint: up 9,000 in March, flat in
        # April, up to 10,000 in May, back down to 8,000 in June.
        months = {row["month"]: flt(row["cumulative_amount"]) for row in rows}
        self.assertEqual(
            [months["2031-03"], months["2031-04"], months["2031-05"], months["2031-06"]],
            [9000.0, 9000.0, 10000.0, 8000.0],
        )

    def test_cumulative_is_scoped_to_the_requested_window(self):
        # Opening the window in May must not silently carry March's 9,000 in.
        rows = self._run(from_date="2031-05-01", to_date="2031-06-30")["result"]
        self.assertEqual([row["month"] for row in rows], ["2031-05", "2031-06"])
        self.assertEqual(flt(rows[0]["cumulative_amount"]), 1000.0)
        self.assertEqual(flt(rows[1]["cumulative_amount"]), -1000.0)

    def test_cheque_type_filter_removes_the_other_side(self):
        rows = self._run(cheque_type="Incoming")["result"]
        self.assertEqual(sum(flt(row["outgoing_amount"]) for row in rows), 0.0)
        self.assertEqual(sum(flt(row["incoming_amount"]) for row in rows), _amount_of(self.incoming))
        self.assertEqual(flt(rows[-1]["cumulative_amount"]), _amount_of(self.incoming))

    def test_chart_mirrors_the_table(self):
        result = self._run()
        chart = result["chart"]
        self.assertTrue(chart, "the ladder returned no chart block")

        rows = result["result"]
        self.assertEqual(chart["data"]["labels"], [row["month"] for row in rows])

        datasets = {dataset["name"]: dataset["values"] for dataset in chart["data"]["datasets"]}
        self.assertEqual(datasets["Incoming"], [flt(row["incoming_amount"]) for row in rows])
        self.assertEqual(datasets["Outgoing"], [flt(row["outgoing_amount"]) for row in rows])
        self.assertEqual(datasets["Cumulative Net"], [flt(row["cumulative_amount"]) for row in rows])
        self.assertEqual(chart["fieldtype"], "Currency")

    def test_money_columns_are_currency_with_a_resolvable_option(self):
        result = self._run()
        columns = _columns_by_fieldname(result)
        for fieldname in ("incoming_amount", "outgoing_amount", "net_amount", "cumulative_amount"):
            with self.subTest(column=fieldname):
                self.assertEqual(columns[fieldname]["fieldtype"], "Currency")
                # The desk resolves a Currency column's `options` against the row
                # (frappe/public/js/frappe/model/meta.js:319), so the key it names
                # has to be present in every row.
                self.assertEqual(columns[fieldname]["options"], "currency")
        for row in result["result"]:
            self.assertEqual(row["currency"], self.env["currency"])

    def test_empty_window_returns_no_rows_and_no_chart(self):
        result = self._run(from_date="2035-01-01", to_date="2035-03-31")
        self.assertEqual(sum(flt(row["incoming_amount"]) for row in result["result"]), 0.0)
        self.assertEqual(sum(flt(row["outgoing_amount"]) for row in result["result"]), 0.0)


# ====================================================================== #
#  §5.3.2 — Cheques in Custody                                            #
# ====================================================================== #


class TestChequesInCustody(FrappeTestCase):
    """Who holds what, and for how long — with age proven to come from the event."""

    BACKDATED_DAYS = 10

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = get_test_env()
        # Unique per run, so the holder filter isolates this test's cheques from
        # every other cheque on the site.
        cls.tag = f"CUSTODY-{frappe.generate_hash(length=8)}"

        cls.received = e2e.make_incoming(cls.env, amount=1500, external_holder=cls.tag)
        cls.deposited = e2e.act(
            e2e.make_incoming(cls.env, amount=2500, external_holder=cls.tag), "Deposit"
        )
        cls.handed_over = e2e.act(
            e2e.make_outgoing(cls.env, amount=4500, external_holder=cls.tag), "Hand Over"
        )

        # Settled and gone: cleared cheques are not in anybody's custody.
        cls.cleared = e2e.act(
            e2e.act(e2e.make_incoming(cls.env, amount=3500, external_holder=cls.tag), "Deposit"),
            "Clear",
        )
        # Never submitted — a draft is a plan, not a custody position.
        cls.draft = e2e.make_incoming(cls.env, submit=False, amount=6500, external_holder=cls.tag)

        # Age must come from the transition into the current state, so push that
        # event back while leaving received_date on today. If the report read the
        # document date instead, this cheque would still show age 0.
        event = frappe.db.get_value(
            "Cheque Event",
            {"parent": cls.received.name, "parenttype": "Cheque", "event_type": "Received"},
            "name",
        )
        cls.backdated_to = add_days(now_datetime(), -cls.BACKDATED_DAYS)
        frappe.db.set_value("Cheque Event", event, "event_datetime", cls.backdated_to)

    def _run(self, **overrides):
        filters = {"company": self.env["company"], "holder": self.tag}
        filters.update(overrides)
        return run(CUSTODY, filters=filters)

    def _by_cheque(self, result):
        return {row["name"]: row for row in result["result"]}

    def test_only_cheques_still_in_custody_are_listed(self):
        rows = self._by_cheque(self._run())
        self.assertEqual(
            set(rows),
            {self.received.name, self.deposited.name, self.handed_over.name},
            "custody list does not match the v1.2 active vocabulary",
        )
        self.assertNotIn(self.cleared.name, rows)
        self.assertNotIn(self.draft.name, rows)

    def test_both_lifecycles_use_their_own_vocabulary(self):
        rows = self._by_cheque(self._run())
        self.assertEqual(rows[self.received.name]["status"], "Received")
        self.assertEqual(rows[self.deposited.name]["status"], "Deposited")
        self.assertEqual(rows[self.handed_over.name]["status"], "Handed Over")
        self.assertEqual(rows[self.handed_over.name]["cheque_type"], "Outgoing")

    def test_age_is_measured_from_the_transition_not_the_document_date(self):
        rows = self._by_cheque(self._run())
        row = rows[self.received.name]

        self.assertEqual(row["age_days"], self.BACKDATED_DAYS)
        self.assertEqual(getdate(row["custody_since"]), getdate(self.backdated_to))
        # The document still says it was received today: proof the age came from
        # the Cheque Event and not from received_date.
        self.assertEqual(getdate(self.received.received_date), getdate(today()))
        self.assertNotEqual(getdate(row["custody_since"]), getdate(self.received.received_date))

        # Everything else entered its state just now.
        self.assertEqual(rows[self.deposited.name]["age_days"], 0)
        self.assertEqual(getdate(rows[self.deposited.name]["custody_since"]), getdate(today()))

    def test_external_holder_becomes_the_group(self):
        for row in self._run()["result"]:
            with self.subTest(cheque=row["name"]):
                self.assertEqual(row["holder"], self.tag)
                self.assertEqual(row["holder_type"], "External")
                # §3.2.7a — custody left the company, so no User may be named.
                self.assertFalse(row["holder_user"])

    def test_rows_are_grouped_by_holder_oldest_first(self):
        rows = self._run()["result"]
        self.assertEqual([row["holder"] for row in rows], [self.tag] * len(rows))
        self.assertEqual([row["age_days"] for row in rows], sorted((r["age_days"] for r in rows), reverse=True))
        self.assertEqual(rows[0]["name"], self.received.name)

    def test_internal_holder_is_reported_as_a_user(self):
        held = e2e.make_incoming(self.env, amount=7500, current_holder=self.env["treasury_user"])
        rows = {row["name"]: row for row in self._run(holder=self.env["treasury_user"])["result"]}

        self.assertIn(held.name, rows)
        row = rows[held.name]
        self.assertEqual(row["holder_type"], "User")
        self.assertEqual(row["holder_user"], self.env["treasury_user"])
        self.assertEqual(
            row["holder"],
            frappe.db.get_value("User", self.env["treasury_user"], "full_name"),
        )

    def test_cheque_type_filter_narrows_the_list(self):
        rows = self._by_cheque(self._run(cheque_type="Outgoing"))
        self.assertEqual(set(rows), {self.handed_over.name})

    def test_amount_column_is_currency_and_rows_carry_the_cheque_currency(self):
        result = self._run()
        columns = _columns_by_fieldname(result)
        self.assertEqual(columns["amount"]["fieldtype"], "Currency")
        self.assertEqual(columns["amount"]["options"], "currency")
        self.assertEqual(columns["age_days"]["fieldtype"], "Int")
        for row in result["result"]:
            self.assertEqual(row["currency"], self.env["currency"])

    def test_amounts_match_the_documents(self):
        rows = self._by_cheque(self._run())
        for cheque in (self.received, self.deposited, self.handed_over):
            with self.subTest(cheque=cheque.name):
                self.assertEqual(flt(rows[cheque.name]["amount"]), flt(cheque.amount))


# ====================================================================== #
#  §5.3.3 — Bounce Rate by Customer                                       #
# ====================================================================== #


class TestBounceRateByCustomer(FrappeTestCase):
    """Five cheques, two of which bounced — so the answer is 40% by construction."""

    FROM_DATE = "2032-01-01"
    TO_DATE = "2032-12-31"
    DUE_DATE = "2032-02-10"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = get_test_env()

        cls.clean = [
            e2e.make_incoming(cls.env, amount=1000, due_date=cls.DUE_DATE) for _ in range(3)
        ]

        # Bounced and still bouncing.
        cls.bounced = e2e.act(e2e.make_incoming(cls.env, amount=2000, due_date=cls.DUE_DATE), "Deposit")
        e2e.set_fields(cls.bounced, bounce_reason="Insufficient Funds")
        e2e.act(cls.bounced, "Bounce")

        # Bounced, re-presented, and collected on the second attempt. §4.4 made
        # Bounced non-terminal, so this cheque's status is now Cleared — a
        # status-only count would forgive the customer for it.
        cls.recovered = e2e.act(
            e2e.make_incoming(cls.env, amount=4000, due_date=cls.DUE_DATE), "Deposit"
        )
        e2e.set_fields(cls.recovered, bounce_reason="Technical")
        e2e.act(cls.recovered, "Bounce")
        e2e.act(cls.recovered, "Re-deposit")
        e2e.act(cls.recovered, "Clear")

        cls.all_cheques = [*cls.clean, cls.bounced, cls.recovered]

    def _run(self, **overrides):
        filters = {
            "company": self.env["company"],
            "from_date": self.FROM_DATE,
            "to_date": self.TO_DATE,
        }
        filters.update(overrides)
        return run(BOUNCE, filters=filters)

    def _customer_row(self, result=None):
        result = result or self._run()
        rows = [row for row in result["result"] if row["party"] == self.env["customer"]]
        self.assertEqual(len(rows), 1, "expected exactly one row for the seeded customer")
        return rows[0]

    def test_counts_and_values_match_the_underlying_cheques(self):
        row = self._customer_row()
        self.assertEqual(row["total_cheques"], len(self.all_cheques))
        self.assertEqual(flt(row["total_amount"]), _amount_of(self.all_cheques))
        self.assertEqual(flt(row["total_amount"]), 3 * 1000.0 + 2000.0 + 4000.0)
        self.assertEqual(row["bounced_cheques"], 2)
        self.assertEqual(flt(row["bounced_amount"]), _amount_of([self.bounced, self.recovered]))
        self.assertEqual(flt(row["bounced_amount"]), 6000.0)

    def test_bounce_percentage_equals_bounced_over_total(self):
        row = self._customer_row()
        self.assertEqual(flt(row["bounce_pct"]), round(2 / 5 * 100, 2))
        self.assertEqual(
            flt(row["bounce_pct"]),
            round(row["bounced_cheques"] / row["total_cheques"] * 100, 2),
        )
        self.assertEqual(
            flt(row["bounced_value_pct"]),
            round(flt(row["bounced_amount"]) / flt(row["total_amount"]) * 100, 2),
        )

    def test_a_cheque_that_bounced_and_later_cleared_still_counts(self):
        self.recovered.reload()
        self.assertEqual(self.recovered.status, "Cleared")
        # It is only in the bounced count because the timeline remembers it.
        self.assertIn("Bounced", e2e.events(self.recovered.name))
        self.assertEqual(self._customer_row()["bounced_cheques"], 2)

    def test_reason_breakdown_adds_up_to_the_bounced_count(self):
        row = self._customer_row()
        reasons = [
            reason.strip()
            for reason in (frappe.get_meta("Cheque").get_field("bounce_reason").options or "").split("\n")
            if reason.strip()
        ]
        self.assertIn("Insufficient Funds", reasons)

        self.assertEqual(row["insufficient_funds"], 1)
        self.assertEqual(row["technical"], 1)
        self.assertEqual(row["signature_mismatch"], 0)
        self.assertEqual(row["account_closed"], 0)
        self.assertEqual(row["other"], 0)
        self.assertEqual(row["not_specified"], 0)

        breakdown = sum(row[frappe.scrub(reason)] for reason in reasons) + row["not_specified"]
        self.assertEqual(breakdown, row["bounced_cheques"], "reason breakdown does not add up")

    def test_reason_columns_come_from_the_doctype_vocabulary(self):
        columns = _columns_by_fieldname(self._run())
        reasons = [
            reason.strip()
            for reason in (frappe.get_meta("Cheque").get_field("bounce_reason").options or "").split("\n")
            if reason.strip()
        ]
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertIn(frappe.scrub(reason), columns)
                self.assertEqual(columns[frappe.scrub(reason)]["fieldtype"], "Int")

    def test_percentage_and_money_column_types(self):
        columns = _columns_by_fieldname(self._run())
        self.assertEqual(columns["bounce_pct"]["fieldtype"], "Percent")
        self.assertEqual(columns["bounce_pct"]["precision"], 2)
        for fieldname in ("total_amount", "bounced_amount"):
            with self.subTest(column=fieldname):
                self.assertEqual(columns[fieldname]["fieldtype"], "Currency")
                self.assertEqual(columns[fieldname]["options"], "currency")

    def test_a_window_with_no_cheques_is_empty_not_a_division_by_zero(self):
        result = self._run(from_date="2033-01-01", to_date="2033-12-31")
        self.assertEqual(result["result"], [])

    def test_outgoing_cheques_are_not_scored_against_the_customer(self):
        # A cheque WE drew in favour of a customer (a refund) bouncing is our
        # failure, not theirs. It shares the party, so only the direction filter
        # keeps it out of their score.
        refund = e2e.make_outgoing(
            self.env,
            amount=50_000,
            due_date=self.DUE_DATE,
            party_type="Customer",
            party=self.env["customer"],
        )
        self.assertEqual(refund.cheque_type, "Outgoing")
        self.assertEqual(refund.party, self.env["customer"])

        row = self._customer_row()
        self.assertEqual(row["total_cheques"], len(self.all_cheques))
        self.assertEqual(flt(row["total_amount"]), _amount_of(self.all_cheques))
