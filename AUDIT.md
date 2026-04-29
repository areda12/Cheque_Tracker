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

## 2. `hooks.py` review

File: `cheque_tracker/hooks.py` (120 lines).

### App metadata
| Key                | Value                              |
|--------------------|------------------------------------|
| `app_name`         | `cheque_tracker`                   |
| `app_title`        | `Cheque Tracker`                   |
| `app_publisher`    | `Ahmed Abbas`                      |
| `app_description`  | `Cheque Tracking System for ERPNext` |
| `app_email`        | `ahmed@example.com` ⚠️             |
| `app_license`      | `MIT`                              |

**Concern:** `app_email = "ahmed@example.com"` is a placeholder address, not a real contact. `pyproject.toml` has the same placeholder. Cosmetic, but sloppy for a production app.

### `app_include_css` / `app_include_js`
| Key                | Value                                              |
|--------------------|----------------------------------------------------|
| `app_include_css`  | `/assets/cheque_tracker/css/cheque_tracker.css`    |
| `app_include_js`   | `/assets/cheque_tracker/js/cheque_tracker.js`      |

**What it does:** Bundles `public/css/cheque_tracker.css` and `public/js/cheque_tracker.js` into every desk page load (not just Cheque pages).

**Concerns (P1):**
- `public/js/cheque_tracker.js` registers `frappe.ui.form.on("Cheque", { refresh, cheque_type })` AND `frappe.ui.form.on("Cheque Book", { onload, ... })` at lines 48 and 181. These are **doctype-specific** handlers being loaded globally, which:
  1. Wastes bandwidth on every desk page request.
  2. Conflicts with the doctype-local `cheque/cheque.js` (also a `frappe.ui.form.on("Cheque", ...)` handler at line 34). Both handlers fire on every Cheque form refresh. The local controller calls `frm.clear_custom_buttons()` at the start of `_setup_buttons()` (line 334), which **wipes the buttons added by the global controller** (Hand Over, Move to Safe, Deposit, Return, Bounce, Present, Clear, Replace). The order of registration is non-deterministic; in practice the doctype-local file runs after `app_include_js`, so the global buttons are usually wiped after being added.
- The CSS is small enough that loading it globally is harmless; the JS should be moved into `cheque/cheque.js` (or split between `cheque/cheque.js` and `cheque_book/cheque_book.js`).

### `fixtures`
Single list in `hooks.py` lines 17-73:

| `dt` filter                    | Filter expression                                  | Purpose                                              |
|--------------------------------|----------------------------------------------------|------------------------------------------------------|
| `Role`                         | `name in ["Treasury User", "Cheque Auditor"]`      | Export the two custom roles.                         |
| `Workflow`                     | `document_type = "Cheque"`                         | Export the Cheque Workflow.                          |
| `Workflow State`               | `workflow_name = "Cheque Workflow"`                | ⚠️ Bug — see below.                                  |
| `Workflow Action Master`       | `name in [Receive, Move to Safe, ..., Cancel Cheque]` | Export 9 workflow action masters.                 |
| `Workspace`                    | `name = "Treasury Workbench"`                      | Export the Treasury Workbench workspace.             |
| `Report`                       | `name in [4 reports]`                              | Export the 4 Script Reports.                         |
| `Property Setter`              | `doc_type = "Cheque"`                              | Export property setters scoped to Cheque.            |

**Concerns:**
- **Workflow State filter is wrong (P1):** `Workflow State` doctype has NO `workflow_name` field — workflow states are global. The filter `[["workflow_name", "=", "Cheque Workflow"]]` will match zero rows on export. The states in `fixtures/workflow_state.json` are committed to the repo, so they install correctly on first install via the `make_fixtures` mechanism, but a `bench export-fixtures` run will silently produce an empty list and overwrite the committed file. Recommend changing the filter to `[["name", "in", [<list of state names>]]]` or removing the entry and managing state JSON manually.
- **`fixtures/custom_roles.json` is committed but NOT registered** — the file in `cheque_tracker/fixtures/` defines two Role records with the same names as in the `Role` fixtures filter. On export, Frappe writes `custom_roles.json` is overwritten by `role.json`, so the custom_roles.json file is dead/ignored.
- All fixture filters look read-only; no install-time side effects.

### `scheduler_events`
| Frequency | Method                                                                  |
|-----------|-------------------------------------------------------------------------|
| `daily`   | `cheque_tracker.cheque_tracker.tasks.auto_update_cheque_statuses`       |

**What the handler does:** (`cheque_tracker/cheque_tracker/tasks.py:13-53`)
1. Fetches all submitted Cheques in status `Deposited`/`Presented` whose `due_date < today` and writes a warning line to the `cheque_tracker` logger for each.
2. Iterates every active Cheque Book and calls `book._refresh_counters()` to recompute leaf counters.
3. Wraps each book refresh in `try/except Exception:` → `frappe.log_error(...)` (logs but swallows).

**Concerns:**
- **Duplicate file:** `cheque_tracker/tasks.py` (top-level) is a byte-identical copy of `cheque_tracker/cheque_tracker/tasks.py`. The hook references the deeper one. The shallower file is dead code — risk of someone editing the wrong one.
- **No batching / chunking:** for a site with thousands of active books, this iterates them all in one transaction. Low risk on Aldar's scale but worth noting.
- **`frappe.log_error` with `frappe.get_traceback()`** is correct usage and won't mask errors — good.
- The "OVERDUE" warnings are logged via `frappe.logger("cheque_tracker", allow_site=True)` but no one consumes that logger; the warnings are effectively write-only telemetry. Not a bug, just observation.

### `doc_events`
Three doctypes wired (`hooks.py:92-111`):

| DocType         | Event       | Handler                                                                          | What it does                                                                                                                                                                                                                                                                                                                                                  |
|-----------------|-------------|----------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `Cheque Book`   | `on_submit` | `cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book.on_submit`         | **No-op shim** (`pass`). Real logic lives in the `ChequeBook.on_submit()` method on the Document subclass.                                                                                                                                                                                                                                                    |
| `Cheque Book`   | `on_cancel` | `cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book.on_cancel`         | **No-op shim** (`pass`). Same as above.                                                                                                                                                                                                                                                                                                                       |
| `Payment Entry` | `on_submit` | `cheque_tracker.cheque_tracker.hooks.payment_entry_hooks.payment_entry_on_submit` | Looks up a Cheque whose `recording_payment_entry == pe.name`. If found, transitions cheque status to `Received` (only when current status is `Draft` or `Received`) via `frappe.db.set_value`, then appends a `Received` Cheque Event with the PE reference. Wraps event append in `ignore_permissions` + `ignore_validate_update_after_submit`.              |
| `Payment Entry` | `on_cancel` | `cheque_tracker.cheque_tracker.hooks.payment_entry_hooks.payment_entry_on_cancel` | Looks up the same back-link. Sets cheque status to `Draft`, clears `recording_payment_entry`, and appends a `Note` event. Hard-coded rollback to `Draft` regardless of the current status — see Concerns.                                                                                                                                                     |
| `Journal Entry` | `on_submit` | `cheque_tracker.cheque_tracker.hooks.journal_entry_hooks.journal_entry_on_submit` | Calls TWO handlers: (1) `_handle_clearance_je_submit` looks up a cheque whose `clearance_journal_entry == je.name`, sets status `Cleared` + `cleared_date = today()`, appends `Cleared` event; (2) `_handle_reversal_je_submit` looks up `reversal_journal_entry == je.name`, sets status `Bounced`, appends `Bounced` event.                                  |
| `Journal Entry` | `on_cancel` | `cheque_tracker.cheque_tracker.hooks.journal_entry_hooks.journal_entry_on_cancel` | Mirrors above: clearance JE cancel → status rolls back to `Received` (hard-coded fallback), `cleared_date` cleared, `clearance_journal_entry` cleared. Reversal JE cancel → status restored from `pre_bounce_status`, both link fields cleared.                                                                                                               |

**Concerns:**

1. **Cheque Book hooks are no-ops (P3, cosmetic):** `cheque_tracker/doctype/cheque_book/cheque_book.py:157-162` defines `def on_submit(doc, method=None): pass` and `def on_cancel(doc, method=None): pass`. These hooks are wired in `hooks.py` but do nothing — the Document subclass `on_submit`/`on_cancel` methods are called automatically by Frappe. The hooks file comment (lines 87-91 of `hooks.py`) explicitly explains that Cheque lifecycle hooks are NOT registered to avoid double-firing — but Cheque Book ones are wired anyway. Recommend either removing the registration entirely or removing the shim functions; the current state is confusing.

2. **PE submit handler runs on EVERY Payment Entry (P2):** Both `payment_entry_on_submit` and `payment_entry_on_cancel` execute a `frappe.get_all("Cheque", filters={"recording_payment_entry": pe.name})` query on every PE submit/cancel **across the entire site**, including PEs that have nothing to do with cheques. On Aldar, that's hundreds of PEs/day. The query is indexed (a Link field auto-creates an index) so the cost is small, but it does fire `_append_event_and_save` (which loads + saves a Cheque doc) for every match. No early-return guard. Acceptable but worth noting.
   - Same applies to `journal_entry_on_submit`/`on_cancel`. Each JE submit runs TWO `frappe.get_all` queries (clearance + reversal lookups). Worth measuring after deployment.

3. **Hard-coded rollback statuses (P2 correctness):**
   - `payment_entry_hooks.py:100`: PE cancel always rolls cheque to `"Draft"`. If the cheque had progressed to `In Safe`/`Deposited`/`Presented` after the PE was submitted, this rolls back too aggressively and may break the workflow state machine.
   - `journal_entry_hooks.py:116`: clearance JE cancel always rolls cheque to `"Received"`. If the cheque was actually `Presented` before clearance, the rollback is wrong.
   - Reversal JE cancel uses `pre_bounce_status` correctly — that pattern should be applied to the other two.

4. **`ignore_validate_update_after_submit = True` in event-append helpers (P2):** Both PE and JE hooks use this flag in `_append_event_and_save` to bypass the post-submit validation lock on Cheque. This is necessary because the `events` child table is `allow_on_submit: 1` and we need to write to it. But the flag bypasses **all** post-submit validation, including the `_protect_fields_if_submitted_accounting_docs` guard. In the current code path the only field being mutated is the events child table, so it's safe — but a future change to `_handle_*_submit` that also touches a parent field on `cheque` would silently bypass protection.

5. **No idempotency guard on event-append (P3):** If a PE/JE submit hook is replayed (e.g., rerun via `bench execute`), a second `Received`/`Cleared`/`Bounced` event will be appended. Cheque events are append-only by design, so duplicates are visible noise but not corrupting.

6. **No permission check on hook handlers (acceptable):** The hooks run in the context of whoever submitted the PE/JE; the permission check on the originating doc has already passed. Hook code uses `ignore_permissions=True` to mutate the linked Cheque, which is the standard pattern. Not a concern.

### `jinja`
| Key       | Value                |
|-----------|----------------------|
| `methods` | `[]` (empty)         |
| `filters` | `[]` (empty)         |

No Jinja extensions. Fine.

### Hooks NOT present (gaps)
The following hook keys are **not** defined in `hooks.py` and may need to be added in Phase 2:

| Hook                       | Why it might be needed                                                                               |
|----------------------------|------------------------------------------------------------------------------------------------------|
| `before_install`           | Pre-install validation (e.g., ensure ERPNext is installed first).                                    |
| `after_install`            | **Important** — should create the `Cheque Tracker Settings` singleton record so first reads don't go through Frappe's lazy-creation path. See §4. |
| `before_uninstall`         | Currently nothing prevents uninstall while submitted Cheques exist; an uninstall would orphan PE/JE links. |
| `boot_session`             | Could push status colors / role flags to the client session for the JS controller, removing the hardcoded `STATUS_COLORS` dict. Not blocking. |
| `permission_query_conditions` | Not needed — DocType-level perms cover the access model. |
| `has_permission`           | Could enforce row-level rules (e.g., Treasury User can only see cheques for companies they have access to). Currently not enforced. |
| `override_doctype_class`   | Not needed.                                                                                          |
| `override_whitelisted_methods` | Not needed.                                                                                      |
| `doctype_js`               | Not used; doctype-local JS is auto-loaded by Frappe. The conflict with `app_include_js` (see above) would be cleaner if the global JS were registered as `doctype_js: {"Cheque": "...", "Cheque Book": "..."}` instead — or, better, merged into the doctype-local controllers. |

---

