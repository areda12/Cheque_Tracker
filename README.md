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
