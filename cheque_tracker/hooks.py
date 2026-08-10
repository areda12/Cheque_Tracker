app_name = "cheque_tracker"
app_title = "Cheque Tracker"
app_publisher = "Ahmed Abbas"
app_description = "Cheque Tracking System for ERPNext"
app_email = "ahmed@example.com"
app_license = "MIT"

# ------------------------------------------------------------------
# Assets bundled by `bench build`
# ------------------------------------------------------------------
app_include_css = "/assets/cheque_tracker/css/cheque_tracker.css"
app_include_js  = "/assets/cheque_tracker/js/cheque_tracker.js"

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------
fixtures = [
    {
        "dt": "Role",
        "filters": [["name", "in", ["Treasury User", "Cheque Auditor"]]],
    },
    {
        "dt": "Workflow",
        "filters": [["document_type", "=", "Cheque"]],
    },
    {
        "dt": "Workflow State",
        "filters": [["workflow_name", "=", "Cheque Workflow"]],
    },
    {
        "dt": "Workflow Action Master",
        "filters": [
            [
                "name",
                "in",
                [
                    "Receive",
                    "Move to Safe",
                    "Deposit",
                    "Hand Over",
                    "Clear",
                    "Bounce",
                    "Return",
                    "Replace",
                    "Cancel Cheque",
                ],
            ]
        ],
    },
    {
        "dt": "Workspace",
        "filters": [["name", "=", "Cheque Tracker"]],
    },
    {
        "dt": "Workspace Sidebar",
        "filters": [["name", "=", "Cheque Tracker"]],
    },
    {
        "dt": "Desktop Icon",
        "filters": [["app", "=", "cheque_tracker"]],
    },
    {
        "dt": "Number Card",
        "filters": [["module", "=", "Cheque Tracker"]],
    },
    {
        "dt": "Dashboard Chart",
        "filters": [["module", "=", "Cheque Tracker"]],
    },
    {
        "dt": "Report",
        "filters": [
            [
                "name",
                "in",
                [
                    "Cheques Due This Week",
                    "Deposited Not Cleared",
                    "Bounced Cheques Register",
                    "Cheque Book Utilization",
                ],
            ]
        ],
    },
    {
        "dt": "Property Setter",
        "filters": [["doc_type", "=", "Cheque"]],
    },
]

# ------------------------------------------------------------------
# Scheduled Tasks
# ------------------------------------------------------------------
scheduler_events = {
    "daily": [
        "cheque_tracker.cheque_tracker.tasks.auto_update_cheque_statuses",
    ],
}

# ------------------------------------------------------------------
# Document Events
# ------------------------------------------------------------------
# IMPORTANT: Cheque lifecycle hooks (after_insert, before_save, on_submit,
# on_cancel) are defined as *methods* on the Cheque Document subclass.
# Frappe calls Document class methods automatically — registering them
# again here via doc_events would fire them TWICE, producing duplicate
# Cheque Events. They are intentionally absent from this dict.
#
# Selling-side accounting is now JE-only and Cheque-doc-driven (Cheque
# actions create/cancel JEs as side effects), so Payment Entry and
# Journal Entry no longer need doc_event hooks here.
doc_events = {
    "Cheque Book": {
        "on_submit": "cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book.on_submit",
        "on_cancel": "cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book.on_cancel",
    },
}

# ------------------------------------------------------------------
# Install hooks
# ------------------------------------------------------------------
# See cheque_tracker/install.py and AUDIT.md §3 for rationale.
# Bootstraps Cheque Tracker Settings (Singles row) and warns if
# required account fields are unconfigured.
after_install = "cheque_tracker.install.after_install"
after_migrate = "cheque_tracker.install.ensure_ui_fixtures"

# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------
# Builds the deterministic test environment (canonical EEI company, bank
# account, parties, roles, settings) before the suite runs. Without it the
# helpers fell back to `frappe.get_all("Company", limit=1)` and most tests
# skipped themselves. See cheque_tracker/tests/utils.py.
before_tests = "cheque_tracker.tests.utils.before_tests"

# ------------------------------------------------------------------
# Jinja
# ------------------------------------------------------------------
jinja = {
    "methods": [],
    "filters": [],
}
