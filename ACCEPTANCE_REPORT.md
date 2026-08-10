# ACCEPTANCE REPORT

Self-run acceptance evidence for each release, per BUILD_INSTRUCTIONS.md §6.2.

---

## Phase 1 — v1.1.6 (bug release)

**Branch:** `v1.1.6-bugfix` (PR 1, off `origin/main`)
**Date:** 2026-08-10
**Result:** all §3.3 acceptance criteria pass.

### Environment

| Component | Version / value |
|---|---|
| Frappe | 16.29.0 (`version-16`) |
| ERPNext | `version-16` |
| payments | `version-16` (see *Deviations*) |
| cheque_tracker | 1.1.6, from `origin/main` @ `e507bf2` (1.1.5) |
| Site | `cheque.localhost` on `~/frappe-bench-eei` |
| Python / MariaDB | 3.14.4 / MariaDB (local) |
| `mute_emails` | `1` — set before the app was installed |
| Slack | no Slack or webhook code exists in the app at 1.1.5; nothing to neutralise (grep for `slack|webhook` returns zero hits). §4.6 will introduce it guarded. |

Seeded with `bench --site cheque.localhost execute cheque_tracker.tests.seed_local.run`
(idempotent — verified by three consecutive runs, the 2nd and 3rd creating nothing).

### Version numbering

`BUILD_INSTRUCTIONS.md` describes the baseline as v1.1.4 and this release as v1.1.5.
`origin/main` is already `__version__ = "1.1.5"` (the Replace-workflow changeset, PR #16,
merged 2026-05-11). Ahmed confirmed the baseline is `origin/main` = what production runs,
and that the three releases renumber to **1.1.6 / 1.2.0 / 1.3.0**. Appendix A's behavioural
facts were verified against the live 1.1.5 site and are treated as authoritative.

### §3.3 acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Event logging via workflow — exactly one event | **PASS** | `test_workflow_transition_logs_exactly_one_event` |
| 2 | Event logging via `change_cheque_status` — exactly one event | **PASS** | `test_ui_transition_logs_exactly_one_event` |
| 3 | Two transitions produce two events (no swallowing) | **PASS** | `test_two_transitions_produce_two_events` |
| 4 | Outgoing submit logs the transition, not a 2nd `Created` | **PASS** | `test_outgoing_submit_logs_received_not_a_second_created` |
| 5 | Holder auto-clear on Handed Over (UI path) | **PASS** | `test_hand_over_via_ui_clears_current_holder` |
| 6 | Holder auto-clear on Handed Over (workflow path) | **PASS** | `test_hand_over_via_workflow_clears_current_holder` |
| 7 | `external_holder` round-trip | **PASS** | `test_external_holder_round_trip`, `test_external_holder_settable_after_submit` |
| 8 | Incoming does not default `current_holder` | **PASS** | `test_incoming_does_not_default_current_holder` |
| 9 | Outgoing still defaults `current_holder` | **PASS** | `test_outgoing_defaults_current_holder_to_creator` |
| 10 | Book counters after reserve / issue / void / cancel / manual edit | **PASS** | `TestChequeBookCounters` (7 tests, all read the stored column, never `get_book_counters`) |
| 11 | **Fixture idempotency: `bench migrate` ×2, assert 10 cards + 2 charts after each** | **PASS** | see below |
| 12 | Patch test: seed OLD broken values → run patch → assert corrected | **PASS** | `TestRepairPatch` (3 tests, incl. the set-once recreate and idempotency) |

### Migrate-idempotency proof (§3.3, the headline regression)

```
migrate1 exit=0
[verify_fixtures] OK — 10 Number Cards and 2 Dashboard Charts match §3.1,
                       in the database and in the shipped fixtures.
suite1  exit=0   Ran 63 tests   OK
migrate2 exit=0
[verify_fixtures] OK — 10 Number Cards and 2 Dashboard Charts match §3.1,
                       in the database and in the shipped fixtures.
suite2  exit=0   Ran 63 tests   OK
```

`verify_fixtures` asserts the **database** and the **shipped JSON** against the §3.1
values independently. Checking the database against the fixture file alone would pass
happily with a wrong fixture file — and the next migrate force-imports that file.

The patch's own output, from the migrate that first applied it:

```
Executing cheque_tracker.patches.v3_2.repair_dashboard_fixtures in cheque.localhost
[cheque_tracker] repaired Number Card Active Outgoing: ['filters_json']
[cheque_tracker] repaired Number Card Bounced Incoming: ['filters_json']
[cheque_tracker] repaired Number Card Bounced Outgoing: ['filters_json']
[cheque_tracker] repaired Number Card Due This Week Outgoing: ['filters_json']
[cheque_tracker] repaired Number Card Overdue Incoming: ['dynamic_filters_json', 'filters_json']
[cheque_tracker] repaired Number Card Overdue Outgoing: ['dynamic_filters_json', 'filters_json']
[cheque_tracker] repaired Number Card Pending Payable: ['filters_json']
[cheque_tracker] repaired Dashboard Chart Cheque Status Distribution: ['aggregate_function_based_on', 'group_by_type']
[cheque_tracker] recreated Dashboard Chart Cheques Over Time (set-once field changed)
```

### Test suite

| Run | Tests | Failures | Errors | Skipped |
|---|---|---|---|---|
| Baseline (`origin/main`, fresh site) | 34 | 0 | 16 | 12 |
| Baseline after seeding the env | 34 | 1 | 2 | 1 |
| **v1.1.6 final** | **63** | **0** | **0** | **0** |

The 16 baseline errors were all one cause — `No Bank Account found for company _Test Company`.
The 12 skips were `skipTest` guards that fired whenever the environment lookup came up
empty, so the suite reported no failures while asserting almost nothing. Both are fixed by
pinning the environment, not by relaxing anything: no assertion was weakened, skipped or
deleted, and the guards were replaced with hard setup.

Three defects surfaced only once the environment was real, and are fixed in this release:

1. `submit()` → `cancel()` raised `TimestampMismatchError` (stale in-memory doc).
2. `test_incoming_cheque_no_book_required` omitted the required `drawee_bank` (test bug).
3. The concurrency test's threads shared the parent's DB connection and all died with
   "object is not bound", so `SELECT … FOR UPDATE` was never exercised at all.

### Sensitivity check

Not required by BUILD_INSTRUCTIONS, run anyway — a green suite is only worth what it
catches. Each bug was introduced alone, the suite run, then reverted:

| Seeded bug | Expected | Observed |
|---|---|---|
| `Active Outgoing` status list reverted to the broken `Deposited` variant | red | **red** — 3 failures, naming the field and both values |
| `on_submit` restored to logging a second `Created` for Outgoing | red | **red** — 1 failure |

Full suite re-run green after reverting both.

### Live behaviour confirmed on the seeded site

The seed reproduced all three §3.2 defects on the untouched 1.1.5 baseline before any fix:
the outgoing cheque sat in status `Received`, `current_holder` was `Administrator` on both
cheques including the incoming one, and CHQ-2026-00002 carried two `Created` events and no
`Received`.

### Deviations and known limitations

1. **The `payments` app was installed on the test site.** ERPNext's `Payment Gateway Account`
   links to `Payment Gateway`, which lives in the separate `payments` app. Frappe's
   test-record dependency walk follows that link and died with
   `DocType Payment Gateway not found`, so `bench run-tests --app cheque_tracker` could not
   start at all on a fresh ERPNext-only site. Installing `payments` (`version-16`) resolves
   it. This is a **local test-environment dependency only** — no app code references it, and
   nothing about it ships in the release.
2. **`bench migrate` still deletes and recreates the Workspace and Desktop Icon on every
   run.** Core `remove_orphan_entities()` deletes them because the app has no
   `cheque_tracker/workspace/` directory and its `desktop_icon/` directory is empty; the
   `after_migrate` hook then re-imports them. It is load-bearing, not belt-and-braces, and
   any UI edit to the Workspace is destroyed on each migrate. Out of scope here — §5.4
   rebuilds the Workspace fixture and is the right place to fix the cause.
3. **The tests still use the deprecated `FrappeTestCase`.** v16 routes that class through a
   compatibility path that force-preloads test records for the whole dependency graph (the
   `payments` problem above). Migrating to `IntegrationTestCase` would avoid it, but changing
   the base class of every test in a bug-fix release is the wrong risk trade; noted for a
   later release.
4. **`workflow.json` references a `Submit` Workflow Action Master that does not exist.**
   It survives only because fixture import sets `ignore_links`; any UI save of the Workflow
   document will fail link validation. Untouched here because §4.1 rebuilds the workflow.
5. **Two `Cheque Event` types are unreachable.** `event_type` offers `Presented`, which no
   v1.1.x transition produces (the v2_1 patch removed that state). §4.1 revisits the
   vocabulary.

### Migration notes (v1.1.6)

- Patches that run: **`cheque_tracker.patches.v3_2.repair_dashboard_fixtures`** (one patch).
  Runs `pre_model_sync` like every other patch in this app — `patches.txt` carries no
  section headers.
- Expected duration: sub-second. It touches at most 10 Number Card rows and 2 Dashboard
  Chart rows, and deletes/reinserts one chart.
- Manual steps required post-deploy: **none.**
- Rollback: the patch is forward-only, but it writes no user data — reverting the app to
  1.1.5 and re-running migrate restores the previous (broken) fixture values from the
  1.1.5 JSON.

---

## Phase 2 — v1.2.0 (state model + accounting)

**Branch:** `v1.2.0-state-accounting` (PR 2, stacked on PR 1)
**Date:** 2026-08-10
**Result:** all §4.8 acceptance criteria pass.

### Environment

Same site as Phase 1 (`cheque.localhost`, `mute_emails = 1`), plus an **active
Payment Entry approval workflow** created by `before_tests` and mirroring
production's "Approval Pending by Accounting Manager" gate, with two users: one
holding `Accounts Manager` and one deliberately without it. §4.8 requires the PE
integration to be exercised with a gate in play — it is the only way the degraded
ToDo path is reachable at all.

### §4.8 acceptance criteria

**Scenario matrix** — `bench --site cheque.localhost execute cheque_tracker.tests.e2e.run_all`

```
incoming: deposit → clear                        PASS
incoming: cash clear                             PASS
incoming: cash cheque cannot deposit             PASS
incoming: endorse                                PASS
incoming: endorse requires counterparty          PASS
incoming: deposit → bounce → re-deposit → clear  PASS
incoming: bounce requires a reason               PASS
incoming: bounce → replace                       PASS
outgoing: issue → hand over → present → clear    PASS
outgoing: bounce                                 PASS
outgoing: cannot use incoming statuses           PASS
===========================================================
11/11 passed
```

Every scenario moves the cheque with `apply_workflow` — the same call the desk
makes — never by writing `status` directly. The matrix runs both standalone
(against committed data) and inside the suite (rolled back), so it is proven in
both modes.

**Payment Entry integration** (`TestPaymentEntryIntegration`, 10 tests)

| Criterion | Result | Test |
|---|---|---|
| Auto-create fires exactly once | **PASS** | `test_auto_create_fires_once` |
| **Clear is refused with no accounting document** | **PASS** | `test_clear_is_blocked_without_an_accounting_document` |
| **Cash Clear refused likewise** | **PASS** | `test_cash_clear_is_blocked_without_an_accounting_document` |
| **The UI endpoint is gated too, not just the form** | **PASS** | `test_ui_path_is_blocked_too` |
| **System Manager override works and is logged with who** | **PASS** | `test_system_manager_can_override_and_it_is_logged` |
| **Override needs a reason** | **PASS** | `test_override_requires_a_reason` |
| **Override refused to a non-System-Manager** | **PASS** | `test_override_is_refused_to_a_non_system_manager` |
| **A Journal Entry also satisfies the gate** | **PASS** | `test_a_journal_entry_also_satisfies_the_gate` |
| **Normal PE-linked path unaffected** | **PASS** | `test_normal_pe_linked_path_still_clears` |
| Direction + fields mapped from the PE | **PASS** | `test_auto_create_maps_direction_and_fields` |
| Outgoing PE does not auto-create (needs a book leaf) | **PASS** | `test_outgoing_payment_maps_to_outgoing_cheque` |
| Draft-PE edits sync onto the draft cheque | **PASS** | `test_draft_pe_edits_sync_onto_the_draft_cheque` |
| Amount mismatch throws | **PASS** | `test_amount_mismatch_throws_on_cheque_save` |
| Clear submits the draft PE when permitted | **PASS** | `test_clear_submits_the_draft_pe_when_permitted` |
| Clear degrades to a ToDo when not permitted | **PASS** | `test_clear_degrades_to_todo_when_user_cannot_approve` |
| Already-submitted PE only gets `clearance_date` | **PASS** | `test_submitted_pe_only_gets_clearance_date` |
| Clearing with no PE posts nothing, and says so | **PASS** | `test_clear_with_no_payment_entry_posts_nothing` |
| **No clearance Journal Entry is created** | **PASS** | `test_no_clearance_journal_entry_is_created` |

The last one is the double-posting guard: it counts Journal Entries before and
after a Clear and asserts the count is unchanged. See `DECISIONS.md` D2.

**Migration** (`TestStatusVocabularyMigration`, 3 tests) — outgoing `Received` →
`Issued` including timeline rows; incoming untouched; idempotent.

**Reminders** (§4.6, 13 tests) — due window, overdue/upcoming split, all four
closed statuses excluded, Bounced still reminded (money still owed), same-day
idempotency proven both in-transaction and cross-process, recipient parsing,
Notification Log row, and the `developer_mode` Slack guard.

**Translations** (§4.7, 10 tests) — **201 app-unique entries**. The app
translates only strings frappe/erpnext do not: the namespace is flat, so an
app-level entry rewrites that string for every app on the site. 39 shadowing
entries were removed. `tests/verify_translations.py` enforces the rule against
the 16,069 msgids in the two core catalogues, in the suite and as
`bench execute cheque_tracker.tests.verify_translations.run`. The completeness
checks now assert coverage across both catalogues, so a new untranslated status
still turns the suite red.

**migrate ×2**

```
migrate1 exit=0  →  verify_fixtures OK   verify_translations OK
suite1  exit=0   Ran 118 tests   OK
migrate2 exit=0  →  verify_fixtures OK   verify_translations OK
suite2  exit=0   Ran 118 tests   OK
scenario matrix: 11/11 passed
```

### Test suite

| Release | Tests | Failures | Errors | Skipped |
|---|---|---|---|---|
| Baseline (`origin/main`) | 34 | 0 | 16 | 12 |
| v1.1.6 | 63 | 0 | 0 | 0 |
| **v1.2.0** | **118** | **0** | **0** | **0** |

### Sensitivity check

Run on the reminder digest (the newest, least-exercised code), one seeded bug at
a time, each reverted:

| Seeded bug | Observed |
|---|---|
| Idempotency guard disabled | **red** — `test_digest_is_sent_once_per_day` |
| Closed-status filter narrowed to `Cancelled` only | **red** — `test_closed_statuses_are_excluded` |
| Due-date bound changed to exclude overdue | **red** — 5 tests |
| Clearance gate disabled | **red** — 3 tests |
| A core-shadowing translation re-added | **red** — 1 test |

### Deviations and known limitations

1. **The tracker no longer posts to the general ledger.** The single most
   important thing to review in this release. `DECISIONS.md` D2 has the full
   reasoning; the short version is that keeping the v1.1.x clearance JE alongside
   §4.5's submitted Payment Entry would double-post every collection.
2. **A cheque with no accounting document can no longer be cleared** — refused on
   both transition paths, with a logged System Manager override. Production
   CHQ-2026-00001 (outgoing, no PE link) is exactly the case this catches: it
   will refuse to clear until someone links the Payment Entry or overrides
   deliberately. Worth knowing before the first eei-test soak.
3. **The duplicate guard needs `drawee_bank`** to fire — D3.
4. **Some poor core Arabic is now visible again** — the app no longer shadows
   frappe/erpnext at all (D10), so core's `Draft` → "مشروع" ("project"),
   `Due Date` → "بسبب تاريخ" and `Amount` → "كمية" ("quantity") render as core has
   them. Fixing those belongs upstream or in a site-level Translation record, not
   in an app override.
5. **`auto_update_cheque_statuses` still only logs overdue *Deposited* cheques**,
   so outgoing overdue cheques are not logged by it. Pre-existing, and superseded
   in practice by the §4.6 digest, which covers both directions. Left alone to
   keep this release's diff to its scope.
6. The workspace still gets deleted and recreated on every migrate (Phase 1
   limitation 2). §5.4 fixes the cause.

### Migration notes (v1.2.0)

- Patches that run: **`v3_3.split_status_vocabulary`** (one new patch).
- Expected duration: seconds. It rewrites `status` on outgoing cheques sitting in
  `Received`, rewrites their `Cheque Event` rows, and re-applies the dashboard
  fixtures. Proportional to the number of outgoing cheques — two on production.
- Manual steps required post-deploy: **none**. Optionally set `reminder_days` and
  `notify_emails` in Cheque Tracker Settings to turn the daily digest on; with
  `notify_emails` blank the digest simply does not send.
- **Review before deploying:** the GL change in D2. Everything else is additive.
