# DECISIONS

Judgment calls made while building v1.1.6 → v1.2.0 → v1.3.0, with the reasoning
behind each. Anything here that Ahmed disagrees with is cheap to reverse now and
expensive later.

---

## D1 — Release numbers are 1.1.6 / 1.2.0 / 1.3.0, not 1.1.5 / 1.2 / 1.3

`BUILD_INSTRUCTIONS.md` describes the baseline as v1.1.4 and the bug release as
v1.1.5. `origin/main` already ships `__version__ = "1.1.5"` — the Replace-workflow
changeset merged as PR #16 on 2026-05-11. Shipping a second, different v1.1.5
would make the releases non-independently-deployable, which §0 explicitly
requires. Confirmed with Ahmed before starting: baseline is `origin/main`, which
is what production runs, and the three releases renumber.

Appendix A's behavioural facts were verified against the live 1.1.5 site and are
treated as authoritative wherever they differ from the version label.

---

## D2 — v1.2 removes the clearance Journal Entry (the tracker posts no GL)

**This is the most consequential decision in the build.**

v1.1.5 posts exactly one accounting document per cheque: a Journal Entry created
by `make_clearance_je` on the Clear action —

```
Incoming:  Dr Bank GL          Cr Debtors / Advance Received
Outgoing:  Dr Creditors / Advances Paid    Cr Bank GL
```

§4.5 replaces that with EEI's confirmed model: **the Payment Entry stays draft
until the cheque is actually collected, and clearing the cheque submits it.** A
submitted Payment Entry posts `Dr Bank / Cr Debtors` itself — the *same entry*
the clearance JE was making.

Keeping both would double-post every collection: two debits to the bank and two
credits to the receivable for one cheque. Over a year that silently doubles
recorded cash receipts.

§4.5.5 is explicit — "Deliberately **no GL postings from the tracker** in v1.2" —
so `make_clearance_je` is deleted. `cancel_clearance_je` is kept: cheques cleared
under v1.1.x still carry a `clearance_je` link that must be unwound if they are
ever cancelled, and the `clearance_je` field is kept read-only for that history.

**Consequence, stated plainly:** a cheque with no linked Payment Entry now
produces *no* accounting entry when it clears. That is the documented v1.2
behaviour, not an oversight. Production CHQ-2026-00001 (the outgoing electricity
cheque) is exactly such a cheque. Rather than let that pass silently, clearing a
cheque with no linked PE raises a visible orange message saying nothing was
posted and the collection must be recorded separately, and writes a Cheque Event
saying the same thing. Silence is what turns this into a year-end discovery.

`Cheque Tracker Settings.gl_posting_model` is the placeholder §4.5.5 asks for —
read-only, single option "Payment Entry only" — so a future Notes Receivable
model has somewhere to live.

---

## D3 — The duplicate guard only fires when `drawee_bank` is set

§4.5.4 keys the duplicate check on `cheque_no` + `drawee_bank` + `cheque_type`.
`drawee_bank` is mandatory on Incoming cheques but optional on Outgoing ones,
and with it blank the key is incomplete: two banks issue cheque number 9100 all
the time, so matching on the number alone rejects legitimate cheques. This was
not theoretical — it broke two tests immediately.

The guard therefore returns early when `drawee_bank` is empty. Outgoing cheques
without one are already protected by the unique index on
`Cheque Leaf (cheque_book, cheque_no)`, which is a stronger guarantee than the
app-level check anyway.

---

## D4 — "Endorsed To" gets a free-text companion field

§4.3 specifies `endorsed_to_party_type` (Supplier / Employee / **Other**) with
`endorsed_to_party` as a Dynamic Link. A Dynamic Link whose party type is
"Other" cannot validate — "Other" is not a DocType, and Frappe's link validation
throws on save. The same latent bug already exists on the Cheque's main
`party_type` field.

So `endorsed_to_party` stays a Dynamic Link for Supplier and Employee, and a new
`endorsed_to_other_name` (Data) holds the counterparty when the type is "Other".
Validation requires exactly one of them. This keeps the "Other" option §4.3 asks
for without shipping a guaranteed crash.

---

## D5 — Outgoing cheques enter at `Issued`, and the state order in the fixture is load-bearing

`frappe.model.workflow.set_workflow_state_on_action` force-overwrites the state
field on **any** submit — including a plain `doc.submit()` that never went
through the workflow — setting it to the *first* workflow state matching the
target docstatus. That is why an outgoing cheque landed in "Received": it was
simply the first `doc_status: 1` state in the list.

It does return early when the document already sits in a state with the right
docstatus, and `before_submit` runs before `_validate`
(frappe/model/document.py:479-480). So `before_submit` setting `Issued` for
outgoing / `Received` for incoming is honoured.

This is fragile in a way that is invisible from the fixture alone, so
`workflow.json` carries a comment recording it, and "Received" is deliberately
kept first as the safe fallback.

---

## D6 — The test environment is pinned, and the `skipTest` guards are gone

The suite resolved its fixtures with `frappe.get_all("Company", limit=1)`, which
orders by `modified desc`. The company each test ran against changed from run to
run, so every test carried a `skipTest` guard for the case where the lookup found
a company with no bank account. On a fresh site that meant 16 errors and 12
silent skips out of 34 tests — a suite that reported no failures while asserting
almost nothing.

Rather than make the guards more forgiving, the environment is now built
deterministically by `before_tests` and the guards were replaced with hard setup.
No assertion was weakened, skipped or deleted; three real defects surfaced
immediately once the tests actually ran.

---

## D7 — `payments` is installed on the test site only

ERPNext's `Payment Gateway Account` links to `Payment Gateway`, which lives in
the separate `payments` app. Frappe's test-record dependency walk follows that
link, so `bench run-tests --app cheque_tracker` could not start at all on an
ERPNext-only site — it died with `DocType Payment Gateway not found` before
running a single test.

Installing `payments` (version-16) on the local site fixes it. No app code
references `payments`, nothing about it ships in any release, and it is not a
deployment requirement — it is a local test-harness dependency, recorded in
ACCEPTANCE_REPORT.md.

---

## D8 — Cheque Event types were migrated, not just extended

Adding `Issued` and `Endorsed` to the event vocabulary leaves historical rows
saying "Received" on outgoing cheques, so the timeline would contradict the
document it belongs to. The v3_3 patch rewrites those rows as well as the status
field, with `update_modified=False` — a vocabulary rename is not a business event
and should not make every migrated cheque look edited today.
