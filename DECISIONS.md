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

**Consequence, and how it is handled — amended after review.** A cheque with no
linked Payment Entry would clear with *no* accounting entry at all: recorded as
collected while the books never hear about it. The first cut warned about this
and let it through. Ahmed's call, and the right one: **a warning on a screen is
not a control.** Clear and Cash Clear now *refuse* the transition when the cheque
has neither a Payment Entry nor a Journal Entry behind it.

Both transition paths are gated — the workflow save through
`before_update_after_submit`, and the whitelisted UI endpoint through
`_validate_transition` — because `change_cheque_status` writes with
`frappe.db.set_value` and would otherwise sail straight past a form-only check.

A **System Manager** can still clear such a cheque deliberately: tick
`clearance_override`, give a reason, and the clearance goes through. The override
is not silent — it writes a Cheque Event naming the user who authorised it and
quoting their reason, and only a System Manager can set the flag (checked on
every save, so it cannot be pre-set by someone else and relied on later).

Production CHQ-2026-00001 (the outgoing electricity cheque, no PE link) is
exactly the case this catches. It will refuse to clear until someone either links
the Payment Entry or consciously overrides.

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

---

## D9 — A blank `reminder_days` falls back to 3, so "overdue only" is not configurable

`reminder_days` postdates the existing Settings row, so it reads back as 0 on any
site that has not touched it. Treating 0 literally would silently shrink the
digest to overdue-only on every upgraded site — a quiet loss of the feature.

It is coerced to the field default of 3 instead. The cost is that an admin cannot
*deliberately* configure an overdue-only digest by setting 0; a blank field is far
more likely than that intent. One-line reversal if Ahmed wants 0 honoured.

---

## D10 — Arabic translations are site-wide, so the app translates only what core does not

Frappe's translation namespace is flat: an entry in an app's `ar.csv` overrides
that source string for **every** app on the site. The first cut shipped
corrections for generic strings and flagged two (`Issue`, `Clear`) as
context-dependent risks.

Ahmed's call, applied here: **no entry may shadow a string that frappe or erpnext
already translates.** Not just those two — the rule is categorical, because the
next well-meaning correction has the same failure mode. 39 entries were removed;
201 app-unique ones remain.

`cheque_tracker/tests/verify_translations.py` enforces it, comparing `ar.csv`
against the msgids in `frappe/locale/ar.po` and `erpnext/locale/ar.po` (16,069
strings). It runs in the suite and standalone:

    bench --site <site> execute cheque_tracker.tests.verify_translations.run

**The cost, stated plainly.** Some of the removed corrections were fixing genuinely
bad core Arabic — `Draft` → "مشروع" ("project"), `Due Date` → "بسبب تاريخ"
(nonsense), `Amount` → "كمية" ("quantity"), `Custodian` → "وصي" ("legal
guardian"). Those now render with core's wording again. That is the deliberate
trade: this app does not get to silently rewrite the Arabic of the whole site,
and core's mistakes belong upstream. If EEI wants them fixed, the route is a PR
to frappe/erpnext or a site-level Translation record — not an app override.

Words the app genuinely owns (تظهير, دفتر شيكات, مُظهَّر, عدم كفاية الرصيد, the
workflow actions it invented, and every one of its own messages) are unaffected.

The completeness checks now assert that each status, workflow action and bounce
reason has Arabic from *somewhere* — ours or core's — so a new untranslated
status still turns the suite red.

---

## D11 — There were two `tasks.py` files; the module-level copy was deleted

`cheque_tracker/tasks.py` and `cheque_tracker/cheque_tracker/tasks.py` were
byte-identical and both git-tracked, with `hooks.py` pointing at the inner one.
Anyone opening "tasks.py" had even odds of editing the dead copy, and the two
would have drifted the moment either was touched.

Consolidated onto the app-level `cheque_tracker/tasks.py` — the conventional
Frappe location — with both scheduled jobs registered under
`cheque_tracker.tasks.*` and the duplicate removed. Both paths verified to
resolve through `frappe.get_attr`, the same call `ScheduledJobType.execute`
makes.

---

## D12 — The Cairo web font is not embedded in the print formats

`EEI_PRINT_DESIGN_REFERENCE` §1 keeps a Google-Fonts `@import` for Cairo. The
print formats drop it: PDF rendering happens server-side with no outbound
network, so the import silently fails and costs a DNS timeout on every render.
The formats fall back through a local Arabic stack instead. If EEI wants Cairo
specifically, the fix is to ship the font file with the app and `@font-face` it
from `/assets/`, not to fetch it at render time.

---

## D13 — What the maturity ladder counts, and what "bounced" means

Three judgment calls inside the new reports, each also documented where it lives:

- **The ladder excludes Cancelled, Replaced and Returned cheques.** A replaced
  cheque's cash flow is already represented by its replacement; counting both
  would double that month's expected inflow.
- **Bounce counting is event-based ("ever bounced"), not status-based.** A cheque
  that bounced and was later cleared still bounced — a status-based count would
  quietly forgive it and understate a customer's true rate.
- **Bounce rate covers Incoming cheques with `party_type = Customer` only.** An
  outgoing cheque of ours that bounced is our failure, not the customer's, and
  mixing the two would make the report unusable for its purpose.

---

## D14 — Batch members inherit the batch's bank account

Routing the cascade through `change_cheque_status` (§5.2) means each member must
satisfy the same preconditions a single deposit does, including having a bank
account. Members without one now inherit the batch's, because depositing *is*
naming the account the cheque goes into. The alternative — failing each member
with "Bank Account is required" — would be a confusing way to say "this batch has
no bank account".

---

## D15 — Cairo ships as static instances derived from the variable font

`google/fonts` no longer publishes static Cairo faces: `ofl/cairo` contains only
`Cairo[slnt,wght].ttf` and there is no `static/` directory. The three faces this
app ships are instances pinned from that canonical binary at `wght` 400 / 600 /
700 with `slnt = 0`, which is how Google's own `static/` folders are produced.

Shipping the variable file directly would have been simpler and wrong: wkhtmltopdf
embeds QtWebKit, which has no variable-font support, so SemiBold and Bold would
have rendered identically to Regular. A test asserts the shipped faces carry no
`fvar` table.

TrueType rather than woff2 for the same reason — QtWebKit cannot read the modern
compressed formats. The SIL OFL licence ships alongside the fonts.

Worth recording: the formats already *named* `'Cairo'` in their font stack but
never loaded it, so Arabic had been rendering in the Segoe UI / Tahoma fallback
in the desk as well as in PDF. This is a fix, not just a PDF hardening.

---

## D16 — Un-clear does not touch the Payment Entry

Reversing a clearance clears `cleared_date` and records who did it and why, but
deliberately leaves a submitted Payment Entry alone.

Auto-cancelling it would be the tidy-looking choice and the wrong one: cancelling
a submitted PE reverses its GL entries and can break links held by documents the
tracker cannot see — a reconciliation, an allocation against an invoice. That is
an accounting decision with consequences beyond this app, so it belongs to a
person. The un-clear tells them plainly, in a msgprint and on the timeline, that
the PE is still posted and needs cancelling or amending by hand.

The transition is System Manager only, and the reason is mandatory — a reversal
with no explanation is worse than no reversal at all when someone audits it later.

---

## D17 — References may be attached after submit, but not repointed

`reference_doctype` / `reference_name` became `allow_on_submit` because the v1.2
clearance gate requires a Payment Entry, and cheques submitted before their PE
existed had no way to get one. Production was bridging this with two Property
Setters, which grant the edit with no rules attached.

The app's version is bounded: after submit a reference may be **filled in when
empty**, but changing or clearing one that is already set is System Manager only.
Attaching a missing reference is routine bookkeeping; repointing a submitted
cheque moves the money to a different document after the fact. The §4.5.3
amount/party validation runs either way, so a reference cannot be attached that
disagrees with the cheque.
