# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""
Scheduled tasks for Cheque Tracker.
Registered in hooks.py under scheduler_events.
"""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import (
    add_days,
    cint,
    escape_html,
    fmt_money,
    format_date,
    getdate,
    now,
    split_emails,
    today,
)
from frappe.utils.user import get_users_with_role


def auto_update_cheque_statuses():
    """
    Daily job:
    1. Log a warning for every Deposited cheque whose due_date has passed.
    2. Refresh leaf counters on Active Cheque Books.

    Does NOT auto-transition statuses – that requires human confirmation.
    """
    logger = frappe.logger("cheque_tracker", allow_site=True)

    overdue = frappe.get_all(
        "Cheque",
        filters={
            "status": ["in", ["Deposited"]],
            "due_date": ["<", today()],
            "docstatus": 1,
        },
        fields=["name", "due_date", "status", "party", "amount"],
    )
    for row in overdue:
        logger.warning(
            "[ChequeTracker] OVERDUE  %s | party=%s | amount=%s | "
            "status=%s | due=%s",
            row.name, row.party, row.amount, row.status, row.due_date,
        )

    # Refresh counters
    active_books = frappe.get_all(
        "Cheque Book",
        filters={"status": "Active", "docstatus": 1},
        pluck="name",
    )
    for book_name in active_books:
        try:
            book = frappe.get_doc("Cheque Book", book_name)
            book._refresh_counters()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"ChequeTracker: counter refresh failed for {book_name}",
            )


# ---------------------------------------------------------------------- #
#  §4.6 — daily reminder digest                                          #
# ---------------------------------------------------------------------- #

# Statuses at which a cheque is finished with and nobody needs chasing about
# it. "Bounced" is deliberately NOT here: a bounced cheque is money we still
# have to recover, so it keeps appearing until it is replaced or returned.
DIGEST_CLOSED_STATUSES = ("Cleared", "Cancelled", "Replaced", "Returned")

# Mirrors the field default in cheque_tracker_settings.json. Needed because a
# site that already had the Settings row before the field was added reads it
# back as NULL, not as 3.
DEFAULT_REMINDER_DAYS = 3

# `parent` bucket in tabDefaultValue that holds the one-per-day claim tokens.
DIGEST_CLAIM_PARENT = "__cheque_tracker_digest"

# An admin must create a Slack Webhook URL under exactly this name before
# anything reaches Slack. Falling back to "whichever webhook exists" would push
# cheque amounts and party names into some unrelated channel, so absence of a
# webhook by this name means silence, not a guess.
DIGEST_SLACK_WEBHOOK = "Cheque Tracker"


def get_reminder_settings():
    """Return (reminder_days, recipients) from Cheque Tracker Settings.

    Read uncached: a scheduler worker is long-lived, and an admin who widens
    the window or fixes a typo in the recipient list should not have to wait
    for a cache eviction before the next digest honours it.
    """
    days = cint(
        frappe.db.get_single_value("Cheque Tracker Settings", "reminder_days", cache=False)
    )
    if days <= 0:
        # An unset field reads as 0, and 0 would silently shrink the digest to
        # "overdue only" — far more likely a blank than a deliberate choice.
        days = DEFAULT_REMINDER_DAYS

    raw = frappe.db.get_single_value("Cheque Tracker Settings", "notify_emails", cache=False)
    return days, split_emails(raw) if raw else []


def get_cheques_for_reminder(as_of=None, reminder_days=None):
    """Return (overdue, upcoming) cheque rows that belong in the digest.

    One `due_date <= as_of + reminder_days` bound already covers both halves —
    every overdue cheque is trivially inside it — so the database does a single
    range scan and the split into two buckets is presentation only.
    """
    as_of = getdate(as_of or today())
    if reminder_days is None:
        reminder_days = get_reminder_settings()[0]

    rows = frappe.get_all(
        "Cheque",
        filters={
            "docstatus": 1,
            "status": ["not in", DIGEST_CLOSED_STATUSES],
            "due_date": ["<=", add_days(as_of, cint(reminder_days))],
        },
        fields=[
            "name", "cheque_type", "status", "cheque_no", "party_type",
            "party", "amount", "currency", "due_date", "company",
        ],
        order_by="due_date asc, name asc",
    )

    overdue = [row for row in rows if getdate(row.due_date) < as_of]
    upcoming = [row for row in rows if getdate(row.due_date) >= as_of]
    return overdue, upcoming


def _claim_digest_day(day):
    """Claim the exclusive right to send `day`'s digest. True means we own it.

    Mechanism: insert one row into `tabDefaultValue` whose PRIMARY KEY encodes
    the date, so the database's own uniqueness constraint is the mutex.

    Why a DB row and not a module flag or a cache key — the guarantee has to
    survive a worker restart and a Redis flush, and a manual `bench execute`
    must see what this morning's scheduler run already did. Only a durable row
    does that.

    Why INSERT and not read-then-write — two workers starting together would
    both read "no marker yet" and both send. Only one INSERT can win; the loser
    gets a duplicate-key error and returns False.

    The row is written inside the job's own transaction, which the scheduler
    commits once the method returns (scheduled_job_type.py). So the claim and
    the queued mail commit together, and if the send raises, the caller drops
    the claim again (_release_digest_day) rather than burning the day.
    """
    claim = f"digest-{day}"
    save_point = "cheque_digest_claim"

    frappe.db.savepoint(save_point)
    try:
        frappe.db.sql(
            """
            INSERT INTO `tabDefaultValue`
                (name, creation, modified, owner, modified_by,
                 parent, parenttype, parentfield, defkey, defvalue)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                claim, now(), now(), "Administrator", "Administrator",
                DIGEST_CLAIM_PARENT, "__default", "system_defaults", claim, day,
            ),
        )
    except Exception as exc:
        # MariaDB leaves the transaction usable after a duplicate key, Postgres
        # aborts it — rolling back to the savepoint keeps either backend safe
        # to carry on working in.
        frappe.db.rollback(save_point=save_point)
        if frappe.db.is_duplicate_entry(exc):
            return False
        raise

    frappe.db.release_savepoint(save_point)
    return True


def _release_digest_day(day):
    """Give back an unused claim so the next run can retry today."""
    frappe.db.delete("DefaultValue", {"name": f"digest-{day}"})


def _digest_lines(rows):
    """Render each cheque to one plain-text line.

    Guarded per row on purpose: a single cheque with unformattable data (an
    unknown currency, say) must cost only its own line, never the reminder of
    every other cheque in the digest.
    """
    lines = []
    for row in rows:
        try:
            lines.append(
                "{cheque_no} · {party_type} {party} · {amount} · due {due} · {status} ({cheque_type})".format(
                    cheque_no=row.cheque_no or row.name,
                    party_type=row.party_type or "",
                    party=row.party or "",
                    amount=fmt_money(row.amount, currency=row.currency),
                    due=format_date(row.due_date),
                    status=row.status,
                    cheque_type=row.cheque_type,
                )
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"ChequeTracker: digest line failed for {row.get('name')}",
            )
            lines.append(row.get("name"))
    return lines


def _build_digest_html(day, reminder_days, overdue_lines, upcoming_lines):
    """The digest body. Every interpolated value is escaped — party names and
    drawer names are free text typed by users and end up inside an email."""
    header = _(
        "Cheque reminder digest for {0}. Covers everything already overdue "
        "plus everything falling due within {1} day(s)."
    ).format(format_date(day), reminder_days)

    parts = [f"<p>{escape_html(header)}</p>"]
    for title, lines in ((_("Overdue"), overdue_lines), (_("Due soon"), upcoming_lines)):
        if not lines:
            continue
        items = "".join(f"<li>{escape_html(line)}</li>" for line in lines)
        parts.append(f"<h4>{escape_html(title)} ({len(lines)})</h4><ul>{items}</ul>")

    return "".join(parts)


def _build_digest_text(day, overdue_lines, upcoming_lines):
    """Plain-text twin of the HTML body, for Slack."""
    parts = [
        _("Cheque reminders for {0}: {1} overdue, {2} due soon.").format(
            format_date(day), len(overdue_lines), len(upcoming_lines)
        )
    ]
    for title, lines in ((_("Overdue"), overdue_lines), (_("Due soon"), upcoming_lines)):
        if not lines:
            continue
        parts.append(f"*{title}*")
        parts.extend(f"• {line}" for line in lines)

    return "\n".join(parts)


def _notify_treasury_users(subject, html):
    """Raise a Desk notification for every Treasury User. Returns the users.

    Type "Alert" so the row is written even when a recipient happens to be the
    user the job runs as — notification_log.make_notification_logs skips
    self-notifications for every other type.

    Swallows its own failures: a Desk notification is a convenience, and losing
    it must not cost the day its email digest.
    """
    try:
        users = get_users_with_role("Treasury User")
        if not users:
            return []

        enqueue_create_notification(
            users,
            {
                "type": "Alert",
                "subject": subject,
                "email_content": html,
                "document_type": "Cheque Tracker Settings",
                "document_name": "Cheque Tracker Settings",
                "from_user": frappe.session.user,
            },
        )
        return users
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "ChequeTracker: digest Desk notification failed",
        )
        return []


def _post_digest_to_slack(text):
    """Best-effort Slack post. Returns a short status string for the caller.

    The guard is `developer_mode`: a dev or CI site must never ring a real
    treasury channel, and that flag is the one thing every dev site sets and no
    production site does.

    Everything past the guard is optional — the "Slack Webhook URL" doctype may
    not exist in this Frappe build, the webhook may not be configured, Slack may
    be down. None of that may take the digest with it, so it all degrades to a
    logged no-op.
    """
    if frappe.conf.developer_mode:
        return "skipped: developer_mode"

    try:
        if not frappe.db.exists("DocType", "Slack Webhook URL"):
            return "skipped: doctype not installed"
        if not frappe.db.exists("Slack Webhook URL", DIGEST_SLACK_WEBHOOK):
            return f"skipped: no webhook named {DIGEST_SLACK_WEBHOOK}"

        from frappe.integrations.doctype.slack_webhook_url.slack_webhook_url import (
            send_slack_message,
        )

        # The helper's first argument is the *name* of the Slack Webhook URL
        # doc, not a URL (it looks the URL up itself). The reference points at
        # Settings so the optional "Go to the document" button lands somewhere
        # real — a digest spans many cheques and has no single document.
        send_slack_message(
            webhook_url=DIGEST_SLACK_WEBHOOK,
            message=text,
            reference_doctype="Cheque Tracker Settings",
            reference_name="Cheque Tracker Settings",
        )
        return "sent"
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "ChequeTracker: Slack digest post failed",
        )
        return "failed"


def send_daily_cheque_reminders(as_of=None):
    """Daily job (§4.6): ONE digest per day covering every submitted cheque
    that is already overdue or falls due within Settings.reminder_days.

    `as_of` exists for tests and for replaying a missed day by hand; the
    scheduler calls it with no arguments.

    Returns a dict describing what it did. The scheduler ignores the return
    value — tests and `bench execute` read it, which is why nothing here is
    asserted through side effects alone.

    Never raises. A scheduled job that throws takes the rest of the daily queue
    down with it, and one unsent reminder is far cheaper than a stalled
    scheduler.
    """
    logger = frappe.logger("cheque_tracker", allow_site=True)
    day = str(getdate(as_of or today()))
    claimed = False
    result = {
        "date": day,
        "sent": False,
        "reason": None,
        "reminder_days": None,
        "overdue": [],
        "upcoming": [],
        "recipients": [],
        "email_queue": None,
        "notified_users": [],
        "slack": "not attempted",
    }

    try:
        reminder_days, recipients = get_reminder_settings()
        overdue, upcoming = get_cheques_for_reminder(as_of=day, reminder_days=reminder_days)

        result["reminder_days"] = reminder_days
        result["overdue"] = [row.name for row in overdue]
        result["upcoming"] = [row.name for row in upcoming]

        if not overdue and not upcoming:
            # Nothing to say, so deliberately do NOT claim the day: a cheque
            # submitted later this morning should still get its digest.
            result["reason"] = "nothing due"
            return result

        if not _claim_digest_day(day):
            result["reason"] = "already sent today"
            return result
        claimed = True

        overdue_lines = _digest_lines(overdue)
        upcoming_lines = _digest_lines(upcoming)
        subject = _("Cheque reminders {0} — {1} overdue, {2} due soon").format(
            format_date(day), len(overdue), len(upcoming)
        )
        html = _build_digest_html(day, reminder_days, overdue_lines, upcoming_lines)

        if recipients:
            queued = frappe.sendmail(
                recipients=recipients,
                subject=subject,
                message=html,
                reference_doctype="Cheque Tracker Settings",
                reference_name="Cheque Tracker Settings",
            )
            result["recipients"] = recipients
            result["email_queue"] = queued.name if queued else None
        else:
            logger.warning(
                "[ChequeTracker] digest for %s has no recipients — "
                "set Cheque Tracker Settings > Notify Emails",
                day,
            )

        # Both of these swallow their own errors, so by here the only thing
        # that can still fail is bookkeeping, not delivery.
        result["notified_users"] = _notify_treasury_users(subject, html)
        result["slack"] = _post_digest_to_slack(
            _build_digest_text(day, overdue_lines, upcoming_lines)
        )
        result["sent"] = True

        logger.info(
            "[ChequeTracker] digest %s | overdue=%s | upcoming=%s | recipients=%s | slack=%s",
            day, len(overdue), len(upcoming), len(recipients), result["slack"],
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "ChequeTracker: daily reminder digest failed",
        )
        result["reason"] = "error"
        if claimed:
            # The send blew up, so nobody was told anything — hand the day back
            # instead of letting a half-run claim suppress the retry.
            try:
                _release_digest_day(day)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "ChequeTracker: could not release digest claim",
                )

    return result
