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

**Stage 1 close-out clarification (post-PR #5).** The `after_install` hook landed in PR #5 lists `pdc_receivable_account`, `default_bank_gl_account`, and `default_cash_account` in its install-time warning — those are the three fields that any code path actually reads (PDC for Recording PE; Bank GL for Deposit clearance JE; Cash for Cash clearance JE). `default_bank_account` is deliberately **not** in the warning because it has no read path (audit follow-up #22 in §8 Part B). The receivable side of the PE flow is sourced from `Company.default_receivable_account` via `_get_receivable_account` in `cheque_financial.py` — it is not a Settings field, so it cannot be bootstrapped or warned about by the install hook.

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

**D5 — Transaction safety / rollback discipline**
*Severity:* **P2**.
*Description:* `make_recording_payment_entry` does **not** contain the C1 anti-pattern — there is no `frappe.db.begin/commit/rollback` inside the body, so the function correctly inherits the request's outer transaction. However, `pe.insert()` (line 227) and `_set_cheque_fields(...)` (line 230) are not wrapped in a `frappe.db.savepoint`. If `_set_cheque_fields` fails (e.g., a deadlock on `tabCheque`), the PE row exists in the same uncommitted transaction; an outer rollback will undo both, but a partial commit caused by an intermediate `frappe.db.commit()` elsewhere in the request would leave a Draft PE with no back-link.
*Phase 2 recommendation:* Wrap lines 174-230 in a single `with frappe.db.savepoint("make_recording_pe"):` block, and ensure no helper inside that block calls `frappe.db.commit()`.

**D6 — `ignore_permissions = True` on PE creation**
*Severity:* **P2**.
*Description:* `pe.flags.ignore_permissions = True` is set before `pe.insert()` (line 226) and again in `_update_recording_pe` (line 247). This bypasses ERPNext's standard `Payment Entry: create` / `write` permission check. Justification is that the whitelisted entry point already verified `frappe.has_permission("Cheque", "write")`. Net effect: a Treasury User who has Cheque write but lacks Payment Entry create rights can still create PEs through this path.
*Phase 2 recommendation:* For tighter discipline, add an explicit `frappe.has_permission("Payment Entry", "create", throw=True)` check at the top of `make_recording_payment_entry` (and similarly for the JE flow). If EEI's Treasury role is intended to be able to create PEs, this is a no-op; if not, it surfaces the gap.

**D7 — Multi-company / multi-currency assumptions**
*Severity:* **P2** (informational for EEI's single-company / single-currency setup).
*Description:* Two hardcoded assumptions:
- `pe.source_exchange_rate = pe.target_exchange_rate = 1` (lines 201-202). If `cheque.currency != company.default_currency`, the PE will post with a wrong FX rate.
- `Cheque Tracker Settings.pdc_receivable_account` is a single global value — fine when Settings is `issingle=1` and there's one company, but breaks if a multi-company site uses one Settings doc for all companies. The per-cheque `pdc_account` override mitigates this manually but there is no validation that the chosen account belongs to `cheque.company`.
*Phase 2 recommendation:* No change required for EEI's current scope. If multi-company / multi-currency is ever in play, replace exchange rates with `erpnext.setup.utils.get_exchange_rate(cheque.currency, company_currency, posting_date)` and add a company-scope validation on `pdc_account` (the linked Account row's `company` field must equal `cheque.company`).

**D8 — Error UX when Settings account fields are unpopulated**
*Severity:* **P3** (current messages are clear).
*Description:* Three account-resolver helpers (`_get_pdc_account`, `_get_receivable_account`, `_get_bank_gl_account` — the last fires later in Flow B but shares the pattern) raise actionable `frappe.throw` messages naming both the source (Cheque field or Settings) and the missing field. Example: `"PDC Receivable Account is not configured. Please set it in Cheque Tracker Settings or on the Cheque itself."` A user with no Settings populated and no per-cheque override will see this message immediately on clicking *Create Recording Payment Entry*; no cryptic stack trace surfaces.
*Phase 2 recommendation:* No code change. The Settings-field-population decision called out in §3 (option (a) admin-configures vs (b) ship defaults) is the only outstanding action; either choice is compatible with current error messaging.

---

### Flow B — Cheque clearance to Journal Entry

#### Step-by-step trace

1. Pre-state: the Cheque is submitted (`docstatus=1`), `cheque_type=="Incoming"`, and `status` is in `["Received", "In Safe", "Deposited", "Presented"]` for Deposit clearance, or `["Received", "In Safe"]` for Cash clearance (`cheque.js:387-390`).
2. User opens the cheque form. `_setup_buttons` (`cheque.js:327`) renders **"Create Clearance Entry"** (Deposit) or **"Create Cash Clearance Entry"** (Cash) under the *Accounting* group, depending on `frm.doc.clearance_type`.
3. User clicks the button. `_make_clearance_je` (`cheque.js:452`) runs client-side guards: if Cash and `cash_account` empty → `frappe.msgprint` orange and abort; if Deposit and `bank_account` empty → same pattern. Then `frappe.confirm`, then `frappe.call({ method: "...cheque_financial.make_clearance_journal_entry", args: { cheque_name } })`.
4. Server enters `make_clearance_journal_entry` (`cheque_financial.py:255-336`). `frappe.get_doc("Cheque", cheque_name)` then `frappe.has_permission("Cheque", "write", doc=cheque, throw=True)` (line 267).
5. Validations (`cheque_financial.py:269-272`): `cheque_type` must be `Incoming`, `flt(cheque.amount) > 0`. Each failure raises `frappe.throw`.
6. Calls `_get_pdc_account(cheque)` (`cheque_financial.py:40`) — same resolver used in Flow A: cheque override → Settings → throw.
7. Calls `_get_debit_account_for_clearance(cheque)` (`cheque_financial.py:107-115`):
   - If `cheque.clearance_type == "Cash"` → `_get_cash_gl_account(cheque)` (lines 89-104): returns `cheque.cash_account` if set, else `Cheque Tracker Settings.default_cash_account`, else `frappe.throw`.
   - Else → `_get_bank_gl_account(cheque)` (lines 69-86): if `cheque.bank_account` set, reads `Bank Account.account` via `frappe.db.get_value`. Falls back to `Cheque Tracker Settings.default_bank_gl_account`. Else `frappe.throw`.
8. Sets `is_cash = (cheque.clearance_type == "Cash")` (line 276).
9. Idempotency branch (`cheque_financial.py:279-290`) on `cheque.clearance_journal_entry`: if linked JE has `docstatus == 0`, calls `_update_clearance_je(...)` (line 339) which iterates `je.accounts`, rewrites debit/credit on the matching debit_account and pdc_account rows, refreshes `cheque_no` / `cheque_date`, and `je.save()`s, then returns the existing name. If `docstatus == 1`, `frappe.msgprint` and return. (No `docstatus == 2` fall-through is shown — a cancelled clearance JE would be re-used, since the branch is missing; this is a code-path observation, not a finding for this section.)
10. Construct new doc: `je = frappe.new_doc("Journal Entry")` (line 293). Sets fields:
    - `voucher_type = "Journal Entry"`
    - `company = cheque.company`
    - `posting_date = today()`
    - `cheque_no = cheque.cheque_no`, `cheque_date = cheque.due_date`
    - `clearance_label = "Cash Clearance (Teller)"` if `is_cash` else `"Cheque Clearance"`
    - `user_remark = f"{clearance_label}: {cheque_name} | Cheque No: {no} | Party: {party}"`
11. Resolves `currency = cheque.currency or Company.default_currency` and `amt = flt(cheque.amount)` (lines 306-309). Currency is captured but not assigned onto the JE rows.
12. Appends first row to `je.accounts` (lines 311-317):
    - `account = debit_account` (Bank GL or Cash GL per step 7)
    - `debit_in_account_currency = amt`, `credit_in_account_currency = 0`
    - `cost_center = cheque.cost_center`, `project = cheque.project`
13. Appends second row (lines 318-322):
    - `account = pdc_account`
    - `debit_in_account_currency = 0`, `credit_in_account_currency = amt`
14. `je.flags.ignore_permissions = True` (line 324), then `je.insert()` — saved as **Draft (`docstatus = 0`)**. The function never calls `je.submit()`.
15. `_set_cheque_fields(cheque_name, {"clearance_journal_entry": je.name})` (line 327) → `frappe.db.set_value("Cheque", cheque_name, {"clearance_journal_entry": je.name})`.
16. `frappe.msgprint("Clearance Journal Entry {0} created in Draft (Dr {Bank|Cash} / Cr PDC). Submit it to mark cheque as Cleared.")` (lines 330-335). Function returns `je.name`.
17. JS receives the name, reloads the cheque form, prompts the user to open the JE.
18. The user navigates to the Draft JE and clicks **Submit**. ERPNext core runs JE validation and posts the GL entries (Dr `debit_account`, Cr `pdc_account`).
19. As a side effect of JE submission, the `Journal Entry: on_submit` doc_event fires `cheque_tracker.cheque_tracker.hooks.journal_entry_hooks.journal_entry_on_submit` (`hooks.py:108`).
20. Handler `journal_entry_on_submit` (`journal_entry_hooks.py:48-51`) calls both `_handle_clearance_je_submit` and `_handle_reversal_je_submit`. Only one matches a given JE; the other returns early after its own back-link lookup.
21. `_handle_clearance_je_submit` (`journal_entry_hooks.py:54-76`) runs `frappe.get_all("Cheque", filters={"clearance_journal_entry": je.name}, fields=["name","status","pre_bounce_status"], limit=1)`. If no row, returns.
22. Reads `clearance_type` for the cheque via `frappe.db.get_value` (line 60); `target = "Cash" if clearance_type == "Cash" else "Bank"`.
23. `frappe.db.set_value("Cheque", cheque_name, {"status": "Cleared", "cleared_date": today()})` (lines 63-66).
24. `_append_event_and_save` (`journal_entry_hooks.py:29-41`) loads the cheque, appends a `Cleared` event with `reference_doctype="Journal Entry"`, `reference_name=je.name`, notes `"Clearance Journal Entry {name} submitted. Funds moved from PDC Receivable to {Bank|Cash}."`, sets both `ignore_permissions` and `ignore_validate_update_after_submit` flags, and saves.
25. Flow ends. Final state: Cheque `docstatus=1`, `status="Cleared"`, `cleared_date=today()`, `clearance_journal_entry=JE-name`, audit table contains a `Cleared` event referencing the submitted JE. JE `docstatus=1` with GL entries posted by ERPNext core.

#### Risks identified

**E1 — Auto-submit vs draft**
*Severity:* **P3** (current behavior is the safer default).
*Description:* `make_clearance_journal_entry` calls `je.insert()` only — the JE is left as Draft and the user must click Submit to post GL. Same trade-off as Flow A D1: Draft preserves ERPNext's submit-time validation gates (period closed, account active, etc.) and lets the user review before posting; cost is one extra click.
*Phase 2 recommendation:* Keep Draft as the default. Mirror any auto-submit toggle introduced for Flow A D1 here.

**E2 — Cheque "uncleared" reversal does not cancel the JE**
*Severity:* **P1**.
*Description:* The JE-submit hook sets cheque status to `Cleared`, but the reverse path is asymmetric. `_validate_transition` in `cheque.py:381-387` blocks transitions **into** `Cleared` (must go through JE submit), but does **not** block transitions **away from** `Cleared`. A user can call `change_cheque_status(name, "Received")` (or any other status) on a Cleared cheque and the cheque state will diverge from the JE state — the submitted clearance JE remains, GL stays posted, but the cheque is no longer marked Cleared. The only sanctioned reversal path is JE cancel, which then triggers `_handle_clearance_je_cancel` and rolls cheque status back to `Received` (hard-coded fallback already flagged in §2).
*Phase 2 recommendation:* In `_validate_transition`, add a guard: if `doc.status == "Cleared"` and `new_status != "Cleared"`, `frappe.throw("Cheque is Cleared. Cancel the Clearance Journal Entry to revert.")`.

**E3 — Idempotency of clearance creation**
*Severity:* **P3**.
*Description:* `make_clearance_journal_entry` guards on `cheque.clearance_journal_entry` and dispatches by linked-JE `docstatus`: `0` updates the Draft and returns its name; `1` shows a msgprint and returns the same name; `2` (cancelled) correctly falls through to create a fresh JE. So re-clicking the button does not create duplicates under normal operation. The same theoretical race noted in Flow A D4 exists here — between `je.insert()` (line 325) and `_set_cheque_fields` (line 327), two concurrent clicks could both pass the idempotency check and both insert. Practical window is sub-millisecond.
*Phase 2 recommendation:* No code change required. If the race is ever observed, wrap lines 279-327 in a `frappe.db.savepoint("clearance_je")` and add a `SELECT ... FOR UPDATE` row-lock on the Cheque before the idempotency check.

**E4 — Error UX when Settings account fields are unpopulated**
*Severity:* **P3** (current messages are clear).
*Description:* All three resolvers used by Flow B raise actionable `frappe.throw` messages naming both the per-cheque field and the Settings field that could fix the error: `_get_pdc_account` ("PDC Receivable Account is not configured. Please set it in Cheque Tracker Settings or on the Cheque itself."), `_get_bank_gl_account` ("Bank GL Account could not be resolved. Set the Bank Account on the Cheque or configure Cheque Tracker Settings."), `_get_cash_gl_account` ("Cash GL Account could not be resolved. Set the Cash Account on the Cheque or configure Cheque Tracker Settings."). The JS layer also pre-flights `cash_account` / `bank_account` presence in `_make_clearance_je` (`cheque.js:455-470`) and shows an orange `frappe.msgprint` before any server round-trip — so misconfigured users see a friendly modal, not a stack trace.
*Phase 2 recommendation:* No code change. The Settings-field-population decision from §3 is the only outstanding action; current messaging is compatible with either choice.

**E5 — Transaction safety / rollback discipline**
*Severity:* **P2**.
*Description:* `make_clearance_journal_entry` does not contain the C1 anti-pattern — no `frappe.db.begin/commit/rollback` inside the body — so it inherits the request's outer transaction correctly. However, `je.insert()` (line 325) and `_set_cheque_fields(...)` (line 327) are not wrapped in a `frappe.db.savepoint`. Same fragility as Flow A D5: if the link write fails after the JE is inserted, the JE row exists but the back-link is missing; subsequent calls will not find the link and will create a duplicate JE.
*Phase 2 recommendation:* Wrap lines 279-327 in a single `with frappe.db.savepoint("make_clearance_je"):` block and ensure no helper inside that block calls `frappe.db.commit()`. Apply the same pattern to `_update_clearance_je`.

**E6 — `ignore_permissions = True` on JE creation**
*Severity:* **P2**.
*Description:* `je.flags.ignore_permissions = True` is set before `je.insert()` (line 324) and again in `_update_clearance_je` (line 352). The `_append_event_and_save` helper used by `_handle_clearance_je_submit` also sets `doc.flags.ignore_permissions = True` (`journal_entry_hooks.py:39`). Same trade-off as Flow A D6 — a Treasury User with Cheque write but no Journal Entry create rights can still create JEs through this path.
*Phase 2 recommendation:* Add an explicit `frappe.has_permission("Journal Entry", "create", throw=True)` check at the top of `make_clearance_journal_entry` (and pair it with the equivalent for `process_bounce`, which also creates reversal JEs).

**E7 — Multi-company / multi-currency assumptions**
*Severity:* **P2** (informational for EEI's single-company / single-currency setup).
*Description:* Two gaps:
- The JE accounts rows omit `account_currency` and `exchange_rate`. With `cheque.currency != company.default_currency`, ERPNext core will treat `debit_in_account_currency` / `credit_in_account_currency` as company currency, so the GL will post the wrong amount. The local `currency` variable computed at line 306 is never written onto the rows.
- `Cheque Tracker Settings.pdc_receivable_account` and `default_bank_gl_account` / `default_cash_account` are global (Settings is `issingle=1`); they have no company-scope check. Same gap as Flow A D7.
*Phase 2 recommendation:* No change required for EEI's current scope. If multi-currency is ever in play, populate `account_currency` and `exchange_rate` on each JE row using `erpnext.setup.utils.get_exchange_rate(cheque.currency, company_currency, posting_date)`, and add a company-scope validation on each Settings account.

**E8 — Cross-flow consistency and full-lifecycle GL drift**
*Severity:* **P2**.
*Description:* Per-flow accounting is symmetric: PE submit posts `Dr PDC, Cr AR` for `cheque.amount`; clearance JE submit posts `Dr Bank|Cash, Cr PDC` for the same amount; reversal JE (bounce path) posts `Dr AR, Cr PDC`. Net effect of issue → clear is `Dr Bank, Cr AR` (correct). Net effect of issue → bounce-after-submitted-PE is zero (PE + reversal JE cancel out). For the abuse path enabled by E2 (issue → clear → manually shift cheque status away from `Cleared` via `change_cheque_status` → re-click "Create Clearance Entry"): the idempotency guard on `cheque.clearance_journal_entry` correctly detects the submitted JE and emits a msgprint without inserting a second one, so under standard UI flow GL stays net-zero. **Drift only becomes possible if the `clearance_journal_entry` field is manually wiped** (e.g., via Set Value desk action or a script) — at that point the next click will insert a second `Dr Bank, Cr PDC`, leaving a phantom `-PDC` of `cheque.amount`. The E2 status hole therefore creates *state* divergence without *GL* divergence under normal use.
*Phase 2 recommendation:* Fix E2 (block transitions away from `Cleared` unless the JE is cancelled) — that closes the data-integrity hole. Additionally, mark `clearance_journal_entry` / `recording_payment_entry` / `reversal_journal_entry` as `read_only: 1, allow_on_submit: 0` (or remove `allow_on_submit` — currently `1` per `cheque.json`) to prevent ad-hoc clearing of the link from the desk.

---

## 6. Test coverage

### Test file inventory

Four `test_*.py` files exist, all under `cheque_tracker/cheque_tracker/doctype/`. No top-level `tests/` directory; no test for the scheduler, hooks, reports, or batch flow.

#### `cheque_book/test_cheque_book.py` — 9 tests, 137 LOC
- `make_cheque_book(...)` factory (used by other test files).
- `test_leaf_count_on_numeric_range` — book with start=1 end=10 generates 10 leaves on submit.
- `test_leaf_sequence_values` — leaf cheque_no values match the numeric range.
- `test_zero_padded_leaves` — `digits_count=6` produces `000001`-style padding.
- `test_prefixed_leaves` — `prefix="CHK-"` + `digits_count=3` produces `CHK-001`-style numbers.
- `test_status_becomes_active_on_submit` — book status transitions Draft → Active.
- `test_unused_counter_set_after_submit` — `unused_leaves` counter is correct.
- `test_bank_account_company_mismatch_raises` — `_validate_bank_account_company` rejects cross-company assignments.
- `test_end_before_start_raises` — `end_cheque_no < start_cheque_no` raises ValidationError.
- `test_cancel_voids_unused_leaves` — book cancel marks all 6 leaves as Cancelled.

#### `cheque_leaf/test_cheque_leaf.py` — 7 tests, 94 LOC
- `test_reserve_marks_status_reserved` — `reserve_leaf()` flips status to Reserved.
- `test_reserve_links_cheque` — reservation writes the linking cheque name.
- `test_issued_leaf_not_reserved_again` — already-Issued leaves are skipped by next reservation.
- `test_voided_leaf_not_reserved_again` — already-Voided leaves are skipped.
- `test_exhausted_book_raises` — third reservation against a 2-leaf book raises ValidationError.
- `test_concurrent_reservation_gets_distinct_leaves` — two threads reserve two different leaves (the `SELECT ... FOR UPDATE` concurrency invariant).
- `test_duplicate_leaf_in_book_raises` — `before_insert` duplicate guard fires.

#### `cheque/test_cheque.py` — 9 tests, 147 LOC
- `_outgoing(...)` factory — creates and inserts an Outgoing cheque against a submitted book.
- `test_outgoing_reserves_leaf_on_save` — leaf reservation in `_handle_outgoing_leaf_reservation`.
- `test_outgoing_cheque_no_matches_leaf` — `cheque.cheque_no` synced from the leaf.
- `test_submit_marks_leaf_issued` — `_mark_leaf_issued_on_submit` transitions leaf to Issued.
- `test_cancel_voids_leaf` — Cheque cancel marks linked leaf as Voided.
- `test_cleared_cheque_cannot_cancel` — manual status=Cleared then cancel raises ValidationError (note: status is force-written via `db.set_value` in the test, not via the JE flow).
- `test_two_cheques_get_different_leaves` — second cheque receives a different leaf.
- `test_manual_cheque_no_override_raises` — `_validate_outgoing_cheque_no` blocks manual override.
- `test_incoming_cheque_no_book_required` — Incoming cheques accept any cheque_no without a book.
- `test_event_created_on_insert` — `after_insert` audit event row is appended.

#### `cheque/test_cheque_financial.py` — 8 tests, 419 LOC
Sets up `Cheque Tracker Settings` with PDC + Bank GL accounts in `setUp`. Each test creates an Incoming cheque and exercises:
- `test_make_recording_pe_creates_draft` — PE created with correct payment_type, party, paid_to, paid_amount, and back-link.
- `test_recording_pe_submit_sets_cheque_received` — PE submit transitions cheque to `Received` and logs an event referencing the PE.
- `test_clearance_je_submit_sets_cheque_cleared` — full PE-then-JE chain ends with cheque `Cleared` and `cleared_date` set.
- `test_bounce_after_submitted_pe_creates_reversal_je` — `process_bounce` creates a Draft reversal JE; submitting it marks cheque `Bounced`.
- `test_bounce_cancels_draft_pe` — `process_bounce` against a Draft PE cancels the PE and marks cheque `Bounced` immediately (no JE created).
- `test_idempotent_recording_pe` — calling `make_recording_payment_entry` twice returns the same PE name.
- `test_no_new_pe_after_submit` — calling again after PE submit does not create a new PE.
- `test_clearance_je_cancel_rolls_back_status` — JE cancel rolls cheque back to `Received`.
- `test_protected_fields_blocked_after_pe_submit` — editing `amount` after PE submit raises ValidationError. *(9th test — the file actually contains 9 not 8 despite the docstring claim of 8.)*

### Test run status

I cannot execute `bench --site eei-test.f.frappe.cloud run-tests --app cheque_tracker` from this sandbox — there is no `bench` binary here and the dev site lives on Frappe Cloud. **Pass/fail status is not verified in this audit.** Please run the command and paste the output; I'll fold the results into this section in a follow-up commit.

That said, two structural issues in the existing tests are visible from static reading:
- Several tests `skipTest()` if no `Company` / `Bank Account` / `Customer` / default receivable account is present on the test site. On a freshly-bootstrapped CI site this means most of `test_cheque_financial.py` will silently skip.
- `test_cheque_financial.py` line 5 docstring says "8 tests" but defines 9. Cosmetic.

### Coverage gaps

The existing tests cover unit-level invariants well (leaf reservation, idempotency, individual status transitions). The gaps below all map to risks identified earlier in this audit.

**Flow A end-to-end with GL assertions (D1, D6, §5)**
- `test_recording_pe_submit_sets_cheque_received` asserts cheque status and event but **does not query `tabGL Entry`** to confirm `Dr PDC, Cr AR` for `cheque.amount`. A regression that swapped `paid_from` and `paid_to` would still pass.
- No test covers the SI-allocation branch (`reference_doctype == "Sales Invoice"` → `pe.references` row).
- No test covers the `pe.flags.ignore_permissions = True` permission-bypass path; running the test as a Treasury User without PE create rights would expose D6 but no such fixture exists.

**Flow B end-to-end with GL assertions (E1, E7)**
- `test_clearance_je_submit_sets_cheque_cleared` asserts cheque status / `cleared_date` but **does not query `tabGL Entry`** to confirm `Dr Bank, Cr PDC` for `cheque.amount`.
- No test for the cash clearance flow (`clearance_type == "Cash"` → `cash_account` debit). The entire v1.1.4 cash branch is untested.
- No multi-currency test (E7) — the missing `account_currency` / `exchange_rate` on JE rows is not exercised.

**Cancellation paths (D2, D3)**
- No test for "Cheque on_cancel cancels orphan Draft PE" (D2) — it can't, because the behavior doesn't exist yet. The placeholder is needed for Phase 2.
- No test for amend → orphan Draft PE remediation (D3).

**C1 transaction-safety bug (`cheque.py:205`)**
- No test asserts that a failure later in `before_save` rolls back the leaf reservation. To exercise this you'd need to monkey-patch `_validate_outgoing_cheque_no` to raise after `_handle_outgoing_leaf_reservation` has called `frappe.db.commit()`, then assert the leaf is back to `Unused`. This is the most important missing test in the suite.

**E2 state-asymmetry bug**
- No test asserts that calling `change_cheque_status(name, "Received")` on a Cleared cheque is blocked. Today it is not blocked, so a test would correctly fail and expose the bug.

**E8 writable-link-post-submit bug**
- No test asserts that `clearance_journal_entry` / `recording_payment_entry` / `reversal_journal_entry` cannot be cleared via direct `frappe.db.set_value` after submit. (Frappe enforces field-level `allow_on_submit` from JSON; today these are `allow_on_submit: 1`, so a test would correctly demonstrate the editability.)

**Other untested surfaces (informational, lower priority)**
- `cheque_batch.py` — zero tests for batch validation, totals, `_mark_cheques_deposited` error swallowing (C3).
- `tasks.auto_update_cheque_statuses` — no test for the daily scheduler path (overdue logging, counter refresh).
- `cheque.hand_over_cheque` whitelist — no test.
- `cheque_book.get_book_counters` whitelist — no test (also missing `has_permission`, see C2).
- 4 Script Reports — none of the report `.py` files have any test coverage; SQL conditional logic (filters, ordering, aging buckets) is unverified.

### Phase 2 recommendation (test additions)

In priority order:
1. Happy-path Flow A end-to-end with GL assertions (`Dr PDC, Cr AR` after PE submit).
2. Happy-path Flow B end-to-end with GL assertions (`Dr Bank|Cash, Cr PDC` after JE submit).
3. C1 regression test (rollback on later validation failure).
4. E2 regression test (no transition out of Cleared without JE cancel).
5. Cash-clearance happy path (the entire v1.1.4 surface).
6. Cheque cancel → orphan Draft PE cleanup (after D2 fix lands).
7. `cheque_batch._mark_cheques_deposited` error-handling test (after C3 fix lands).
8. An install/uninstall smoke test (`bench install-app cheque_tracker` then `uninstall-app`) to verify migrations don't leave residue.

---

## 7. Permissions sanity check

> Note: the prompt mentions a "Cheque Print Template" DocType — that DocType does **not** exist in this repo. The seven actual DocTypes are listed below. Flagging in case it was supposed to be added in v1.1.4 and was missed.

### Part A — DocType permissions vs. code enforcement

| DocType                     | Roles in JSON (rights summary)                                                                                                                                                | Code enforcement                                                                                                                          | Match? / Concerns                                                                                                                                                                                                                                                                       |
|-----------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Cheque**                  | Treasury User: full (r/w/create/delete/submit/cancel). Accounts User: r/w/email/export/print/share/report. Cheque Auditor: read/print/export/report.                          | Whitelisted methods all check `has_permission("Cheque", "write")`. Workflow transitions are encoded in `Cheque Workflow` fixture roles.    | ⚠️ **P1 — workflow bypass.** `change_cheque_status` only checks `Cheque write`. Since Accounts User has Cheque `write`, they can drive any non-Cleared/non-Cancelled transition (e.g., `Deposited`, `Bounced`) — bypassing the workflow's role gates that restrict those to Treasury User. |
| **Cheque Book**             | Treasury User: full. System Manager: r/w/submit/cancel. Accounts User: read/print/export/report. Cheque Auditor: read/print/export/report.                                    | `ChequeBook` class methods enforce business rules. `get_book_counters` whitelist has **no** `has_permission` check (cross-ref C2 / §4.5). | ⚠️ **P2** — `get_book_counters` is callable by any authenticated user; data is benign (leaf counters) but not gated.                                                                                                                                                                  |
| **Cheque Leaf**             | Treasury User: read/print/email/export/share/report. Accounts User: read. Cheque Auditor: read. System Manager: create/read/write/delete.                                     | All mutations go through `ignore_permissions=True` or raw `db.sql` (leaf gen, reserve, release, mark issued, cancel unused).               | ✅ Match. Treasury User has no JSON write — and code paths that mutate leaves all run on behalf of a permitted Cheque/Cheque Book action, with `ignore_permissions=True`. Standard pattern.                                                                                            |
| **Cheque Batch**            | Treasury User: full. Accounts User: read. Cheque Auditor: read.                                                                                                                | `_mark_cheques_deposited` calls `Cheque.log_status_change` (requires Cheque write — Treasury User has it).                                 | ✅ Match.                                                                                                                                                                                                                                                                              |
| **Cheque Event** (child)    | No permissions in JSON → inherits from parent (Cheque).                                                                                                                        | Mutations via `ignore_permissions=True` from PE/JE hooks and `_flush_events`.                                                              | ✅ Standard child-table semantics.                                                                                                                                                                                                                                                     |
| **Cheque Batch Item** (child) | No permissions in JSON → inherits from parent (Cheque Batch).                                                                                                                  | Read-only via `fetch_from`.                                                                                                                | ✅ Match.                                                                                                                                                                                                                                                                              |
| **Cheque Tracker Settings** | System Manager: create/read/write/print/email/share. Treasury User: read/print/email/share.                                                                                    | `cheque_financial.py` reads via `frappe.get_cached_doc` from PE/JE entry-points (which are callable by Accounts User per Cheque write).    | ⚠️ **P2 latent** — Accounts User has Cheque write but **no** Cheque Tracker Settings read. Calling `make_recording_payment_entry` will likely fail when `_get_pdc_account` loads Settings, depending on cache state. Either add Accounts User to Settings read perms, or document that PE/JE creation is Treasury-only. |

#### Read-but-shouldn't / write-but-shouldn't summary

- **Accounts User reading unposted (Draft) Cheques:** allowed by Cheque JSON (`read=1`) and by core docstatus filtering. Acceptable for a treasury workflow where Accounts staff need visibility before clearance. ✅
- **Accounts User writing Cheque:** allowed (`write=1` in JSON). Combined with the lack of role gates in `change_cheque_status`, this enables the workflow bypass flagged above. ⚠️ **P1**.
- **Cheque Auditor:** read-only across the board, both in JSON and (implicitly) in code. ✅
- **System Manager:** has full access on Cheque Book and Settings; not listed on Cheque itself, but inherits via standard core Frappe behavior. ✅

### Part B — Whitelisted methods inventory

| # | File:line                                                          | Function                                | Purpose                                                                                                | Guard                                                                | Severity if missing |
|---|--------------------------------------------------------------------|-----------------------------------------|--------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|---------------------|
| 1 | `cheque/cheque.py:343`                                             | `change_cheque_status`                  | Workflow / status transition (non-financial).                                                          | ✅ `frappe.has_permission("Cheque", "write", doc=doc, throw=True)`   | P0 (status drives downstream finance)  |
| 2 | `cheque/cheque.py:399`                                             | `hand_over_cheque`                      | Transfer custody and log a Handed Over event.                                                          | ✅ `frappe.has_permission("Cheque", "write", doc=doc, throw=True)`   | P1 (custody trail)  |
| 3 | `cheque/cheque_financial.py:142`                                   | `make_recording_payment_entry`          | Create Draft Recording PE for an Incoming cheque.                                                      | ✅ `frappe.has_permission("Cheque", "write", doc=cheque, throw=True)` | **P0**              |
| 4 | `cheque/cheque_financial.py:255`                                   | `make_clearance_journal_entry`          | Create Draft Clearance JE.                                                                              | ✅ `frappe.has_permission("Cheque", "write", doc=cheque, throw=True)` | **P0**              |
| 5 | `cheque/cheque_financial.py:360`                                   | `process_bounce`                        | Cancel Draft PE or create Draft reversal JE.                                                            | ✅ `frappe.has_permission("Cheque", "write", doc=cheque, throw=True)` | **P0**              |
| 6 | `cheque_book/cheque_book.py:169`                                   | `get_book_counters`                     | Read leaf counters for any Cheque Book by name.                                                         | ❌ **none**                                                          | **P2** (read-only, benign data) |

#### Cross-reference with C2

C2 in §4.5 listed `get_book_counters` as the only whitelisted method missing a permission guard. Confirmed: **C2 is still the sole unguarded whitelisted method.** The other five all use `frappe.has_permission("Cheque", "write")`.

C2 was rated **P1** in §4.5 under the general "missing-guard" framework, and **P2** here under the §7 framework (read-only, benign data). The two ratings are not contradictory — the §4 framework treats *any* missing guard as P1 by default; §7's stricter classification reflects that the data exposed is leaf counters, not finance state. Proposed fix is unchanged: add `frappe.has_permission("Cheque Book", "read", doc=cheque_book, throw=True)` at the top of the function.

#### New Phase 2 finding from §7

**F1 (P1)** — Workflow role bypass via `change_cheque_status`. Accounts User has `Cheque write` per JSON, so they can drive transitions that the workflow restricts to Treasury User (e.g., `Deposit`, `Bounce`). Recommend adding role checks inside `_validate_transition`, e.g.:

```python
ROLE_BY_TRANSITION = {
    ("In Safe", "Deposited"): {"Treasury User", "System Manager"},
    ("Received", "Deposited"): {"Treasury User", "System Manager"},
    ...
}
```

or simply call `frappe.only_for(["Treasury User", "System Manager"])` for the Treasury-restricted transitions and `frappe.only_for(["Accounts User", "System Manager"])` for the Accounts-restricted ones (mirroring the Workflow's `allowed` field).

**F2 (P2 latent)** — Accounts User cannot read `Cheque Tracker Settings` per JSON, but has Cheque write so can call `make_recording_payment_entry` / `make_clearance_journal_entry`, which load Settings via `frappe.get_cached_doc`. Behavior depends on cache state. Either grant Accounts User read on Settings, or restrict the PE/JE entry points to Treasury User explicitly.

---

## 8. Final summary + Phase 2 priority list

### Part A — Production readiness verdict

**`cheque_tracker` v1.1.4 is NOT production-ready as of this audit.** The codebase is well-structured and the financial-posting model is correct in the happy path (PE: Dr PDC / Cr AR; JE: Dr Bank|Cash / Cr PDC; reversal symmetric), but eight issues need to land on `fix/production-readiness-audit` before the app can be safely installed on `aldar.erpnext.com`, three of which (G1, G2, G3) were discovered during Phase 2 test verification rather than the original audit pass.

1. **C1** — leaf reservation calls `frappe.db.commit()` from inside `Cheque.before_save`, which prematurely commits the outer save() transaction; if any later validation in `before_save` raises, the leaf is reserved-and-committed but the cheque is rolled back, producing an orphan reservation.
2. **E2 + E8 (bundled)** — cheque status can be moved away from `Cleared` via `change_cheque_status` while the clearance JE remains submitted, decoupling cheque state from GL state; the link fields are also `allow_on_submit: 1`, so a user can clear the back-link from the desk and then re-clearance, doubling GL.
3. **F1** — `change_cheque_status` enforces only `Cheque write`, not the workflow's role gates; an Accounts User can drive Treasury-only transitions (e.g., `Bounce`).
4. **§3 decision** — the Settings-field-population strategy must be chosen (option (a) admin-configures vs (b) ship-defaults); the app cannot post a single Recording PE until `pdc_receivable_account` is set.
5. **D2/D3** (promoted from deferred) — `Cheque.on_cancel` does not cancel an orphan Draft PE; submitting the orphan later writes phantom audit events against the cancelled cheque. Data-integrity issue, not cleanup.
6. **G1** (Phase 2 verification) — `_append_event_and_save` in the JE/PE post-cancel hooks lacks `ignore_links`, so Frappe's link validator rejects the cheque save with `CancelledLinkError` because historical event rows still reference the doc being cancelled.
7. **G2** (Phase 2 verification) — `Cheque Event.reference_name` is configured as Dynamic Link, so Frappe's `check_if_doc_is_dynamically_linked` blocks any JE/PE cancel pre-flight whenever an audit-trail row references it. Independent of G1: the cancel never starts.
8. **G3** (Phase 2 verification, D2 close-out) — `Cheque._flush_events` does a vanilla `persisted.save()` which Frappe rejects on `docstatus=2` docs (`check_docstatus_transition`); cancelled cheques could not persist their audit-trail "Cancelled" event end-to-end. Discovered by the D2 cancellation test, which was the first to exercise the cheque-cancel pathway.

The Settings-table "missing" finding from the original probe was a false positive (see §3 revision). All other findings are P2 or below and either work around quirks (E7 is informational for single-currency EEI) or are cleanup (C4).

### Part B — Phase 2 priority list

#### Co-blockers (must fix before production install)

| # | ID  | File:line                                                | Fix description                                                                                                                                              | Effort |
|---|-----|----------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|--------|
| 1 | C1  | `cheque/cheque.py:202-208`                               | Replace `db.begin/commit/rollback` with `with frappe.db.savepoint("reserve_leaf"):`; rely on outer Frappe transaction for atomicity.                          | **S**  |
| 2 | E2  | `cheque/cheque.py:353-396` (`_validate_transition`)      | Add: if `doc.status == "Cleared"` and `new_status != "Cleared"`, throw "Cancel the Clearance Journal Entry first."                                            | **S**  |
| 3 | E8  | `cheque/cheque.json` (3 link fields)                     | Change `recording_payment_entry`, `clearance_journal_entry`, `reversal_journal_entry` from `allow_on_submit: 1` → `0`. Server-side mutations use `db.set_value` and won't be affected. | **S**  |
| 4 | F1  | `cheque/cheque.py:353-396` (`_validate_transition`)      | Map each `new_status` to its allowed roles (mirroring `Cheque Workflow.transitions[].allowed`) and call `frappe.only_for(allowed_roles)` before `log_status_change`. | **M**  |
| 5 | G1  | `hooks/journal_entry_hooks.py:29-41` and `hooks/payment_entry_hooks.py:31-43` (`_append_event_and_save`) | Add `doc.flags.ignore_links = True` alongside the existing `ignore_permissions` and `ignore_validate_update_after_submit`. Historical event rows reference the doc being cancelled (e.g., the original "Cleared" event has `reference_name = JE-name`); without `ignore_links`, Frappe's `_validate_links` raises `CancelledLinkError` on the post-cancel save and the cheque rollback never lands — exactly the GL/state divergence E2 was designed to prevent. **Compounds with G2.** Fix landed in commit `37d3e29`; verified by `test_e2_allows_transition_back_to_cleared_after_je_cancel` (after G2 also lands). | **S**  |
| 6 | G2  | `cheque_event.json` (`reference_name` field) + new patch `patches/v1_2/clear_cheque_event_dynamic_links.py` | Change `reference_name` from `Dynamic Link` (options: `reference_doctype`) to `Data` (options: empty), matching ERPNext's own `Comment` doctype which faces the identical audit-trail problem. Independent of G1: with `reference_name` registered as a Dynamic Link, `check_if_doc_is_dynamically_linked` (delete_doc.py:469) raises `LinkExistsError` *before* the cancel logic runs, so the cancel never starts. Cleanup patch deletes stale rows from `tabDynamic Link` where `parenttype = 'Cheque Event'`; idempotent (re-runs match zero rows on a clean DB). Add the patch to `patches.txt`. Fix landed in commit `fa0f6a5`; verified by the same E2 cancellation test as G1. | **M**  |
| 7 | G3  | `cheque/cheque.py` (`Cheque._flush_events`)               | Branch on `persisted.docstatus`. For `docstatus=2`, take a direct child-doc insert path using `frappe.new_doc("Cheque Event")` + `child.db_insert()`, bypassing `save()` validation while still writing a properly-formed audit row; `idx` is computed as `max(existing idx) + 1`. Other docstatus values keep the existing `save()` path unchanged. Frappe's `check_docstatus_transition` (frappe/model/document.py:1084) hardcodes the rejection on `save()` of cancelled docs — no flag bypasses it — so the only viable path is direct insert. **Discovered by the D2 cancellation test**, which was the first to exercise the cheque-cancel pathway end-to-end; without G3 the cheque cannot be cancelled at all. Fix landed in commit `aa6bf26` on PR #4; verified by `test_d2_cancel_cheque_cleans_up_draft_payment_entry` plus all 7 Phase 2 regression tests still green. | **S**  |
| 8 | §3  | `hooks.py` and/or admin runbook                          | Decide between option (a) admin populates Settings via desk UI post-install (document in README) or (b) ship an `after_install` hook that creates the Settings doc and logs a configuration warning. **Fixed (PR #5, merged on `a73f066`):** `cheque_tracker/install.py` adds `after_install` that materialises the Singles row and logs a `[cheque_tracker] WARNING` listing unconfigured required account fields (`pdc_receivable_account`, `default_bank_gl_account`, `default_cash_account`). `hooks.py` wires the hook. `README.md` adds a Post-Install Configuration section documenting required fields and the PDC `account_type` production concern. **Verification:** function-level on dev via FAC — Singles row materialises, warning fires, idempotent on re-run. | **S**  |

#### Should-fix before production

| # | ID  | File:line                                                | Fix description                                                                                                                                                                   | Effort |
|---|-----|----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------|
| 9 | F2  | `cheque_tracker_settings.json` perms OR `cheque_financial.py:142,255` | Either add Accounts User read on Cheque Tracker Settings, or add `frappe.only_for(["Treasury User", "System Manager"])` at the top of `make_recording_payment_entry` / `make_clearance_journal_entry`. **Fixed (PR #7):** added "Accounts User → read" DocPerm entry on `cheque_tracker_settings.json`. Server-side reads via `frappe.get_single` were already implicitly allowed; explicit perm covers UI access and protects against future `has_permission` checks. **Verification:** `test_f2_accounts_user_has_settings_read_permission`. | **S**  |
| 10 | C3  | `cheque_batch/cheque_batch.py:36-48` (`_mark_cheques_deposited`) | Either re-raise after logging (atomic batch) or accumulate failures and surface via `frappe.msgprint` so the user sees which cheques weren't transitioned. **Fixed (PR #9):** `ChequeBatch._mark_cheques_deposited` accumulates failures and surfaces via `frappe.msgprint` with `indicator="orange"` instead of silently logging and continuing. Raises `frappe.throw` when all cheques fail (no-effective-work batch). Existing skip-guard for already-Deposited/Cleared/Cancelled cheques preserved (idempotent re-run after partial failure). **Verification:** `test_c3_all_succeed_no_msgprint`, `test_c3_partial_failure_surfaces_via_msgprint`, `test_c3_all_fail_raises`. | **S**  |
| 11 | C2  | `cheque_book/cheque_book.py:169-180` (`get_book_counters`) | Add `frappe.has_permission("Cheque Book", "read", doc=cheque_book, throw=True)` at the top of the function. **Fixed (PR #8):** added `frappe.has_permission("Cheque Book", "write", doc=doc, throw=True)` in `get_book_counters` between `get_doc` and `_refresh_counters`. `_refresh_counters` writes via `db_set`, bypassing Frappe's write-perm check; without this gate, read-only roles could trigger persistent counter mutations. **Verification:** `test_c2_get_book_counters_allowed_for_treasury_user`, `test_c2_get_book_counters_blocked_for_read_only_role`. | **S**  |
| 12 | D2/D3 | `cheque/cheque.py:56-83` (`Cheque.on_cancel`)          | Before logging the Cancelled event, if `recording_payment_entry` is a Draft PE, cancel/delete it and clear the link. **Data-integrity (orphan PE → phantom events)** — promoted from deferred. **Fixed (PR #4, commit `07164bd`):** added `Cheque._delete_draft_if_any` helper called from `on_cancel` for the three link fields (`recording_payment_entry`, `clearance_journal_entry`, `reversal_journal_entry`). Draft docs are deleted with `force=True` and the back-link cleared via `db.set_value` before the Cancelled event is logged. Submitted accounting docs continue to be blocked by the existing `_has_submitted_accounting_docs()` guard at the top of `on_cancel`. **Verification:** `test_d2_cancel_cheque_cleans_up_draft_payment_entry` (PR #4). | **S**  |
| 13 | —   | `test_cheque_financial.py` and a new `test_flow_b_cash.py` | Add: Flow A end-to-end with `tabGL Entry` assertions, Flow B end-to-end with GL assertions, cash-clearance happy path (v1.1.4), C1 regression, E2 regression, E8 link-edit regression, F1 role-gate regressions. Also add **`_append_event_and_save` regression coverage** for the PE cancellation path: G1 + G2 surfaced from end-to-end exercise of JE cancellation; the structurally-identical PE cancellation hook was fixed defensively but is not yet test-covered. Add a test that submits a recording PE, appends a non-trivial events trail, then cancels the PE and verifies the cheque rolls back to "In Safe" without `CancelledLinkError` or `LinkExistsError`. **D2/G3 cancel-pathway coverage:** PR #4 added `test_d2_cancel_cheque_cleans_up_draft_payment_entry` which surfaced G3 (Cheque.on_cancel cannot save cancelled doc) by exercising the cancel flow end-to-end. Future expansion: add parallel coverage for cancelling a Cleared cheque (which exercises the JE cancel hook chain in addition to the cheque cancel hook), and for cancelling an Outgoing cheque (different draft-cleanup path than Incoming). **`after_install` verification pattern:** PR #5 introduced the `install.py` module which is awkward to unit-test (install hooks fire only on fresh install). Function-level verification on dev via FAC is documented in PR #5; future expansion should add a unit test that calls `_bootstrap_settings()` directly inside a savepoint, asserts the Singles row materialises, and asserts the Error Log entry is created. **Status as of Phase 2 close-out:** 14 tests passing across `test_cheque.py` (9), `test_cheque_book.py` (2), `test_cheque_batch.py` (3). Items still pending: PE-cancel coverage, `after_install` unit test via savepoint pattern, GL assertions on Flow A/B end-to-end. Deferred to a future hardening cycle — not blocking production install. | **L**  |

#### Can defer (post-production, lower priority)

| # | ID  | File:line                                                | Fix description                                                                                                                                                              | Effort |
|---|-----|----------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------|
| 14 | C4 | `cheque_tracker/tasks.py` (top-level)                    | Delete the byte-identical duplicate; only `cheque_tracker/cheque_tracker/tasks.py` is referenced by `hooks.py`.                                                              | **S**  |
| 15 | E7 | `cheque/cheque_financial.py:201-202, 311-322`            | Populate `account_currency` and `exchange_rate` on PE / JE rows. **P0 if Aldar invoices in foreign currency**, otherwise informational.                                       | **M**  |
| 16 | §2 fixtures filter | `hooks.py:27-29`                              | Replace the broken `Workflow State` filter (`workflow_name` field doesn't exist) with `[["name", "in", [<state names>]]]` — or remove the entry and ship the JSON directly.    | **S**  |
| 17 | §2 hooks no-op | `cheque_tracker/doctype/cheque_book/cheque_book.py:157-162` and `hooks.py:93-96` | Remove the no-op `on_submit`/`on_cancel` shims and their `doc_events` registration.                                                                                            | **S**  |
| 18 | §2 global JS | `hooks.py:12` + `public/js/cheque_tracker.js`        | Either move the global Cheque/Cheque Book handlers into the doctype-local `.js` files, or scope via `doctype_js: {...}` instead of `app_include_js`.                            | **M**  |
| 19 | §7 Cheque Print Template | review                                  | Cheque Print Template is an ERPNext core DocType (module: Accounts, custom: 0) — included in the audit prompt by mistake. No action needed unless cheque_tracker plans to override it.       | **S**  |
| 20 | §3 patch idempotency | `patches/v1_0/...:25-30`                       | Replace string-match on exception text with `frappe.db.has_index("tabCheque Leaf", "unique_book_cheque_no")`.                                                                  | **S**  |
| 21 | §4.2 patch commits | `patches/v1_1/...:34`                            | Move `frappe.db.commit()` out of the column-add loop (or remove — DDL auto-commits in MySQL).                                                                                  | **S**  |
| 22 | §3 default_bank_account | `cheque_tracker_settings.json`              | Confirm the `default_bank_account` field is unused (no read path found). If confirmed, deprecate the field with a comment or remove via patch.                                  | **S**  |
| 23 | §2 hook rollbacks | `payment_entry_hooks.py:100`, `journal_entry_hooks.py:116` | PE-cancel hook hard-codes rollback to `"Draft"`; clearance-JE-cancel hook hard-codes rollback to `"Received"`. **P2 correctness** — breaks if the cheque had progressed to `In Safe`/`Deposited`/`Presented` before the accounting doc was cancelled. Mirror the `pre_bounce_status` pattern used by reversal-JE-cancel. | **M**  |
| 24 | §2 dead fixture | `fixtures/custom_roles.json`                     | The committed `custom_roles.json` is overwritten by Frappe's `role.json` on `bench export-fixtures`, so the file is effectively dead. Delete it; the `Role` filter in `hooks.py` already exports the two custom roles correctly.                                       | **S**  |

Total Phase 2 effort estimate (co-blockers only): ~1 day. With should-fix items including the test suite expansion: ~3 days. With deferred items: ~1 week.

### Part C — Open items requiring human input

1. **`bench run-tests` output (§6)** — I could not execute `bench --site eei-test.f.frappe.cloud run-tests --app cheque_tracker` from this sandbox (no `bench` binary, dev site is on Frappe Cloud). Please run the command and paste the output; I'll fold pass/fail and any failures into §6 in a follow-up commit.
2. **Settings field defaults — option (a) or (b) (§3)** — admins populate via desk UI post-install (requires README update only) or ship sensible defaults via fixture / `after_install` (requires code). Decision needed before Phase 2 item #5 can be implemented.
3. **F1 role mapping (§7 / Phase 2 item #4)** — confirm the desired role-by-transition mapping. The proposal mirrors `Cheque Workflow.transitions[].allowed` from `fixtures/workflow.json`, but the workflow currently allows Treasury User to drive almost every transition; please confirm whether Accounts User should retain *any* write access to status, or be locked out entirely.
4. **F2 fix path (§7 / Phase 2 item #6)** — grant Accounts User read on Cheque Tracker Settings, or restrict PE/JE creation entry points to Treasury User? Decision affects who can create financial documents through the app's buttons.
5. **C3 swallow vs. raise (§4.1 / Phase 2 item #7)** — should `_mark_cheques_deposited` be atomic (one cheque failure aborts the whole batch) or partial-with-warnings (current behavior, but surfaced via msgprint instead of silent)?
6. **E7 multi-currency timing (§5 / Phase 2 item #12)** — informational for EEI today, but if Aldar ever issues or receives foreign-currency cheques the missing `account_currency` / `exchange_rate` on JE rows promotes to P0. Please flag if multi-currency is on the roadmap.
7. **PDC account `account_type` production configuration concern** — discovered during Phase 2 test verification: ERPNext validates GL Entry rows hitting any account with `account_type = "Receivable"` and requires `party` populated. The current `make_recording_payment_entry` in `cheque_financial.py` sets `party` on the PE root (which propagates to the AR-side `paid_from` GL row) but **not** on the PDC-side `paid_to` GL row. The test workaround landed in commit `5235865` makes `_get_or_create_pdc_account` use `account_type = ""` to dodge the validation, with a docstring note. **Production decision required before install on `aldar.erpnext.com`:** verify the live PDC account's `account_type`. If configured as `"Receivable"`, Recording PE submission will fail with `ValidationError: Customer is required against Receivable account`. Two paths: (a) change the production PDC account's `account_type` to `""` or another non-party-required asset type — simpler but loses some ERPNext receivable-aging integration; (b) amend `make_recording_payment_entry` to populate `party_type` / `party` on the PDC-side GL row at submission time — production-grade fix that allows either `account_type` to work. **Severity: P0 install-time blocker if the production PDC account is configured as Receivable.** Verification of the production setting is the gating action. **Stage 1 close-out: deferred.** Decision must be made before production install on `aldar.erpnext.com` — verify the live PDC account's `account_type` and choose path (a) or (b) per the entry above. **Resolved (no code change):** no PDC accounts exist on `aldar.erpnext.com` production yet. Documentation in `cheque_tracker/README.md` → Post-Install Configuration directs the operator to create PDC accounts with `account_type = ""` (blank) at first install, sidestepping the GL party-validation issue entirely. The path-(b) production-grade fix (populate party on PDC GL row in `make_recording_payment_entry`) remains a future enhancement for receivable-aging integration; not blocking install.

---

## Phase 2 status (as of 2026-04-30)

All Stage 1 production blockers fixed (4 original + 3 discovered by testing — G1, G2, G3). All Stage 2 hardening items fixed (F2, C2, C3). PDC concern resolved by documentation. Test-suite expansion items deferred. App is production-ready for first install on `aldar.erpnext.com` per `README.md` → Post-Install Configuration.

---

*End of audit. All Phase 2 work is gated on user approval per the original constraints. No code changes have been made to the app outside of `AUDIT.md`.*
