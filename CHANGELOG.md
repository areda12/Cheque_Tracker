# CHANGELOG

## v1.2.0 — State model + accounting integration (2026-08-10)

Feature release. Adds a schema migration and changes where the general ledger
posting comes from — read the accounting note below before deploying.

### Status vocabulary (§4.1) — BREAKING for anything filtering on status

Both directions shared one status list, so an *issued* outgoing cheque landed in
**Received** — which reads as "we were given a cheque", the opposite of what
happened. Root cause of the outgoing dashboard-filter bugs and of persistent user
confusion.

- Status options are now the superset: `Draft, Received, In Safe, Deposited,
  Endorsed, Issued, Handed Over, Presented, Cleared, Bounced, Returned,
  Cancelled, Replaced`.
- **Incoming:** Draft → Received → (In Safe) → Deposited → Cleared / Bounced /
  Returned, plus Endorsed and the cash path.
- **Outgoing:** Draft → Issued → Handed Over → (Presented) → Cleared / Bounced /
  Returned.
- Workflow rebuilt: 13 states, 31 transitions. The phantom `Submit` action —
  which had no Workflow Action Master and survived only because fixture import
  sets `ignore_links` — is gone.
- Migration patch `v3_3.split_status_vocabulary` moves existing Outgoing cheques
  from `Received` to `Issued` and rewrites their timeline rows to match, so the
  audit trail cannot contradict the document. Incoming cheques are untouched.
- All cards and charts updated to the new vocabulary.

### Cash clearance (§4.2)

`clearance_type = Cash` and `cash_account` existed since v1.1.4 but **no
transition used them** — a cash cheque could never reach Cleared by any route.
Added `Received / In Safe → Cash Clear → Cleared`, and depositing a cash cheque
is now refused so the two paths cannot both fire.

### Endorsement — تظهير (§4.3)

An incoming cheque signed over to a supplier as payment: standard Egyptian
practice, previously unrepresentable.

- New fields: `endorsed_to_party_type`, `endorsed_to_party`,
  `endorsed_to_other_name`, `endorsement_date`, `endorsement_payment_entry`.
- `Received / In Safe → Endorse → Endorsed`, requiring the counterparty and date,
  clearing the internal custodian and logging the counterparty.
- New **Endorsed** dashboard card. An endorsed cheque is deliberately excluded
  from Pending Receivable — once endorsed it is no longer our receivable.

### Bounce handling (§4.4)

- `bounce_reason` (Insufficient Funds / Signature Mismatch / Account Closed /
  Technical / Other) is **required** by the Bounce action and surfaces in the
  Bounced Cheques Register.
- New `Bounced → Re-deposit → Deposited` transition — banks re-present PDCs
  routinely, so Bounced is not a terminal state.

### Accounting (§4.5) — the tracker no longer posts to the general ledger

**Read this before deploying.** v1.1.x posted a clearance Journal Entry
(`Dr Bank / Cr Debtors`) when a cheque cleared. v1.2 implements EEI's confirmed
model instead: **the Payment Entry stays draft until the cheque is collected, and
clearing the cheque submits it.** A submitted Payment Entry posts that same
entry itself — keeping both would double-post every collection.

- `make_clearance_je` is removed. The linked Payment Entry is the only posting
  document (§4.5.5). `cancel_clearance_je` and the read-only `clearance_je` field
  remain so cheques cleared under v1.1.x can still be unwound.
- **Auto-create:** a draft Payment Entry with `mode_of_payment = Cheque` spawns a
  Draft Cheque prefilled from it, exactly once, and edits to the draft PE sync
  onto the still-draft cheque.
- **Clear ⇒ settle:** clearing stamps `cleared_date` and submits the linked draft
  PE through its workflow. If the acting user may not approve it, the cheque
  still clears, `pe_pending_submission` is flagged and a ToDo is raised for
  someone holding the approving role — the approval gate is never forced.
- **Validation:** amount mismatch between Cheque and Payment Entry throws; party
  and cheque-number mismatches warn.
- **Duplicate guard:** blocks a second non-cancelled Cheque with the same
  `cheque_no` + `drawee_bank` + `cheque_type`.
- A cheque with **no** linked Payment Entry now posts nothing when it clears.
  That is deliberate, and it is announced with a visible message and a timeline
  entry rather than passing silently. See `DECISIONS.md` D2.
- `Cheque Tracker Settings.gl_posting_model` is a read-only placeholder for a
  future Notes Receivable model.

### Other

- `drawee_bank` is required from submit onward rather than on every save — a
  Payment Entry records the cheque number but has nowhere to say which bank
  issued it, so the old rule made auto-creation impossible. Unchanged for any
  submitted cheque.
- Settings gained `reminder_days` (default 3) and `notify_emails`;
  `default_cash_account` now actually renders (it was missing from `field_order`).
- Client: a generic wrapper persists the fields a transition depends on *before*
  applying it — `apply_workflow` reloads from the database and discards unsaved
  edits — used by the new Endorse, Bounce and Cash Clear actions.

## v1.1.6 — Dashboard fixture repair, workflow event logging, custody & counters (2026-08-10)

Bug-fix release. No schema-breaking changes; one new optional field and one
data-repair patch. Independently deployable ahead of v1.2.

### Fixtures (`fixtures/number_card.json`, `fixtures/dashboard_chart.json`)
- **Outgoing cards now count `Handed Over`.** `Active Outgoing`, `Pending Payable`,
  `Due This Week Outgoing` and `Overdue Outgoing` filtered on
  `["Received", "In Safe", "Deposited"]` — but `Deposited` is incoming-only and the
  workflow routes every outgoing cheque through `Handed Over`, so outgoing cheques
  disappeared from all four cards the moment they were handed over.
- **Overdue cards actually work.** Both carried `["Cheque","due_date","<","Today"]`.
  `"Today"` is not a Frappe keyword; it reached MariaDB as the literal string
  (`coalesce(due_date,'0001-01-01') < 'Today'`), raised *Truncated incorrect datetime
  value*, and matched zero rows — the cards always read 0. The clause moved to
  `dynamic_filters_json` as `frappe.datetime.get_today()`, which is evaluated
  client-side.
- **Bounced cards no longer count drafts.** `Bounced Incoming` / `Bounced Outgoing`
  were missing the `docstatus = 1` clause that the other eight cards carry, so they
  included draft and cancelled cheques.
- **Cheque Status Distribution** switched from `Count` to `Sum` of `amount`
  (`group_by_type`, `aggregate_function_based_on`) — a treasury donut should show
  money at risk, not document count.
- **Cheques Over Time** switched to `Sum` of `amount` plotted on `due_date`.
  `issue_date` is optional and frequently blank, so cheques silently dropped out of
  the series; `due_date` is mandatory.

### Patch — `v3_2.repair_dashboard_fixtures`
- Repairs already-deployed sites, whose records were hot-patched by hand and would
  otherwise keep the broken values. Reads the canonical values from the shipped
  fixture files so patch and fixtures cannot drift.
- `Cheques Over Time` is deleted and recreated under the same name:
  `Dashboard Chart.chart_type` is `set_only_once`, so `Count → Sum` cannot be an
  in-place update. The name is preserved because the Workspace `charts` child row
  references it.
- Idempotent; a second run is a no-op.

### Server
- **Workflow transitions log a Cheque Event.** `apply_workflow` sets the state in
  memory and calls `doc.save()`, which for a submitted doc dispatches only
  `on_update_after_submit` — it never reached `log_status_change`, so every
  workflow-driven transition left no timeline row at all (production CHQ-2026-00001
  logged nothing for its Hand Over). `on_update_after_submit` now detects the status
  delta and appends the mapped event. The two paths stay disjoint — the UI endpoint
  writes with `frappe.db.set_value`, which never enters the ORM — so exactly one row
  is written per real transition.
- **Outgoing submit no longer logs a second `Created`.** `on_submit` appended
  `Created` for Outgoing cheques, duplicating the row `after_insert` had already
  written and leaving the real Draft → Received transition unrecorded. It now logs
  the transition for both directions.
- **`current_holder` is cleared when custody leaves the company.** The field is a
  Link to User and cannot name an external payee; leaving the last employee there
  read as if they still held the cheque. Both transition paths clear it on
  `Handed Over` and log a Note naming the released holder.
- **`current_holder` is no longer defaulted for Incoming cheques.** Whoever keys in
  an incoming cheque may never have touched it. Outgoing keeps the default — we draw
  those, so the creator does hold the leaf.
- **`submit()` followed by `cancel()` no longer raises `TimestampMismatchError`.**
  `_flush_events` saved the persisted document, bumping `modified`, without
  resyncing the in-memory copy that `check_if_latest` compares against. It also made
  the method idempotent: the freshly written child rows are adopted, so a second
  flush cannot write them twice.
- **Cheque Book counters track every leaf change.** `unused/issued/voided/cancelled`
  were recomputed only when the Cheque Book form was opened, so the list view showed
  values that were stale the moment a leaf moved. Reserve, issue, void, cancel and
  manual desk edits now all refresh them through a shared
  `refresh_book_counters()`; bulk generation still refreshes once at the end.

### Doctype
- New optional `external_holder` (Data, `allow_on_submit`) on Cheque, shown in the
  Custody section — the counterpart to clearing `current_holder`.

### Tests
- `bench run-tests --app cheque_tracker` went from **34 tests / 16 errors / 12
  skipped** to **63 tests / 0 failures / 0 skipped**.
- The suite resolved its fixtures with `frappe.get_all("Company", limit=1)` (ordered
  by `modified desc`), so it ran against a different company on every run and every
  test carried a `skipTest` guard for the case where the lookup found nothing usable.
  A pinned environment (`cheque_tracker/tests/utils.py`, wired via the new
  `before_tests` hook) replaces it and the guards are gone.
- New: `cheque_tracker/tests/seed_local.py` — idempotent local seed mirroring the
  EEI production environment, including both Appendix A.4 cheques.
- New: `cheque_tracker/tests/verify_fixtures.py` — asserts the live records *and*
  the shipped JSON against §3.1; also runnable as
  `bench execute cheque_tracker.tests.verify_fixtures.run` between two migrations.
- The concurrency test never exercised anything: its threads shared the parent's
  connection, `frappe.local` is thread-local, and every worker died with "object is
  not bound". Each thread now opens its own connection, which is the only way
  `SELECT … FOR UPDATE` means anything.

## v1.1.5 — Replace workflow with bidirectional audit chain (2026-05-11)

### Workflow
- New **Replace** flow on `Bounced` cheques links the bounced cheque to its replacement (or creates a new draft pre-filled from the bounced one), then applies the workflow transition to `Replaced`.
- Two new Link fields on Cheque: `replacement_cheque` (set on the bounced cheque) and `original_cheque` (set on the replacement). Both read-only — populated only via the Replace action handler.

### Server (`cheque.py`, `cheque_financial.py`)
- New whitelisted methods on the Cheque controller: `link_replacement(replacement_name)` and `create_replacement(cheque_no, issue_date, due_date, …)`. Both apply the Workflow `Replace` action atomically with the linking.
- New `validate_replacement_candidate(original, replacement)` helper enforces hard constraints (same `cheque_type`, same party, same `reference_doctype` + `reference_name`, replacement must be Draft, replacement not already linked) and emits a soft warning on amount mismatch.
- `on_cancel` now sets `flags.ignore_links = True` and proactively clears the partner cheque's pointer (Draft partner: normal save; submitted/cancelled partner: `db_set`), so cancelling either side of a Replace chain no longer trips Frappe's `check_no_back_links_exist`.

### Client (`cheque.js`)
- Wraps the workflow's **Replace** button with a dialog offering two paths: *Create New* (pre-fills party / amount / reference / currency / drawee_bank from the bounced cheque; requires Cheque Book + Leaf for Outgoing) or *Link Existing Draft* (filtered by same type / party / reference).
- New quick-view buttons: **View Replacement** and **View Original (Replaced)** when the corresponding Link is populated.
- Pre-Clear dialog wrapper now also fires for Outgoing cheques in the `Handed Over` state (was Incoming-only).

### Notes
- No GL effect from Replace — the bounced cheque never posted under the clearance-only model, and the replacement does its own GL when it clears. Replace exists purely for audit-chain bookkeeping.

## v1.1.4 — Filters, UX & Cash Clearance (2025-03-02)

### Client-side filters & UX improvements (`cheque.js`)
- **Bank Account** filter now shows only company bank accounts (`is_company_account = 1`)
- **Cheque Book & Cheque Leaf** fields are hidden when cheque type is Incoming
- **Reference DocType** filtered by party type (Customer → Sales Invoice/SO/DN; Supplier → Purchase Invoice/PO/PR; Employee → Expense Claim; all → PE/JE)
- **Reference Name** filtered by party, reference doctype, company, and docstatus
- **Cheque Book** filtered by company, bank account, and active status
- **Cheque Leaf** filtered by cheque book and available status
- **Cost Center & PDC Account** filtered by company
- Cascading field clears on parent field changes (company → bank_account → cheque_book → cheque_leaf; party_type → party → reference_name)
- Auto-populate company default currency and drawer name from party

### Cash clearance flow (teller cashing)
- New **`clearance_type`** field (Select: Deposit / Cash) on Cheque doctype
- New **`cash_account`** field (Link → Account, filtered to Cash type) visible only when clearance_type = Cash
- New **`default_cash_account`** field in Cheque Tracker Settings (global fallback)
- Cash flow skips Deposited/Presented statuses — goes directly from Received/In Safe → Cleared
- Clearance JE debits Cash account instead of Bank account when clearance_type = Cash
- Separate "Create Cash Clearance Entry" button label and confirmation message
- Status transition validation blocks Deposited/Presented for Cash clearance type

### Validation & audit
- **Drawee Bank** now mandatory for Incoming cheques
- Audit logging extended to track `cash_account` and `clearance_type` changes on submitted cheques
- JE hook event notes now reflect "Cash" vs "Bank" target in clearance events

### Files changed
- `cheque.json` — 2 new fields (clearance_type, cash_account), drawee_bank mandatory for incoming
- `cheque.js` — full rewrite of client-side controller with filters and clearance_type logic
- `cheque.py` — drawee_bank validation, cash flow transition rules, extended audit logging
- `cheque_financial.py` — new `_get_cash_gl_account` / `_get_debit_account_for_clearance` helpers
- `journal_entry_hooks.py` — clearance event notes reflect cash vs bank
- `cheque_tracker_settings.json` — new `default_cash_account` field

---

## v1.1.0 — Financial Posting Logic (2024)

### Summary
Implements correct financial posting for Incoming (customer) cheques using
ERPNext Payment Entries and Journal Entries, with safe bidirectional sync
back to Cheque status and Cheque Event audit log.

---

### New DocType: `Cheque Tracker Settings` (singleton)
**File:** `cheque_tracker/cheque_tracker/doctype/cheque_tracker_settings/`

Company-level configuration with two fields:
- `pdc_receivable_account` — Link to Account (Asset; "PDC Receivable / Cheques Under Collection")
- `default_bank_account` — Link to Bank Account (optional default for clearance)
- `default_bank_gl_account` — Link to Account (GL account for clearance JE debit)

---

### Modified DocType: `Cheque`
**File:** `cheque_tracker/cheque_tracker/doctype/cheque/cheque.json`

New fields added under **"Financial Posting"** section:

| Field | Type | Purpose |
|---|---|---|
| `pdc_account` | Link → Account | Per-cheque override for PDC Receivable account |
| `recording_payment_entry` | Link → Payment Entry | The PE that records receipt (Dr PDC, Cr AR) |
| `clearance_journal_entry` | Link → Journal Entry | The JE that clears the cheque (Dr Bank, Cr PDC) |
| `reversal_journal_entry` | Link → Journal Entry | The JE that reverses on bounce (Dr AR, Cr PDC) |
| `pre_bounce_status` | Data (hidden) | Stores pre-bounce status for rollback on JE cancel |

---

### New Module: `cheque_financial.py`
**File:** `cheque_tracker/cheque_tracker/doctype/cheque/cheque_financial.py`

Whitelisted API methods:

#### `make_recording_payment_entry(cheque_name)`
- Validates: Incoming cheque, Customer party, amount > 0, company set
- Resolves PDC account from cheque field → Cheque Tracker Settings
- Creates a **Payment Entry (Receive)**:
  - `paid_from` = Company default AR account
  - `paid_to` = PDC Receivable account
  - Allocates to Sales Invoice if `reference_doctype = "Sales Invoice"`
- **Idempotent**: returns existing Draft PE (updating it) or existing Submitted PE without re-creating
- Does NOT change cheque status — status changes only on PE submit

#### `make_clearance_journal_entry(cheque_name)`
- Creates a **Journal Entry**:
  - Dr Bank GL Account (resolved from bank_account field → Bank Account → account, or settings)
  - Cr PDC Receivable Account
- **Idempotent**: same as above
- Does NOT change cheque status — status changes only on JE submit

#### `process_bounce(cheque_name, notes="")`
- Strategy 1 (recording PE is Draft): Cancel the Draft PE → cheque immediately set Bounced
- Strategy 2 (recording PE is Submitted): Create reversing JE (Dr AR, Cr PDC) in Draft; submit JE to finalise Bounced status
- Strategy 3 (no recording PE): Directly mark Bounced, no GL impact
- **Idempotent**: returns existing reversal JE if already created

---

### New Module: `payment_entry_hooks.py`
**File:** `cheque_tracker/cheque_tracker/hooks/payment_entry_hooks.py`

- `payment_entry_on_submit`: Finds cheque linked via `recording_payment_entry` → sets status `Received`, appends Cheque Event with PE reference
- `payment_entry_on_cancel`: Finds linked cheque → rolls back status to `Draft`, clears `recording_payment_entry`, logs Note event

---

### New Module: `journal_entry_hooks.py`
**File:** `cheque_tracker/cheque_tracker/hooks/journal_entry_hooks.py`

- `journal_entry_on_submit`:
  - `clearance_journal_entry` submitted → cheque `Cleared` + `cleared_date = today()`
  - `reversal_journal_entry` submitted → cheque `Bounced`
- `journal_entry_on_cancel`:
  - `clearance_journal_entry` cancelled → cheque rolled back to `Received`, `cleared_date` cleared
  - `reversal_journal_entry` cancelled → cheque rolled back to `pre_bounce_status`

---

### Modified: `cheque.py`
**File:** `cheque_tracker/cheque_tracker/doctype/cheque/cheque.py`

- `before_save` now calls `_protect_fields_if_submitted_accounting_docs()`:
  - Prevents modifying `amount`, `party`, `party_type`, `company`, `cheque_no`, `bank_account` when any linked accounting doc is Submitted
- `on_cancel` now blocks cancellation when submitted accounting docs exist
- `on_submit` (Incoming): sets status `Received` immediately (physical receipt confirmed)
- `_validate_transition`: blocks manual `Cleared` status (must go through JE hook); validates required fields for `In Safe/Deposited/Presented`; checks for submitted accounting docs before `Cancelled`

---

### Modified: `hooks.py`
**File:** `cheque_tracker/hooks.py`

Added `doc_events` for:
```python
"Payment Entry": {
    "on_submit": "...payment_entry_hooks.payment_entry_on_submit",
    "on_cancel": "...payment_entry_hooks.payment_entry_on_cancel",
},
"Journal Entry": {
    "on_submit": "...journal_entry_hooks.journal_entry_on_submit",
    "on_cancel": "...journal_entry_hooks.journal_entry_on_cancel",
},
```

---

### New: `cheque.js`
**File:** `cheque_tracker/cheque_tracker/doctype/cheque/cheque.js`

Adds custom action buttons on the Cheque form (visible for submitted Incoming cheques):

**Accounting group:**
- "Create Recording Payment Entry" → calls `make_recording_payment_entry`
- "View Recording Payment Entry" → opens existing PE
- "Create Clearance Entry" → calls `make_clearance_journal_entry`
- "View Clearance Entry" → opens existing JE
- "Process Bounce" → prompts for reason, calls `process_bounce`
- "View Reversal Entry" → opens reversal JE

**Manage group:**
- "Mark In Safe" (from Received)
- "Mark Deposited" (from In Safe)
- "Mark Presented" (from Deposited)

---

### New: `test_cheque_financial.py`
**File:** `cheque_tracker/cheque_tracker/doctype/cheque/test_cheque_financial.py`

8 automated tests covering:
1. Recording PE created as Draft with correct fields
2. Recording PE submit → cheque status = Received, event logged with PE reference
3. Clearance JE submit → cheque status = Cleared, cleared_date set, event logged
4. Bounce after submitted PE → reversal JE created → submit → Bounced
5. Bounce with Draft PE → PE cancelled → Bounced immediately
6. Idempotency: calling make_recording_pe twice returns same PE
7. No new PE created when existing PE is Submitted
8. Clearance JE cancel rolls back cheque status to Received
9. Protected fields (amount, party, etc.) cannot be edited after PE submit

---

### New: `patches/v1_1/add_financial_posting_fields.py`
**File:** `cheque_tracker/cheque_tracker/patches/v1_1/add_financial_posting_fields.py`

Database migration patch that adds the five new columns to `tabCheque` for existing installations.

---

## Accounting Invariants Maintained

| Invariant | How Enforced |
|---|---|
| Only PE/JE posts to GL | Cheque/ChequeEvent have no GL calls |
| Status only changes on accounting doc submit | All `db.set_value` calls in hook handlers |
| No double-posting | Idempotency checks on `recording_payment_entry` / `clearance_journal_entry` fields |
| Audit trail | Every status change adds a Cheque Event row with reference_doctype/name |
| Core fields protected | `_protect_fields_if_submitted_accounting_docs()` in `before_save` |
| Clearance requires bank account | Validated in `_get_bank_gl_account()` and in JS button guard |
