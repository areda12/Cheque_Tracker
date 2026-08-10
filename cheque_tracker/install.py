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



# ------------------------------------------------------------------
# ensure_ui_fixtures() lived here until v1.3.
#
# It re-imported workspace.json / workspace_sidebar.json / desktop_icon.json
# after every migrate, because `remove_orphan_entities()` deleted the Workspace
# and Desktop Icon on every migrate. Its docstring blamed Frappe for "silently
# skipping" the files, which was never the cause: core force-imports every
# fixture. The records were deleted because `create_entity_file_map()` globs
# `<app_path>/**/workspace/**/*.json` and the app had no `workspace/` directory,
# and `check_if_record_exists()` looks for `<app_path>/desktop_icon/<name>.json`
# and that directory was empty (frappe/model/sync.py:271-312).
#
# All three records now ship as standard files where core expects them, so
# nothing is orphaned and the after_migrate hook is gone. Deleting the band-aid
# also restores something it was quietly destroying: an admin's edits to the
# Workspace layout, which it overwrote on every single migrate.
