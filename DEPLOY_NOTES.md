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
