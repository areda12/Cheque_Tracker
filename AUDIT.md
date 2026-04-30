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

## 3. Patches and Settings table investigation

### Part A — `patches.txt` inventory

File: `cheque_tracker/patches.txt` (2 lines).

```
cheque_tracker.patches.v1_0.add_unique_constraint_cheque_leaf
cheque_tracker.patches.v1_1.add_financial_posting_fields
```

#### Patch 1 — `v1_0.add_unique_constraint_cheque_leaf`

| Field            | Value                                                                |
|------------------|----------------------------------------------------------------------|
| File             | `cheque_tracker/patches/v1_0/add_unique_constraint_cheque_leaf.py`   |
| Added in commit  | `7d158f9` ("Add files via upload" — initial v1.0 import)             |
| Lines            | 30                                                                   |

**What it does:**
- Issues `ALTER TABLE \`tabCheque Leaf\` ADD UNIQUE INDEX \`unique_book_cheque_no\` (cheque_book(140), cheque_no(100))`.
- Calls `frappe.db.commit()` after the DDL (DDL implicitly commits in MySQL anyway, so the commit is redundant but harmless).
- Catches the resulting exception. If the message contains "duplicate key name" or "already exists", it swallows it (treating the index as already in place).

**Idempotency:** ✅ **Yes.** The `try/except` on `"duplicate key name" / "already exists"` makes re-runs safe. Re-running on a fresh DB will create the index; re-running on an upgraded DB will swallow the duplicate-name error.

**Concerns:**
- Catches `except Exception as exc` and inspects `str(exc).lower()` for substring matches. Fragile across MariaDB / MySQL / Postgres versions where the wording may differ. Acceptable for now.
- The duplicate-key-name check should ideally be `frappe.db.has_index("tabCheque Leaf", "unique_book_cheque_no")` instead, which is locale-independent.

#### Patch 2 — `v1_1.add_financial_posting_fields`

| Field            | Value                                                                       |
|------------------|-----------------------------------------------------------------------------|
| File             | `cheque_tracker/patches/v1_1/add_financial_posting_fields.py`               |
| Added in commit  | `41761fe` ("Replace repo content with cheque_tracker_updated v1.1.0")        |
| Lines            | 38                                                                          |

**What it does:**
1. Reads `frappe.db.get_table_columns("Cheque")` to get existing columns.
2. For each of `pdc_account`, `recording_payment_entry`, `clearance_journal_entry`, `reversal_journal_entry`, `pre_bounce_status` — if the column is missing, runs `ALTER TABLE \`tabCheque\` ADD COLUMN \`{col_name}\` varchar(140) DEFAULT NULL` followed by `frappe.db.commit()` (inside the loop).
3. Calls `frappe.reload_doc("cheque_tracker", "doctype", "cheque")` to refresh the Cheque DocType meta from JSON.
4. Calls `frappe.reload_doc("cheque_tracker", "doctype", "cheque_tracker_settings")` to refresh the Settings DocType meta from JSON.

**Idempotency:** ✅ **Yes** (column-add is gated on `if col_name not in existing_columns`). Re-running is a no-op on an up-to-date schema.

**Concerns:**
- **`frappe.db.commit()` inside a loop (P3):** five DDL statements, each followed by a commit. DDL auto-commits anyway in MySQL, so the explicit commits are functionally redundant but break the "single transaction per patch" model. Not a correctness bug.
- **F-string SQL with `col_name` and `col_type` (P2 reviewable, not exploitable):** `f"ALTER TABLE \`tabCheque\` ADD COLUMN \`{col_name}\` {col_type} DEFAULT NULL"`. The values come from a hardcoded literal dict, not user input — there is no SQL injection vector. But the pattern is fragile (a future maintainer copying this code might use a user-supplied column name). Recommend switching to `frappe.db.add_column("Cheque", col_name, col_type)` if available, or at minimum quoting/whitelisting.
- **Does NOT create the `Cheque Tracker Settings` Single record.** The patch reloads the doctype meta but never calls `frappe.get_single("Cheque Tracker Settings").save()`. This is the heart of Part B below.
- **No corresponding patch for the v1.1.4 `cash_account` and `default_cash_account` fields.** The fields exist only in the JSON; on a v1.1.0 → v1.1.4 upgrade the column add for `Cheque.cash_account` and the singleton-field add for `Cheque Tracker Settings.default_cash_account` will rely on Frappe's automatic schema sync via `bench migrate`. That sync usually works for new columns on regular doctypes, but it's not deterministic for Singles fields. **Not strictly a blocker for fresh installs**, but worth a defensive patch.

#### What's missing from `patches.txt`

| Missing patch                                  | Why it should exist                                                                                         |
|------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| Initialize the `Cheque Tracker Settings` Single | So freshly-installed sites have a row in `tabSingles` and `_get_pdc_account()` can find configured values rather than throwing. See Part B. |
| Backfill `cash_account` / `default_cash_account` (v1.1.4) | Belt-and-braces if `bench migrate` schema sync misses it on upgraded sites.                       |

---

### Part B — Settings table root cause

#### Confirming the DocType JSON

File: `cheque_tracker/cheque_tracker/doctype/cheque_tracker_settings/cheque_tracker_settings.json`, line 43:

```json
"issingle": 1,
```

Confirmed: the DocType is declared as a Single. ✅

#### Root cause analysis

> **REVISED 2026-04-29 after direct verification on dev.**
> Earlier this section proposed a repair patch for a "missing `tabCheque Tracker Settings` table". That diagnosis was wrong. Both the original symptom and the proposed fix are retracted below; the proposed patch is **dropped** (no Phase 2 work needed for this issue).

**False positive — there is no bug here.**

The reported symptom ("`tabCheque Tracker Settings` MySQL table is missing → `frappe.get_single()` will crash with 1146") came from an external probe that ran a `count(*)` query against `tab<DocType>` without first checking `issingle`. For a Single DocType, that table does not exist by design — the per-doctype table query was the problem, not the DocType state.

A direct `frappe.get_single("Cheque Tracker Settings")` call on dev (`eei-test.f.frappe.cloud`) was performed and confirmed:

- The DocType meta has `issingle = 1`, matching the JSON.
- Singles values are stored in the shared `tabSingles` table (rows of the form `(doctype, field, value)`), not in a per-doctype table.
- `get_single()` returns the singleton doc cleanly with all four declared fields:
  - `pdc_receivable_account`
  - `default_bank_account`
  - `default_bank_gl_account`
  - `default_cash_account`
- All four fields are currently null on dev, but the doc itself is healthy.

The Settings DocType is therefore **not** a blocker. No migration patch is required.

#### Open questions — answered

| # | Question                                                                                                                                                                       | Answer                                                                                                                                                              |
|---|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1 | Was the dev site installed from a particular tagged release, or from a hand-built bench?                                                                                       | Installed from tagged release **v1.1.4** on Frappe Cloud (detached HEAD = tag checkout). Migration completed cleanly; both patches ran successfully; no failures.    |
| 2 | Has anyone touched `Cheque Tracker Settings` directly via the desk UI on dev — e.g., creating a non-singleton DocType with the same name, then deleting it?                    | No. Settings DocType has been a Single since creation (`issingle = 1`). Zero Version log entries. Zero Property Setters. No drift.                                  |

#### Fix dropped

The previously-proposed `cheque_tracker/patches/v1_1/initialize_settings_singleton.py` patch is **withdrawn**. Do not create the file in Phase 2. There is nothing to repair on either dev or production.

#### Follow-up item (decision needed before production install)

The four Settings fields are all null on dev. They are **required** for the financial-posting flow to work end-to-end:

| Field                       | Used by                                                                                       |
|-----------------------------|-----------------------------------------------------------------------------------------------|
| `pdc_receivable_account`    | `_get_pdc_account()` in `cheque_financial.py:40` — fallback when `Cheque.pdc_account` is unset. |
| `default_bank_account`      | Documented as a default for clearance, but not actually read by any code path (orphan field).  |
| `default_bank_gl_account`   | `_get_bank_gl_account()` in `cheque_financial.py:69` — fallback when `Cheque.bank_account` resolves to no GL account. |
| `default_cash_account`      | `_get_cash_gl_account()` in `cheque_financial.py:89` — fallback when `Cheque.cash_account` is unset on cash-clearance flow. |

If these are unset and a user tries to create a Recording PE / Clearance JE without per-cheque overrides, the helpers explicitly `frappe.throw(...)`. That's a clean failure mode (not data corruption), but it means the app is unusable until an admin populates them.

**Decision required:** which of these is the production policy?

- **(a)** Admins set these fields via the desk UI post-install. The app ships with all-null defaults. Document this in `README.md` install steps.
- **(b)** The app ships sensible defaults via a fixture or `after_install` hook, with the understanding that ERPNext-specific defaults (PDC asset account, etc.) cannot be hardcoded and must be derived from the company's Chart of Accounts.

Recommended approach if going with (b): a Phase 2 task that adds an `after_install` hook which logs a clear warning ("Cheque Tracker Settings: please configure pdc_receivable_account etc. before submitting incoming cheques") and creates the Settings doc by `save()`-ing it (so it's pre-pinned in the desk navigation). This is much smaller than the original proposed patch.

Note also: `default_bank_account` (the `Bank Account` link, not the GL account) appears unused by any read path I could find in the audit. Worth confirming whether to remove the field or wire it up — flagging only, not changing.

---

## 4. Python code smells

Each subsection lists every occurrence of the pattern across the app, classifies severity, and notes whether it should be fixed in Phase 2.

Severity legend:
- **P0** — security or correctness bug, fix before production install.
- **P1** — likely-incorrect behavior under realistic conditions, fix soon.
- **P2** — fragile pattern that will bite a future maintainer.
- **P3** — cosmetic / style.

### 4.1 Bare `except:` and `except Exception:` without re-raise

| File:line                                                                                  | Severity | Code & analysis                                                                                                                                                                                                                                                                                          |
|--------------------------------------------------------------------------------------------|----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `cheque_tracker/cheque_tracker/tasks.py:49`                                                | **P2**   | `except Exception:` inside per-book counter-refresh loop, logged via `frappe.log_error` and continues. Acceptable because a single book failing shouldn't kill the daily job for all books. Counter-refresh failures will be silent unless someone reads error log. Worth narrowing to `(OperationalError, ValidationError)` if scope is known. |
| `cheque_tracker/tasks.py:49`                                                               | **P3**   | **Identical duplicate** of the above — this file is dead code (see §1, §2). Remove the file in Phase 2.                                                                                                                                                                                                  |
| `cheque_tracker/patches/v1_0/add_unique_constraint_cheque_leaf.py:25`                      | **P2**   | `except Exception as exc:` with `if "duplicate key name" in str(exc).lower(): pass; else: raise`. Re-raises non-duplicate errors, so it's not silently swallowing. The string-match is locale-fragile (see §3 Patch 1).                                                                                  |
| `cheque_tracker/cheque_tracker/doctype/cheque_batch/cheque_batch.py:44`                    | **P1**   | Inside `_mark_cheques_deposited`. If marking one cheque as Deposited fails, logs the traceback and continues to the next row. Means a Cheque Batch can submit "successfully" while leaving some of its child cheques in their previous status. **Worth tightening:** either re-raise (making batch submit atomic) or surface the failures back to the user via `frappe.msgprint`. |
| `cheque_tracker/cheque_tracker/doctype/cheque_leaf/test_cheque_leaf.py:70`                 | **P3**   | Test code, captures errors from threaded `do_reserve` to surface them in the assertion. Fine.                                                                                                                                                                                                            |

**Verdict:** No bare `except:` (good — every catch specifies `Exception` at minimum). One **P1** in `cheque_batch.py` is the only one I'd actively change.

### 4.2 `frappe.db.commit()` calls

| File:line                                                                                  | Severity | Justification provided?                                                                                                                                                                                                                                                                                                              |
|--------------------------------------------------------------------------------------------|----------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `cheque_tracker/patches/v1_0/add_unique_constraint_cheque_leaf.py:24`                      | **P3**   | After a single DDL `ALTER TABLE ADD UNIQUE INDEX`. DDL auto-commits in MySQL, so the explicit commit is redundant. Harmless.                                                                                                                                                                                                          |
| `cheque_tracker/patches/v1_1/add_financial_posting_fields.py:34`                           | **P2**   | **Inside a `for` loop** that iterates 5 columns. Same DDL-auto-commit reasoning — functionally redundant — but the pattern of `db.commit()` inside a loop is exactly the kind of red flag the audit prompt called out. Acceptable here because each statement is independent DDL, but it should be moved out of the loop or removed. |
| `cheque_tracker/cheque_tracker/doctype/cheque/cheque.py:205`                               | **P0** ⚠️ | **Real bug.** Inside `_handle_outgoing_leaf_reservation` (called from `before_save`) the code calls `frappe.db.begin()`, runs `reserve_leaf()`, then `frappe.db.commit()`. Frappe's outer save() is itself wrapped in a transaction; calling `db.commit()` here **commits the outer transaction prematurely**. If anything later in `before_save` (`_validate_outgoing_cheque_no()`, `_protect_fields_if_submitted_accounting_docs()`) raises, the leaf reservation is already committed and irreversible — a rollback at that point cannot undo it. Discussed further in §6. |

**P0 fix sketch (defer to Phase 2):** the `db.begin() / commit() / rollback()` block in `_handle_outgoing_leaf_reservation` should be replaced with a `frappe.db.savepoint()` (Frappe 14+) so the leaf reservation can be released cleanly if a later validation fails. The `reserve_leaf` function itself uses `SELECT … FOR UPDATE`, which already provides the row-level locking needed.

### 4.3 `frappe.db.sql(...)` writes that bypass the ORM

| File:line                                                                                  | Statement                                                                                                                  | Justified?                                                                                                                                                |
|--------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `cheque_tracker/patches/v1_0/add_unique_constraint_cheque_leaf.py:17`                      | `ALTER TABLE \`tabCheque Leaf\` ADD UNIQUE INDEX ...`                                                                       | ✅ DDL — must use SQL, no ORM equivalent.                                                                                                                  |
| `cheque_tracker/patches/v1_1/add_financial_posting_fields.py:31`                           | `ALTER TABLE \`tabCheque\` ADD COLUMN ...`                                                                                  | ✅ DDL — but should prefer `frappe.db.add_column()` if available; pattern is fragile (see §3 Patch 2).                                                     |
| `cheque_tracker/cheque_tracker/doctype/cheque_book/cheque_book.py:120`                     | `SELECT leaf_status, COUNT(*) ... GROUP BY leaf_status` (read)                                                              | ✅ Aggregation — ORM has `frappe.db.count()` but it doesn't group; SQL is appropriate.                                                                     |
| `cheque_tracker/cheque_tracker/doctype/cheque_book/cheque_book.py:143`                     | `UPDATE \`tabCheque Leaf\` SET leaf_status='Cancelled', modified=NOW() WHERE cheque_book=%s AND leaf_status='Unused'`        | **P2** — bypasses `frappe.db.set_value` and therefore skips Document hooks (no `Cheque Leaf.before_save`/`on_update` runs). Cheque Leaf doesn't have hooks today, so this is safe today but fragile. Also bypasses `track_changes:1` audit on Cheque Leaf. |
| `cheque_tracker/cheque_tracker/doctype/cheque_leaf/cheque_leaf.py:48`                      | `SELECT ... FROM \`tabCheque Leaf\` ... FOR UPDATE` (concurrency lock)                                                      | ✅ `FOR UPDATE` row-locking has no ORM equivalent. Required for atomic leaf reservation. Justified and correct.                                            |
| `cheque_tracker/cheque_tracker/doctype/cheque_leaf/cheque_leaf.py:73`                      | `UPDATE \`tabCheque Leaf\` SET leaf_status='Reserved', ... WHERE name=%s AND leaf_status='Unused'`                          | ✅ Conditional update with double-check on `leaf_status='Unused'` — the second guard against the race window. Justified.                                   |
| `cheque_tracker/cheque_tracker/doctype/cheque_leaf/cheque_leaf.py:88`                      | `SELECT ROW_COUNT() AS r`                                                                                                   | ✅ MariaDB-specific row-count fetch. Justified.                                                                                                            |
| `cheque_tracker/cheque_tracker/report/{4 reports}.py`                                      | `SELECT ... FROM \`tabCheque[ Book]\` WHERE ... ORDER BY ...` (read)                                                        | ✅ Reports — read-only joins and aggregations are SQL by convention in Frappe Script Reports. All four files use parameterized values (`%(name)s`) — see §4.7. |

**Verdict:** No `INSERT` or `DELETE` raw writes. All `UPDATE`s are scoped to the leaf-allocation flow which legitimately needs row-locking. The only nit is `cheque_book.py:143`, which could be replaced by `frappe.db.set_value("Cheque Leaf", {filters}, ...)` but this is **P2** and not worth changing.

### 4.4 Missing `@frappe.whitelist()` on functions called from JS

Every method called via `frappe.call({ method: ... })` from a JS controller was checked. Findings:

| JS call site                                                                                              | Target                                                                                                  | Whitelisted? |
|-----------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|--------------|
| `cheque/cheque.js:431`                                                                                    | `cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial.make_recording_payment_entry`            | ✅            |
| `cheque/cheque.js:478`                                                                                    | `cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial.make_clearance_journal_entry`            | ✅            |
| `cheque/cheque.js:503`                                                                                    | `cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial.process_bounce`                          | ✅            |
| `cheque/cheque.js:534` & `public/js/cheque_tracker.js:164`                                                | `cheque_tracker.cheque_tracker.doctype.cheque.cheque.change_cheque_status`                              | ✅            |
| `public/js/cheque_tracker.js:85`                                                                          | `cheque_tracker.cheque_tracker.doctype.cheque.cheque.hand_over_cheque`                                  | ✅            |

**No misses.** Every JS-invoked Python method has `@frappe.whitelist()`.

**Adjacent finding:** `cheque_tracker/cheque_tracker/doctype/cheque_book/cheque_book.py:169` — `get_book_counters` is whitelisted but is **not called from any JS or Python** in this repo. It looks like a dashboard endpoint that was never wired up. Either it's a stub for an unfinished feature, or it's dead code. Flagging only — see §4.5 below for the related permission gap.

### 4.5 Missing permission guards on whitelisted methods

For each `@frappe.whitelist()` function, checked for a `frappe.has_permission(...)` call (or equivalent) in the body.

| Function                                                                                | `has_permission` call?                                                                | Severity | Notes                                                                                                                                                                                                                                                                                                |
|------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|----------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `cheque.change_cheque_status`                                                            | ✅ `frappe.has_permission("Cheque", "write", doc=doc, throw=True)` (line 347)         | ✅ ok    | Correct. Workflow transitions are write operations.                                                                                                                                                                                                                                                  |
| `cheque.hand_over_cheque`                                                                | ✅ line 405                                                                            | ✅ ok    | Correct.                                                                                                                                                                                                                                                                                             |
| `cheque_financial.make_recording_payment_entry`                                          | ✅ line 156                                                                            | ✅ ok    | Correct. Note: a stricter check (`frappe.has_permission("Payment Entry", "create", throw=True)`) would also be reasonable since the function creates a new Payment Entry. Currently relies on the user already having Cheque write — Treasury User has it, Accounts User has it. Acceptable.        |
| `cheque_financial.make_clearance_journal_entry`                                          | ✅ line 267                                                                            | ✅ ok    | Same caveat — also creates a Journal Entry but only checks Cheque write. Practical risk is low because Treasury / Accounts roles have JE create perms in standard ERPNext setups.                                                                                                                  |
| `cheque_financial.process_bounce`                                                        | ✅ line 375                                                                            | ✅ ok    | Same caveat. Worth considering `frappe.only_for(["Treasury User", "Accounts User", "System Manager"])` for a stricter role gate, since bounce processing is a financially sensitive operation. **P2 suggestion**, not a P0 gap.                                                                       |
| `cheque_book.get_book_counters`                                                          | ❌ **none**                                                                            | **P1**   | No permission check at all. Returns counter values for any Cheque Book name passed in. Cheque Book is reportable to Treasury / Accounts / Auditor, so the data is not strictly secret, but a guest-session call with a guessed book name would still leak counts. Add `frappe.has_permission("Cheque Book", "read", doc=cheque_book, throw=True)` at the top. |

**Verdict:** One **P1** gap on `get_book_counters`. The other five whitelisted functions all have explicit `has_permission` checks; the only suggestion is to consider stricter role gates on the financially-sensitive ones.

### 4.6 Hard-coded company names, account names, user emails

Searched the entire codebase for hardcoded literals of these types.

| Category                | Findings                                                                                                                                            |
|-------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| Company names           | None.                                                                                                                                               |
| GL account names        | None in app code. Test fixture creates `"PDC Receivable - Test"` in `test_cheque_financial.py:36` — appropriate for tests.                          |
| User emails             | `"ahmed@example.com"` in `cheque_tracker/hooks.py:5` and `pyproject.toml:4` (both `app_email` / `authors`). Placeholder values, not a security bug. |
| Bank / customer names   | None.                                                                                                                                               |
| Workflow / status enums | Many string literals like `"Received"`, `"Cleared"`, `"Bounced"` throughout `cheque.py`, hooks, tasks. This is normal Frappe style — the workflow state names are an external contract. Not a smell. |
| Role names              | `"Treasury User"`, `"Accounts User"`, `"Cheque Auditor"`, `"System Manager"` referenced from `hooks.py` (fixtures filter), JS (`has_role` checks), and JSON perms. Same — the role names are an external contract. Not a smell. |

**Verdict:** Clean. The only "hardcoded" items are the placeholder email and the enum strings, which are fine.

### 4.7 SQL string interpolation (f-strings) instead of parameterised queries

Every f-string'd `frappe.db.sql(...)` call was traced back to confirm whether the interpolated values come from user input or trusted literals.

| File:line                                                                                                | Interpolated tokens                                                | Source of tokens                                                                                                                          | Severity |
|----------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|----------|
| `cheque_tracker/patches/v1_1/add_financial_posting_fields.py:32`                                         | `{col_name}`, `{col_type}`                                         | Hardcoded literal dict at lines 19-25. **Not** user-controlled.                                                                            | **P2**   |
| `cheque_tracker/cheque_tracker/report/cheque_book_utilization/cheque_book_utilization.py:38`             | `{" AND ".join(conds)}`                                            | `conds` is a list of literal strings like `"company = %(company)s"` (line 32). User-supplied filter values are bound via `%(name)s` placeholders against the `values` dict (line 46). | **P2**   |
| `cheque_tracker/cheque_tracker/report/deposited_not_cleared/deposited_not_cleared.py:41`                 | `{" AND ".join(conds)}`                                            | Same pattern.                                                                                                                              | **P2**   |
| `cheque_tracker/cheque_tracker/report/bounced_cheques_register/bounced_cheques_register.py:46`           | `{" AND ".join(conds)}`                                            | Same pattern.                                                                                                                              | **P2**   |
| `cheque_tracker/cheque_tracker/report/cheques_due_this_week/cheques_due_this_week.py:47`                 | `{" AND ".join(conds)}`                                            | Same pattern.                                                                                                                              | **P2**   |

**Verdict:** **No SQL injection vector found.** Every f-string interpolation either uses hardcoded literals or interpolates literal *condition fragments* whose user-supplied values are bound separately via `%(name)s`. The pattern is widely used in Frappe core itself. Severity is **P2** because a future maintainer copy-pasting the pattern with a *user-supplied* string would create an injection. Not worth changing in Phase 2 unless a scan tool flags it.

### 4.8 `ignore_permissions = True` density

Not on the original checklist but worth flagging because of the volume.

`ignore_permissions = True` appears **30 times** across the app code (excluding tests):
- 6 in `cheque_financial.py` (PE / JE creation, cheque-event append paths)
- 4 in PE / JE doc-event hook handlers (necessary — handlers run as the PE submitter, who may not have Cheque write perm if Cheque is restricted)
- 2 in `cheque_book.py` (leaf insert during book submit)
- 1 in `cheque.py` (`_flush_events` re-save)

Every use is **defensive and reasonable** — these are all internal mutations triggered by an action the user already had permission for, on a doc the user may not directly have write perms on. The pattern is the standard Frappe idiom. The whitelisted entry-point functions all do their own `has_permission` checks before delegating to these helpers, so there's no privilege escalation.

**Recommendation:** No change. Listing here only for transparency.

### 4.9 Summary table — what to fix in Phase 2

| ID | Severity | File:line                                                                          | Issue                                                                  | Section reference                |
|----|----------|------------------------------------------------------------------------------------|------------------------------------------------------------------------|----------------------------------|
| C1 | **P0**   | `cheque/cheque.py:205`                                                             | `db.begin/commit/rollback` inside `before_save` commits outer txn early | §4.2, also §6 (PE/JE walkthrough) |
| C2 | **P1**   | `cheque_book/cheque_book.py:169` (`get_book_counters`)                             | Whitelisted endpoint with no `has_permission` check                    | §4.5                             |
| C3 | **P1**   | `cheque_batch/cheque_batch.py:44` (`_mark_cheques_deposited`)                       | Swallows per-cheque errors silently; batch can submit with partial state | §4.1                             |
| C4 | **P3**   | `cheque_tracker/tasks.py` (top-level)                                              | Byte-identical duplicate of `cheque_tracker/cheque_tracker/tasks.py`    | §1, §2, §4.1                     |

C1 is the only true blocker. C2 and C3 are recommended fixes before production install. C4 is cleanup.

---

## 5. PE/JE creation logic walkthrough

### Flow A — Cheque issuance to Payment Entry

#### Step-by-step trace

1. User submits a Cheque doc with `cheque_type = "Incoming"`. Frappe core invokes `Cheque.on_submit` (`cheque.py:43-54`).
2. `on_submit` for Incoming: if `self.status == "Draft"`, calls `frappe.db.set_value("Cheque", self.name, "status", "Received")` and updates `self.status` in memory.
3. `on_submit` calls `self._append_event("Received", notes="Cheque submitted.")` then `self._flush_events()` (`cheque.py:53-54`).
4. `_flush_events` (`cheque.py:321-336`) re-fetches the cheque via `frappe.get_doc`, appends the in-memory event row, sets `flags.ignore_permissions = True` and `flags.ignore_validate_update_after_submit = True`, then `persisted.save()`.
5. **`on_submit` does not create a Payment Entry.** Control returns to the user; the cheque is now submitted with status `Received` and one new audit event.
6. User opens the cheque form. `cheque.js` `_setup_buttons` (line 327) renders a **"Create Recording Payment Entry"** button under the *Accounting* group because `docstatus === 1`, `cheque_type === "Incoming"`, and `status` is in `["Received", "Draft"]` (`cheque.js:369-380`).
7. User clicks the button. `_make_recording_pe` (`cheque.js:426`) fires `frappe.confirm`, then `frappe.call({ method: "...cheque_financial.make_recording_payment_entry", args: { cheque_name } })`.
8. Server enters `make_recording_payment_entry` (`cheque_financial.py:142-236`). It calls `frappe.get_doc("Cheque", cheque_name)` then `frappe.has_permission("Cheque", "write", doc=cheque, throw=True)` (line 156).
9. Validations (`cheque_financial.py:159-168`): cheque_type must be `Incoming`, party_type must be `Customer`, party must be set, company must be set, `flt(cheque.amount) > 0`. Each failure raises `frappe.throw`.
10. Calls `_get_pdc_account(cheque)` (`cheque_financial.py:40-51`): returns `cheque.pdc_account` if set, else `Cheque Tracker Settings.pdc_receivable_account` via `frappe.get_cached_doc`, else `frappe.throw`.
11. Calls `_get_receivable_account(cheque)` (`cheque_financial.py:54-66`): reads `Company.default_receivable_account` via `frappe.db.get_value`, else `frappe.throw`.
12. Idempotency branch (`cheque_financial.py:174-188`) on `cheque.recording_payment_entry`: if linked PE has `docstatus == 0`, calls `_update_recording_pe(...)` (line 239) which mutates `paid_amount`, `received_amount`, `paid_from`, `paid_to`, `reference_no` and `pe.save()`s, then returns the existing PE name. If `docstatus == 1`, `frappe.msgprint` and return existing name. If `docstatus == 2` (cancelled), fall through to create a fresh PE.
13. Construct new doc: `pe = frappe.new_doc("Payment Entry")` (line 191). Sets fields:
    - `payment_type = "Receive"`
    - `company = cheque.company`
    - `posting_date = today()`
    - `party_type = "Customer"`, `party = cheque.party`
    - `paid_from = ar_account` (Company AR), `paid_to = pdc_account` (PDC Receivable)
    - `paid_amount = received_amount = flt(cheque.amount)`
    - `source_exchange_rate = target_exchange_rate = 1`
    - `reference_no = cheque.cheque_no`, `reference_date = cheque.issue_date or today()`
    - `remarks = "PDC Recording for Cheque {name} | Cheque No: {no} | Party: {party}"`
    - `paid_from_account_currency = cheque.currency or company.default_currency`
    - `paid_to_account_currency = company.default_currency`
14. If `cheque.reference_doctype == "Sales Invoice"` and `cheque.reference_name` set (`cheque_financial.py:216-224`): reads the SI `outstanding_amount` via `frappe.db.get_value`, then `pe.append("references", { reference_doctype, reference_name, allocated_amount: min(cheque.amount, outstanding) })`.
15. `pe.flags.ignore_permissions = True` (line 226), then `pe.insert()` — saves the PE as **Draft (`docstatus = 0`)**. The function never calls `pe.submit()`.
16. `_set_cheque_fields(cheque_name, {"recording_payment_entry": pe.name})` (line 230) → `frappe.db.set_value("Cheque", cheque_name, {"recording_payment_entry": pe.name})`.
17. `frappe.msgprint("Recording Payment Entry {0} created in Draft. Submit it to finalise.")`. Function returns `pe.name`.
18. JS receives the name, reloads the cheque form, and prompts the user to open the PE.
19. The user navigates to the Draft PE and clicks **Submit** (standard ERPNext flow). Frappe core runs ERPNext's standard PE validation and posts the GL entries (Dr `paid_to` = PDC Receivable, Cr `paid_from` = AR).
20. As a side effect of PE submission, the `Payment Entry: on_submit` doc_event fires `cheque_tracker.cheque_tracker.hooks.payment_entry_hooks.payment_entry_on_submit` (`hooks.py:101`).
21. Handler `_handle_recording_pe_submit` (`payment_entry_hooks.py:55-80`) runs `frappe.get_all("Cheque", filters={"recording_payment_entry": pe.name}, fields=["name", "status"], limit=1)`. If no row, returns. If found and `current_status in ("Draft", "Received")`, calls `frappe.db.set_value("Cheque", cheque_name, {"status": "Received"})`.
22. `_append_event_and_save` (`payment_entry_hooks.py:31-43`) loads the cheque, appends a `Received` event with `reference_doctype="Payment Entry"`, `reference_name=pe.name`, sets both `ignore_permissions` and `ignore_validate_update_after_submit` flags, and saves.
23. Flow ends. Final state: Cheque `docstatus=1`, `status="Received"`, `recording_payment_entry=PE-name`, audit table contains a `Received` event referencing the submitted PE. PE `docstatus=1` with GL entries posted by ERPNext core.

#### Risks identified

**D1 — Auto-submit vs draft**
*Severity:* **P3** (current behavior is the safer default).
*Description:* `make_recording_payment_entry` calls `pe.insert()` only — the PE is left as Draft (`docstatus=0`) and the user must click Submit to post GL.
*Phase 2 recommendation:* Keep Draft as the default. If high-volume sites later need 1-step UX, add an opt-in `auto_submit_recording_pe` flag on Cheque Tracker Settings rather than changing the default.

**D2 — Cheque cancellation does not cancel a Draft PE**
*Severity:* **P1**.
*Description:* `Cheque.on_cancel` (`cheque.py:56-83`) blocks cancellation only if a *submitted* PE/JE is linked (`_has_submitted_accounting_docs`). A linked Draft PE is left orphaned, still pointing at the now-cancelled cheque. Submitting that orphan later will run `payment_entry_on_submit`, which writes a `Received` audit event against the cancelled cheque (the status update is skipped by the `safe_to_set` guard, but the event row still appears).
*Phase 2 recommendation:* In `Cheque.on_cancel`, before logging the Cancelled event, look up `recording_payment_entry`; if it exists with `docstatus == 0`, call `pe.cancel()` (or `frappe.delete_doc("Payment Entry", pe_name)` since it's a draft) and clear the link via `db.set_value`.

**D3 — Cheque amendment leaves orphan Draft PE behind**
*Severity:* **P2**.
*Description:* Amend = cancel + new-doc-with-`amended_from`. With submitted PE linked, the cancel step is correctly blocked (D2's mitigation also covers this). With only a Draft PE, the cancel succeeds and the orphan persists; the new amended Cheque has no `recording_payment_entry` link and the user must regenerate one manually. No automatic re-link or copy is performed.
*Phase 2 recommendation:* Same fix as D2 (cancelling the Draft PE on `on_cancel`) closes the orphan path automatically. Optionally, document in `README.md` that the amended cheque starts with a fresh PE workflow.

**D4 — Idempotency of `on_submit` firing twice**
*Severity:* **P3**.
*Description:* `Cheque.on_submit` does not create a PE — it only writes status/event rows. PE creation is exclusively user-triggered via the whitelisted `make_recording_payment_entry`, which guards on `cheque.recording_payment_entry` (returns existing Draft / existing Submitted; only creates new when status is `2` or unset). A duplicate PE from re-submission of the cheque is therefore not possible. A theoretical race exists between `pe.insert()` (line 227) and `_set_cheque_fields` (line 230) if two concurrent button clicks beat the link write, but the window is sub-millisecond.
*Phase 2 recommendation:* No code change required. If the race is ever observed, wrap lines 174-230 in a `frappe.db.savepoint("recording_pe")` and add a `SELECT ... FOR UPDATE` row-lock on the Cheque before the idempotency check.

---
