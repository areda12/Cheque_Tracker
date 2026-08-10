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
