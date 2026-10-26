# DEPLOY NOTES — eei-test rollout

Status as of the v1.2.0 merge. `aldar` is out of scope for this document and has
not been touched.

## How this app reaches eei-test

Frappe Cloud builds `eei-test.f.frappe.cloud` from the GitHub repo linked to its
bench group. **It picks up `main` on its own** — the v1.1.6 merge deployed
without anyone triggering it. There is no Frappe Cloud CLI, credential or press
API token on the build machine, and the app's only git remote is GitHub, so the
deploy itself cannot be driven from here.

## Where things stand

| | Version |
|---|---|
| `main` | **1.2.0** |
| eei-test | **1.1.6** — v1.2.0 merged later and had not landed at the time of writing |
| PR #19 (v1.3.0) | open, targeting `main`, MERGEABLE/CLEAN |

### v1.1.6 — verified live on eei-test

- `cheque_tracker.patches.v3_2.repair_dashboard_fixtures` ran, `skipped = 0`.
- `Overdue Incoming` / `Overdue Outgoing` now carry
  `dynamic_filters_json = [["Cheque","due_date","<","frappe.datetime.get_today()"]]`
  and the literal `"Today"` is gone from `filters_json`. **These two cards
  previously always read 0.**
- `Active Outgoing` and `Pending Payable` include `Handed Over`.
- `Bounced Incoming` carries `docstatus = 1`.

### v1.2.0 — remaining action

Confirm the deploy in the Frappe Cloud dashboard (bench group → Deploys). If it
has not been picked up, trigger **Update** there. Then verify:

```sql
SELECT app_version FROM `tabInstalled Application` WHERE app_name='cheque_tracker';
-- expect 1.2.0
SELECT patch, skipped FROM `tabPatch Log`
WHERE patch='cheque_tracker.patches.v3_3.split_status_vocabulary';
-- expect one row, skipped = 0
```

**eei-test currently holds zero Cheque records**, so the v3_3 status migration is
a no-op there and staging will not exercise it. The migration only matters on
`aldar`.

## What to watch for on `aldar`, when it gets there

1. **The clearance gate is the behaviour change most likely to surprise someone.**
   A cheque with no linked Payment Entry or Journal Entry can no longer be
   cleared. `CHQ-2026-00001` (outgoing, electricity company, no PE link) is
   exactly that case — it will refuse to clear until someone links the Payment
   Entry or a System Manager ticks **Clear Without Accounting Document** and
   gives a reason. That is deliberate; see `DECISIONS.md` D2.
2. **The v3_3 migration rewrites outgoing cheques sitting in `Received` to
   `Issued`**, and their timeline rows with them. `CHQ-2026-00001` is in
   `Handed Over`, so it is untouched; anything in `Received` will move.
3. **The tracker no longer posts a clearance Journal Entry.** The linked Payment
   Entry is the only posting document. Cheques cleared under v1.1.x keep their
   `clearance_je` link and can still be unwound.
4. **Arabic**: the app no longer overrides any string frappe/erpnext already
   translate, so a few poor core translations become visible again (D10).

## Post-deploy smoke (5 minutes)

1. Open the **Cheque Tracker** workspace — cards render, Overdue cards are not
   stuck at 0.
2. `bench --site eei-test.f.frappe.cloud clear-cache` if the desk looks stale.
3. Create a draft Payment Entry with `mode_of_payment = Cheque` → a Draft Cheque
   should be created once, with a link message.
4. Submit an outgoing cheque → status should read **Issued**, not Received.
5. Deposit and Clear an incoming cheque that has a Payment Entry → the PE should
   submit (or raise a ToDo if the acting user cannot approve it).
6. After v1.3.0: print one **Cheque Receipt Voucher** and confirm the Arabic
   amount-in-words renders. `wkhtmltopdf` was absent on the build machine, so PDF
   generation itself was only proven at HTML level.

## v1.3.1 leaf / back-dating fixes — what to check on the production site

The production site already reports `cheque_tracker` 1.3.1; this ships under the
same number. It carries one data patch, `cheque_tracker.patches.v3_4.fix_chq_2026_00004_leaf`,
written against the live records as read on 2026-10-26.

1. **Migrate twice.** The first migrate prints
   `[cheque_tracker] CHQ-2026-00004 leaf correction: corrected`; the second runs
   no patch at all (Patch Log). Run `verify_fixtures` after each:

   ```
   bench --site <site> migrate
   bench --site <site> execute cheque_tracker.tests.verify_fixtures.run
   bench --site <site> migrate
   bench --site <site> execute cheque_tracker.tests.verify_fixtures.run
   ```

   No fixture file changed in this release, so the Property Setter that keeps
   Cheque No read-only for outgoing cheques and the Cheque Workflow re-import as
   they are.

2. **The correction landed and held:**

   ```sql
   SELECT name, cheque_no, cheque_leaf, status FROM `tabCheque` WHERE name='CHQ-2026-00004';
   -- 528201382 | e75gf78oeh | Returned
   SELECT name, cheque_no, leaf_status, cheque, issued_on FROM `tabCheque Leaf`
   WHERE name IN ('e75gf78oeh','e75ge51640');
   -- e75gf78oeh | 528201382 | Cancelled | CHQ-2026-00004 | 2026-10-18
   -- e75ge51640 | 528201380 | Unused    | NULL           | NULL
   SELECT unused_leaves, issued_leaves, cancelled_leaves FROM `tabCheque Book`
   WHERE name='CHQBK-2026-00001';
   -- 12 | 1 | 1
   ```

   If the first migrate prints `mismatch` instead, someone changed one of these
   records after 2026-10-26 and the patch wrote nothing. Fix it by hand with
   **Reassign Leaf** on CHQ-2026-00004.

3. **Smoke:** create an outgoing draft, pick a leaf that is *not* the next
   Unused one, then save and Issue: the cheque keeps that leaf and number. Then
   set **Effective Date** to yesterday, Update, and Hand Over: the Handed Over
   timeline row shows yesterday.
