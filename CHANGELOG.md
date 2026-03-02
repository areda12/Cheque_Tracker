# CHANGELOG

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
