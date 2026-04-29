# Cheque Tracker — Production Readiness Audit

**Branch:** `fix/production-readiness-audit`
**Base:** `main` @ `b431825` (v1.1.4)
**Audited:** 2026-04-29
**Scope:** Phase 1 — read-only audit. No code changes in this commit.

---

## 1. Repo structure overview

### Top level
```
.
├── README.md            (62 lines)  — install / data-model overview
├── CHANGELOG.md         (205 lines) — v1.1.0 + v1.1.4 release notes
├── MANIFEST.in          (2 lines)
├── pyproject.toml       (20 lines, flit_core build, dynamic version)
└── cheque_tracker/      (the actual app package)
```

### App package (`cheque_tracker/`)
```
cheque_tracker/
├── __init__.py                       (__version__ = "1.1.4")
├── modules.txt                       ("Cheque Tracker")
├── patches.txt                       (2 patches registered)
├── hooks.py                          (120 lines — fixtures / scheduler / doc_events)
├── tasks.py                          (54 lines — DUPLICATE of cheque_tracker/tasks.py)
├── templates/                        (empty stub)
├── fixtures/                         (6 JSON files, 106 lines total)
│   ├── custom_roles.json             — Treasury User, Cheque Auditor
│   ├── workflow.json                 — Cheque Workflow: 10 states, 15 transitions
│   ├── workflow_state.json           — 10 state styles
│   ├── workflow_action_master.json   — 9 action masters
│   ├── workspace.json                — Treasury Workbench
│   └── property_setter.json          — cheque_no read-only depends on cheque_type
├── public/
│   ├── css/cheque_tracker.css        (24 lines — status pill colors)
│   └── js/cheque_tracker.js          (211 lines — GLOBAL form handler, see §2 / §6)
├── patches/
│   ├── v1_0/add_unique_constraint_cheque_leaf.py
│   └── v1_1/add_financial_posting_fields.py
└── cheque_tracker/                   (nested module dir — Frappe convention)
    ├── tasks.py                      (54 lines — auto_update_cheque_statuses)
    ├── hooks/                        (PE/JE doc_events handlers, NOT to be confused with /hooks.py)
    │   ├── payment_entry_hooks.py    (116 lines)
    │   └── journal_entry_hooks.py    (156 lines)
    ├── doctype/
    │   ├── cheque/                   — main doctype, has 3 .py + 2 test files + .js + .json
    │   │   ├── cheque.py             (407 lines)
    │   │   ├── cheque_financial.py   (486 lines — PE/JE creation logic)
    │   │   ├── cheque.js             (546 lines — controller, action buttons)
    │   │   ├── cheque.json           (433 lines)
    │   │   ├── test_cheque.py        (146 lines, 9 tests)
    │   │   └── test_cheque_financial.py (418 lines, 8 tests)
    │   ├── cheque_book/              (180 lines + 9 tests)
    │   ├── cheque_leaf/              (125 lines + 7 tests)
    │   ├── cheque_batch/             (48 lines, NO tests)
    │   ├── cheque_batch_item/        (8 lines, child)
    │   ├── cheque_event/             (8 lines, child)
    │   └── cheque_tracker_settings/  (14 lines, single)
    └── report/                        (4 Script Reports, all .py + .js + .json)
        ├── cheque_book_utilization/   (58 lines)
        ├── deposited_not_cleared/     (64 lines)
        ├── bounced_cheques_register/  (55 lines)
        └── cheques_due_this_week/     (56 lines)
```

### Module count
- **1 module:** `Cheque Tracker`
- **7 DocTypes:** Cheque (submittable), Cheque Batch (submittable), Cheque Book (submittable), Cheque Leaf, Cheque Tracker Settings (single), Cheque Event (child), Cheque Batch Item (child)
- **4 Script Reports**
- **1 Workspace:** Treasury Workbench
- **2 custom roles:** Treasury User, Cheque Auditor (System Manager + Accounts User reused from core)

### Lines of code (Python only)
| Area                    | LOC   |
|-------------------------|-------|
| Doctype controllers     | 790   |
| Financial posting logic | 486   |
| PE/JE doc_event hooks   | 272   |
| Reports                 | 233   |
| Tasks (scheduler)       | 54    |
| Patches                 | ~50   |
| Tests                   | 793   |
| **Total Python**        | **~2,867** |

### Notable structural observations (flagged for later sections)
1. **Two `tasks.py` files** — `cheque_tracker/tasks.py` and `cheque_tracker/cheque_tracker/tasks.py` are **byte-identical**. Only the deeper one is referenced by `hooks.py`; the shallower one is dead code.
2. **Two Cheque form controllers** — `public/js/cheque_tracker.js` is loaded globally via `app_include_js` and registers `frappe.ui.form.on("Cheque", ...)`; `cheque/cheque.js` is the per-doctype controller. Both bind the same form events. Detailed in §6.
3. **`Cheque Tracker Settings` is `issingle: 1`** — confirmed in JSON (line 43). Singletons in Frappe v14+ live in `tabSingles`, NOT in their own table. Detailed in §4.
4. **`fixtures/custom_roles.json`** — exports custom Roles via fixtures rather than via `after_install`. Works, but is fragile if the Role names already exist on a target site.
5. **No `before_install` / `after_install` / `before_uninstall` hook** — fresh-install behavior relies entirely on Frappe's automatic doctype JSON loader.

---
