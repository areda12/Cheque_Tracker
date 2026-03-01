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
# `bench export-fixtures` writes them; `bench migrate` re-imports them.
# ------------------------------------------------------------------
fixtures = [
    # New roles shipped with this app
    {
        "dt": "Role",
        "filters": [["name", "in", ["Treasury User", "Cheque Auditor"]]],
    },
    # Workflow for Cheque
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
                    "Present",
                    "Clear",
                    "Bounce",
                    "Return",
                    "Replace",
                    "Cancel Cheque",
                ],
            ]
        ],
    },
    # Workspace
    {
        "dt": "Workspace",
        "filters": [["name", "=", "Treasury Workbench"]],
    },
    # Script Reports
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
    # Property setters (read-only rules for cheque_no on Outgoing)
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
doc_events = {
    "Cheque": {
        "after_insert": "cheque_tracker.cheque_tracker.doctype.cheque.cheque.after_insert",
        "before_save": "cheque_tracker.cheque_tracker.doctype.cheque.cheque.before_save",
        "on_submit": "cheque_tracker.cheque_tracker.doctype.cheque.cheque.on_submit",
        "on_cancel": "cheque_tracker.cheque_tracker.doctype.cheque.cheque.on_cancel",
    },
    "Cheque Book": {
        "on_submit": "cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book.on_submit",
        "on_cancel": "cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book.on_cancel",
    },
}

# ------------------------------------------------------------------
# Jinja
# ------------------------------------------------------------------
jinja = {
    "methods": [],
    "filters": [],
}
