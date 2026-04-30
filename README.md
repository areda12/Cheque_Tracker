# Cheque Tracker

A production-grade **Frappe / ERPNext v16** application for complete cheque lifecycle management.

## Features

- **Cheque Book** — auto-generates numbered Cheque Leaf records on submit (numeric & alphanumeric patterns)
- **Concurrency-safe leaf allocation** — `SELECT … FOR UPDATE` row-locking prevents double reservation
- **Full auditability** — every status change appends a timestamped Cheque Event row
- **Role-based workflow** — 10 states, 15 transitions; Treasury User / Accounts User / Cheque Auditor
- **4 Script Reports** — Due This Week, Deposited-Not-Cleared aging, Bounced Register, Book Utilization
- **Treasury Workbench** — dedicated workspace with shortcuts

## Installation

```bash
# From inside your bench directory
bench get-app cheque_tracker https://github.com/<your-org>/cheque_tracker
bench --site <site> install-app cheque_tracker
bench --site <site> migrate
bench build --app cheque_tracker
```

## Post-Install Configuration

After installing the app, configure the following accounts in
**Cheque Tracker Settings** (Desk → Cheque Tracker Settings) before
using the app:

- **PDC Receivable Account** (`pdc_receivable_account`) — the
  post-dated cheques receivable account. Used as `paid_to` on the
  Recording Payment Entry and as the credit side of the Clearance
  Journal Entry. See `account_type` note below.
- **Default Bank GL Account** (`default_bank_gl_account`) — bank GL
  account used as the debit side of the Clearance Journal Entry for
  the deposit flow. Can be overridden per cheque via the cheque's
  Bank Account field.
- **Default Cash Account** (`default_cash_account`) — cash GL
  account used as the debit side of the Clearance Journal Entry for
  the cash flow (teller cashing). Required only if you use the cash
  clearance flow; can be overridden per cheque via the cheque's
  Cash Account field.

The install hook creates the Settings record but leaves these
fields blank deliberately. The app will raise clear errors on
Payment Entry / Journal Entry submission until they are set, and
the install output will include a `WARNING` listing the
unconfigured fields.

> AR posting uses `Company.default_receivable_account` (set on the
> Company record), not a Settings field.

### PDC Account `account_type` note (P0 install-time concern)

If your PDC account is configured with `account_type = "Receivable"`
(or `"Payable"`), ERPNext requires `party` on every GL Entry
hitting it. The current `make_recording_payment_entry` does not
populate party on the PDC-side GL row, so submissions will fail
with `ValidationError: Customer is required against Receivable
account`.

Two paths to resolve:

1. **Set the production PDC account's `account_type` to `""` or an
   asset type that does not require party** — simpler; loses some
   ERPNext receivable-aging integration.
2. **Patch `make_recording_payment_entry`** to populate
   `party_type` / `party` on the PDC-side GL row at submission
   time — production-grade; allows either `account_type` to work.
   Tracked as a Phase 2 follow-up.

Verify the production setting before first cheque submission.

## Fresh scaffold (bench new-app flow)

```bash
cd /home/frappe/frappe-bench
bench find .
bench new-app cheque_tracker
# App Title        : Cheque Tracker
# App Description  : Cheque Tracking System for ERPNext
# App Publisher    : Ahmed Abbas
# App Email        : ahmed@example.com
# App Icon         : octicon octicon-credit-card
# App Color        : grey
# App License      : MIT

bench --site <site> install-app cheque_tracker
bench --site <site> migrate
```

## Running tests

```bash
bench --site <site> run-tests --app cheque_tracker
```

## Data model

| DocType | Type | Purpose |
|---|---|---|
| Cheque Book | Submittable | Defines a numbered cheque book; generates leaves on submit |
| Cheque Leaf | Full DocType | One row per cheque leaf; atomically reserved then issued |
| Cheque | Submittable | Full cheque lifecycle with workflow |
| Cheque Event | Child table | Audit timeline under Cheque |
| Cheque Batch | Submittable | Group cheques for deposit |
| Cheque Batch Item | Child table | Line items in a Cheque Batch |

## License

MIT — © 2024 Ahmed Abbas
