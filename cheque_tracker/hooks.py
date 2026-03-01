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
    {
        "dt": "Workspace",
        "filters": [["name", "=", "Treasury Workbench"]],
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
doc_events = {
    "Cheque Book": {
        "on_submit": "cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book.on_submit",
        "on_cancel": "cheque_tracker.cheque_tracker.doctype.cheque_book.cheque_book.on_cancel",
    },
    # ---------------------------------------------------------------
    # Payment Entry: sync cheque status when recording PE is submitted/cancelled
    # ---------------------------------------------------------------
    "Payment Entry": {
        "on_submit": "cheque_tracker.cheque_tracker.hooks.payment_entry_hooks.payment_entry_on_submit",
        "on_cancel": "cheque_tracker.cheque_tracker.hooks.payment_entry_hooks.payment_entry_on_cancel",
    },
    # ---------------------------------------------------------------
    # Journal Entry: sync cheque status for clearance & bounce JEs
    # ---------------------------------------------------------------
    "Journal Entry": {
        "on_submit": "cheque_tracker.cheque_tracker.hooks.journal_entry_hooks.journal_entry_on_submit",
        "on_cancel": "cheque_tracker.cheque_tracker.hooks.journal_entry_hooks.journal_entry_on_cancel",
    },
}

# ------------------------------------------------------------------
# Jinja
# ------------------------------------------------------------------
jinja = {
    "methods": [],
    "filters": [],
}
