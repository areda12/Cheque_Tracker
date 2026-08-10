"""§4.8 — the v1.2 scenario matrix, driven through the real workflow.

Every scenario moves a cheque with `frappe.model.workflow.apply_workflow`, the
same call the desk makes, rather than by setting `status` directly. A test that
writes the field it is supposed to be testing proves nothing.

Two entry points:

    bench --site <site> execute cheque_tracker.tests.e2e.run_all
    bench --site <site> run-tests --app cheque_tracker --module cheque_tracker.tests.test_e2e

`run_all` prints a pass/fail table and raises if anything failed, so it is usable
as a gate in its own right.
"""

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, flt, today

from cheque_tracker.tests.utils import get_test_env

# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def _unique_no(prefix):
    return f"{prefix}-{frappe.generate_hash(length=8)}"


def attach_payment_entry(cheque, env=None):
    """Link a draft Payment Entry to `cheque`, the way real data looks.

    Since the clearance gate landed, a cheque with no Payment Entry or Journal
    Entry cannot be cleared at all — so every scenario that ends in Cleared needs
    one, and building them this way keeps the matrix on the intended flow rather
    than on the System Manager override.

    `mode_of_payment` is deliberately left blank: setting it to "Cheque" would
    trip the §4.5.1 mirror hook and spawn a *second* Cheque for this PE. The
    mirror has its own tests; here we just need the link.
    """
    env = env or get_test_env()
    receive = cheque.cheque_type == "Incoming"

    pe = frappe.get_doc(
        {
            "doctype": "Payment Entry",
            "payment_type": "Receive" if receive else "Pay",
            "company": env["company"],
            "posting_date": today(),
            "party_type": cheque.party_type,
            "party": cheque.party,
            "paid_from": env["debtors"] if receive else env["bank_gl_account"],
            "paid_to": env["bank_gl_account"] if receive else env["creditors"],
            "paid_amount": flt(cheque.amount),
            "received_amount": flt(cheque.amount),
            "source_exchange_rate": 1,
            "target_exchange_rate": 1,
            "reference_no": cheque.cheque_no,
            "reference_date": cheque.due_date,
        }
    )
    pe.flags.ignore_permissions = True
    pe.insert(ignore_permissions=True)

    cheque.db_set("reference_doctype", "Payment Entry", update_modified=False)
    cheque.db_set("reference_name", pe.name, update_modified=False)
    cheque.reload()
    return pe


def make_incoming(env=None, submit=True, with_payment_entry=True, **overrides):
    env = env or get_test_env()
    cheque = frappe.new_doc("Cheque")
    cheque.update(
        {
            "cheque_type": "Incoming",
            "company": env["company"],
            "party_type": "Customer",
            "party": env["customer"],
            "amount": 5000,
            "currency": env["currency"],
            "received_date": today(),
            "due_date": add_days(today(), 30),
            "cheque_no": _unique_no("E2E"),
            "drawee_bank": env["drawee_bank"],
            "drawer_name": env["customer"],
            "bank_account": env["bank_account"],
        }
    )
    cheque.update(overrides)
    cheque.flags.ignore_permissions = True
    cheque.insert(ignore_permissions=True)
    if submit:
        cheque.submit()
        cheque.reload()
    if with_payment_entry:
        attach_payment_entry(cheque, env)
    return cheque


def make_outgoing(env=None, submit=True, start=None, with_payment_entry=True, **overrides):
    env = env or get_test_env()
    from cheque_tracker.cheque_tracker.doctype.cheque_book.test_cheque_book import (
        make_cheque_book,
    )

    # A fresh book per scenario keeps leaf allocation independent — and keeps the
    # §4.5.4 duplicate guard from firing on a cheque number a previous scenario
    # already used. `cint` on a hex hash returns 0, so parse it as hex.
    start = start or (600000 + int(frappe.generate_hash(length=8), 16) % 300000)
    book = make_cheque_book(start, start + 5, company=env["company"], bank_account=env["bank_account"])
    book.submit()

    cheque = frappe.new_doc("Cheque")
    cheque.update(
        {
            "cheque_type": "Outgoing",
            "company": env["company"],
            "party_type": "Supplier",
            "party": env["supplier"],
            "amount": 3000,
            "currency": env["currency"],
            "issue_date": today(),
            "due_date": add_days(today(), 15),
            "cheque_book": book.name,
            "cheque_no": "PLACEHOLDER",
            "bank_account": env["bank_account"],
            "drawee_bank": env["drawee_bank"],
        }
    )
    cheque.update(overrides)
    cheque.flags.ignore_permissions = True
    cheque.insert(ignore_permissions=True)
    if submit:
        cheque.submit()
        cheque.reload()
    if with_payment_entry:
        attach_payment_entry(cheque, env)
    return cheque


def act(cheque, action):
    """Apply a workflow action the way the desk does, then reload."""
    cheque.reload()
    apply_workflow(cheque, action)
    cheque.reload()
    return cheque


def set_fields(cheque, **values):
    """Write allow_on_submit fields before a transition.

    apply_workflow calls doc.load_from_db() first (frappe/model/workflow.py:123),
    discarding anything unsaved — so fields a transition depends on must be
    persisted before the action, never alongside it.
    """
    cheque.reload()
    cheque.update(values)
    cheque.flags.ignore_permissions = True
    cheque.save(ignore_permissions=True)
    cheque.reload()
    return cheque


def events(cheque_name, event_type=None):
    filters = {"parent": cheque_name, "parenttype": "Cheque"}
    if event_type:
        filters["event_type"] = event_type
    return frappe.get_all("Cheque Event", filters=filters, pluck="event_type")


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


# --------------------------------------------------------------------------
# scenarios
# --------------------------------------------------------------------------


def incoming_deposit_clear():
    """Draft → Received → Deposited → Cleared."""
    cheque = make_incoming()
    _assert(cheque.status == "Received", f"expected Received, got {cheque.status}")

    act(cheque, "Deposit")
    _assert(cheque.status == "Deposited", f"expected Deposited, got {cheque.status}")

    act(cheque, "Clear")
    _assert(cheque.status == "Cleared", f"expected Cleared, got {cheque.status}")
    _assert(cheque.cleared_date, "cleared_date was not stamped")

    logged = events(cheque.name)
    for expected in ("Created", "Received", "Deposited", "Cleared"):
        _assert(expected in logged, f"missing {expected} event; got {logged}")
    _assert(logged.count("Deposited") == 1, f"Deposited logged {logged.count('Deposited')} times")
    _assert(logged.count("Cleared") == 1, f"Cleared logged {logged.count('Cleared')} times")
    return {"cheque": cheque.name, "events": logged}


def incoming_cash_clear():
    """§4.2 — the cash path, which was unreachable in v1.1.x."""
    env = get_test_env()
    cheque = make_incoming(clearance_type="Cash", cash_account=env["cash_account"])
    _assert(cheque.status == "Received", f"expected Received, got {cheque.status}")

    act(cheque, "Cash Clear")
    _assert(cheque.status == "Cleared", f"cash clear did not land in Cleared: {cheque.status}")
    _assert(cheque.cleared_date, "cleared_date was not stamped on cash clearance")
    _assert("Cleared" in events(cheque.name), "no Cleared event")
    return {"cheque": cheque.name, "cash_account": cheque.cash_account}


def incoming_cash_cheque_cannot_deposit():
    """A cash cheque must not be depositable — the two paths are exclusive."""
    env = get_test_env()
    cheque = make_incoming(clearance_type="Cash", cash_account=env["cash_account"])
    try:
        act(cheque, "Deposit")
    except Exception:
        cheque.reload()
        _assert(cheque.status == "Received", f"status moved anyway: {cheque.status}")
        return {"cheque": cheque.name, "blocked": True}
    raise AssertionError("Deposit was allowed on a Cash-clearance cheque")


def incoming_endorse():
    """§4.3 — تظهير: the cheque is signed over to a supplier."""
    env = get_test_env()
    cheque = make_incoming()
    set_fields(
        cheque,
        endorsed_to_party_type="Supplier",
        endorsed_to_party=env["supplier"],
        endorsement_date=today(),
    )
    act(cheque, "Endorse")

    _assert(cheque.status == "Endorsed", f"expected Endorsed, got {cheque.status}")
    _assert("Endorsed" in events(cheque.name), "no Endorsed event")
    # §4.3 — custody left the company, so no internal holder may remain.
    _assert(not cheque.current_holder, f"current_holder survived endorsement: {cheque.current_holder}")
    return {"cheque": cheque.name, "endorsed_to": cheque.endorsed_to_party}


def incoming_endorse_requires_counterparty():
    """Endorsing without naming the counterparty must be refused."""
    cheque = make_incoming()
    try:
        act(cheque, "Endorse")
    except Exception:
        cheque.reload()
        _assert(cheque.status == "Received", f"status moved anyway: {cheque.status}")
        return {"cheque": cheque.name, "blocked": True}
    raise AssertionError("Endorse was allowed with no counterparty")


def incoming_bounce_redeposit_clear():
    """§4.4 — banks re-present PDCs routinely, so Bounced is not terminal."""
    cheque = make_incoming()
    act(cheque, "Deposit")

    set_fields(cheque, bounce_reason="Insufficient Funds")
    act(cheque, "Bounce")
    _assert(cheque.status == "Bounced", f"expected Bounced, got {cheque.status}")
    _assert(cheque.bounce_reason == "Insufficient Funds", "bounce_reason lost")

    act(cheque, "Re-deposit")
    _assert(cheque.status == "Deposited", f"re-deposit did not work: {cheque.status}")

    act(cheque, "Clear")
    _assert(cheque.status == "Cleared", f"expected Cleared, got {cheque.status}")

    logged = events(cheque.name)
    _assert(logged.count("Bounced") == 1, f"Bounced logged {logged.count('Bounced')} times")
    _assert(logged.count("Deposited") == 2, f"expected two Deposited events, got {logged}")
    return {"cheque": cheque.name, "events": logged}


def incoming_bounce_requires_reason():
    """§4.4 — a bounce with no reason is not actionable downstream."""
    cheque = make_incoming()
    act(cheque, "Deposit")
    try:
        act(cheque, "Bounce")
    except Exception:
        cheque.reload()
        _assert(cheque.status == "Deposited", f"status moved anyway: {cheque.status}")
        return {"cheque": cheque.name, "blocked": True}
    raise AssertionError("Bounce was allowed with no bounce_reason")


def bounce_replace():
    """Bounced → Replaced, with the audit chain linked both ways."""
    cheque = make_incoming()
    act(cheque, "Deposit")
    set_fields(cheque, bounce_reason="Signature Mismatch")
    act(cheque, "Bounce")

    replacement_no = _unique_no("E2E-REP")
    cheque.reload()
    replacement_name = cheque.create_replacement(
        cheque_no=replacement_no,
        issue_date=today(),
        due_date=add_days(today(), 45),
    )
    cheque.reload()

    _assert(cheque.status == "Replaced", f"expected Replaced, got {cheque.status}")
    _assert(cheque.replacement_cheque == replacement_name, "replacement link missing")
    original = frappe.db.get_value("Cheque", replacement_name, "original_cheque")
    _assert(original == cheque.name, f"back-link missing: {original}")
    return {"cheque": cheque.name, "replacement": replacement_name}


def outgoing_issue_handover_clear():
    """§4.1 — the outgoing lifecycle in its own vocabulary."""
    cheque = make_outgoing()
    _assert(cheque.status == "Issued", f"outgoing cheque should be Issued, got {cheque.status}")

    act(cheque, "Hand Over")
    _assert(cheque.status == "Handed Over", f"expected Handed Over, got {cheque.status}")
    _assert(not cheque.current_holder, "current_holder survived Hand Over")

    act(cheque, "Present")
    _assert(cheque.status == "Presented", f"expected Presented, got {cheque.status}")

    act(cheque, "Clear")
    _assert(cheque.status == "Cleared", f"expected Cleared, got {cheque.status}")

    logged = events(cheque.name)
    for expected in ("Created", "Issued", "Handed Over", "Presented", "Cleared"):
        _assert(expected in logged, f"missing {expected} event; got {logged}")
    _assert("Received" not in logged, "outgoing cheque logged an incoming 'Received' event")
    return {"cheque": cheque.name, "events": logged}


def outgoing_bounce():
    cheque = make_outgoing()
    act(cheque, "Hand Over")
    set_fields(cheque, bounce_reason="Account Closed")
    act(cheque, "Bounce")

    _assert(cheque.status == "Bounced", f"expected Bounced, got {cheque.status}")
    _assert(cheque.bounce_reason == "Account Closed", "bounce_reason lost")
    return {"cheque": cheque.name, "reason": cheque.bounce_reason}


def outgoing_cannot_use_incoming_statuses():
    """§4.1 — the vocabularies must not cross."""
    cheque = make_outgoing()
    try:
        act(cheque, "Deposit")
    except Exception:
        cheque.reload()
        _assert(cheque.status == "Issued", f"status moved anyway: {cheque.status}")
        return {"cheque": cheque.name, "blocked": True}
    raise AssertionError("An outgoing cheque was allowed to Deposit")


SCENARIOS = [
    ("incoming: deposit → clear", incoming_deposit_clear),
    ("incoming: cash clear", incoming_cash_clear),
    ("incoming: cash cheque cannot deposit", incoming_cash_cheque_cannot_deposit),
    ("incoming: endorse", incoming_endorse),
    ("incoming: endorse requires counterparty", incoming_endorse_requires_counterparty),
    ("incoming: deposit → bounce → re-deposit → clear", incoming_bounce_redeposit_clear),
    ("incoming: bounce requires a reason", incoming_bounce_requires_reason),
    ("incoming: bounce → replace", bounce_replace),
    ("outgoing: issue → hand over → present → clear", outgoing_issue_handover_clear),
    ("outgoing: bounce", outgoing_bounce),
    ("outgoing: cannot use incoming statuses", outgoing_cannot_use_incoming_statuses),
]


def run_all(stop_on_failure=False):
    """Run the whole matrix, print a table, raise if anything failed."""
    get_test_env()
    results = []

    for name, fn in SCENARIOS:
        try:
            detail = fn()
            results.append({"scenario": name, "result": "PASS", "detail": detail})
        except Exception as exc:
            frappe.db.rollback()
            results.append({"scenario": name, "result": "FAIL", "detail": f"{type(exc).__name__}: {exc}"})
            if stop_on_failure:
                break

    width = max(len(r["scenario"]) for r in results)
    print("\n" + "=" * (width + 12))
    print("§4.8 SCENARIO MATRIX")
    print("=" * (width + 12))
    for row in results:
        print(f"{row['scenario']:<{width}}  {row['result']}")
        if row["result"] == "FAIL":
            print(f"{'':<{width}}  └─ {row['detail']}")
    failed = [r for r in results if r["result"] == "FAIL"]
    print("=" * (width + 12))
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    print("=" * (width + 12) + "\n")

    if failed:
        raise AssertionError(f"{len(failed)} scenario(s) failed: {[r['scenario'] for r in failed]}")

    return results
