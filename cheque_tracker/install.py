# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Install hooks for Cheque Tracker.

Wired in hooks.py via `after_install = "cheque_tracker.install.after_install"`.
"""

import os

import frappe


def after_install():
    """
    Bootstrap Cheque Tracker Settings on app install.

    Creates the Singles row so reads don't lazy-init in production
    code paths, and logs a clear warning that account fields need
    configuration before the app is usable. We deliberately do NOT
    auto-detect or populate accounts — they are environment-specific
    and silent guessing produces wrong-account postings that are
    hard to diagnose. Better to fail loudly.

    See AUDIT.md §3 for the full decision rationale (option (b)).
    """
    _bootstrap_settings()


# Required-for-flow Settings field names. AR comes from
# Company.default_receivable_account (not from Settings), so it is not
# in this list. `default_bank_account` is an orphan field with no read
# path in the app code (audit §3 follow-up #21) and is also excluded.
_REQUIRED_ACCOUNT_FIELDS = (
    "pdc_receivable_account",
    "default_bank_gl_account",
    "default_cash_account",
)


def _bootstrap_settings():
    """
    Create the Cheque Tracker Settings Singles row with blank accounts.

    Idempotent: re-running on a site where the row already exists just
    re-saves the singleton (no-op if no fields changed) and re-logs the
    warning if any required accounts remain unconfigured. Safe to call
    from both install and migration paths.
    """
    settings = frappe.get_single("Cheque Tracker Settings")
    # Touch and save to ensure the row is persisted (not just lazy-init).
    # On Singles, save() is a no-op if no field values changed.
    settings.flags.ignore_permissions = True
    settings.save()

    unconfigured = [f for f in _REQUIRED_ACCOUNT_FIELDS if not settings.get(f)]
    if not unconfigured:
        return

    msg = (
        "Cheque Tracker installed. Before using the app, configure "
        "the following account fields in Cheque Tracker Settings: "
        + ", ".join(unconfigured)
        + ". The app will fail with 'account not configured' errors on "
        "Payment Entry / Journal Entry submission until these are set. "
        "See README.md → Post-Install Configuration."
    )
    # Log to bench output (visible during install) and to Error Log
    # for operator follow-up after install completes.
    print(f"\n[cheque_tracker] WARNING: {msg}\n")
    frappe.log_error(
        message=msg,
        title="Cheque Tracker: Settings Not Configured",
    )


def ensure_workspace_sidebar():
    """
    Idempotently apply the workspace_sidebar.json fixture.

    Wired in hooks.py via `after_migrate = "cheque_tracker.install.ensure_workspace_sidebar"`.

    Frappe's standard fixture import (also configured in hooks.py) sometimes
    silently skips Workspace Sidebar records during deploy — observed during
    PR #12 deploy on Frappe Cloud, where workspace_sidebar.json was valid and
    on disk but the record did not materialize in DB. Manually invoking
    import_doc on the same file works correctly.

    This hook re-applies the fixture file via import_doc on every migrate as
    belt-and-suspenders. import_doc is idempotent — it inserts if the record
    doesn't exist, updates if it does. Failures are swallowed and logged
    rather than raised, so a sidebar issue never blocks a migrate.
    """
    fixture_path = os.path.join(
        frappe.get_app_path("cheque_tracker"),
        "fixtures",
        "workspace_sidebar.json",
    )
    if not os.path.exists(fixture_path):
        return

    try:
        from frappe.core.doctype.data_import.data_import import import_doc
        import_doc(fixture_path)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="Cheque Tracker: Workspace Sidebar fixture re-import failed",
        )
