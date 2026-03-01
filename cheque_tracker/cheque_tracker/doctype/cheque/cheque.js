// Copyright (c) 2024, Ahmed Abbas and contributors
// License: MIT

/**
 * Cheque form – client-side controller.
 *
 * Adds action buttons for:
 *   • Create Recording Payment Entry  (AR → PDC asset)
 *   • Create Clearance Entry          (PDC asset → Bank)
 *   • Process Bounce                  (reverse PDC recording)
 *   • Mark In Safe / Mark Deposited / Mark Presented  (non-financial custody events)
 */

frappe.ui.form.on("Cheque", {
    // ------------------------------------------------------------------
    // refresh: wire up buttons whenever the form reloads
    // ------------------------------------------------------------------
    refresh(frm) {
        _setup_buttons(frm);
    },

    // Re-evaluate buttons when status changes in the form (workflow transitions)
    status(frm) {
        _setup_buttons(frm);
    },
});

function _setup_buttons(frm) {
    // Only show action buttons for submitted (docstatus=1) Incoming cheques
    const isSubmitted    = frm.doc.docstatus === 1;
    const isIncoming     = frm.doc.cheque_type === "Incoming";
    const status         = frm.doc.status || "Draft";

    // Clear any previously added custom buttons to avoid duplicates
    frm.clear_custom_buttons();

    if (!isSubmitted) return;

    // ------------------------------------------------------------------
    // Non-financial custody transitions
    // ------------------------------------------------------------------
    if (["Received", "In Safe"].includes(status) && isIncoming) {
        if (status === "Received") {
            frm.add_custom_button(__("Mark In Safe"), () => {
                _change_status(frm, "In Safe");
            }, __("Manage"));
        }
    }
    if (status === "In Safe") {
        frm.add_custom_button(__("Mark Deposited"), () => {
            _change_status(frm, "Deposited");
        }, __("Manage"));
    }
    if (status === "Deposited") {
        frm.add_custom_button(__("Mark Presented"), () => {
            _change_status(frm, "Presented");
        }, __("Manage"));
    }

    // ------------------------------------------------------------------
    // A) Recording Payment Entry
    // ------------------------------------------------------------------
    if (isIncoming && ["Received", "Draft"].includes(status)) {
        const rec_pe = frm.doc.recording_payment_entry;
        if (!rec_pe) {
            frm.add_custom_button(__("Create Recording Payment Entry"), () => {
                _make_recording_pe(frm);
            }, __("Accounting"));
        } else {
            frm.add_custom_button(__("View Recording Payment Entry"), () => {
                frappe.set_route("Form", "Payment Entry", rec_pe);
            }, __("Accounting"));
        }
    }

    // ------------------------------------------------------------------
    // B) Clearance Entry
    // ------------------------------------------------------------------
    if (isIncoming && ["Received", "In Safe", "Deposited", "Presented"].includes(status)) {
        const clr_je = frm.doc.clearance_journal_entry;
        if (!clr_je) {
            frm.add_custom_button(__("Create Clearance Entry"), () => {
                _make_clearance_je(frm);
            }, __("Accounting"));
        } else {
            frm.add_custom_button(__("View Clearance Entry"), () => {
                frappe.set_route("Form", "Journal Entry", clr_je);
            }, __("Accounting"));
        }
    }

    // ------------------------------------------------------------------
    // C) Bounce
    // ------------------------------------------------------------------
    if (isIncoming && ["Received", "In Safe", "Deposited", "Presented"].includes(status)) {
        frm.add_custom_button(__("Process Bounce"), () => {
            _process_bounce(frm);
        }, __("Accounting"));
    }

    // View reversal JE if it exists
    if (frm.doc.reversal_journal_entry) {
        frm.add_custom_button(__("View Reversal Entry"), () => {
            frappe.set_route("Form", "Journal Entry", frm.doc.reversal_journal_entry);
        }, __("Accounting"));
    }
}

// ---------------------------------------------------------------------------
// Action: Create Recording Payment Entry
// ---------------------------------------------------------------------------
function _make_recording_pe(frm) {
    frappe.confirm(
        __("Create a Recording Payment Entry for Cheque {0}?", [frm.doc.name]),
        () => {
            frappe.call({
                method: "cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial.make_recording_payment_entry",
                args: { cheque_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Creating Recording Payment Entry…"),
                callback(r) {
                    if (r.message) {
                        frm.reload_doc();
                        frappe.confirm(
                            __("Recording Payment Entry {0} created. Open it now?", [r.message]),
                            () => frappe.set_route("Form", "Payment Entry", r.message)
                        );
                    }
                },
            });
        }
    );
}

// ---------------------------------------------------------------------------
// Action: Create Clearance Journal Entry
// ---------------------------------------------------------------------------
function _make_clearance_je(frm) {
    if (!frm.doc.bank_account) {
        frappe.msgprint({
            title: __("Bank Account Required"),
            message: __("Please set the Bank Account on this Cheque before creating the Clearance Entry."),
            indicator: "orange",
        });
        return;
    }
    frappe.confirm(
        __("Create a Clearance Journal Entry for Cheque {0}?", [frm.doc.name]),
        () => {
            frappe.call({
                method: "cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial.make_clearance_journal_entry",
                args: { cheque_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Creating Clearance Journal Entry…"),
                callback(r) {
                    if (r.message) {
                        frm.reload_doc();
                        frappe.confirm(
                            __("Clearance Journal Entry {0} created. Open it now?", [r.message]),
                            () => frappe.set_route("Form", "Journal Entry", r.message)
                        );
                    }
                },
            });
        }
    );
}

// ---------------------------------------------------------------------------
// Action: Process Bounce
// ---------------------------------------------------------------------------
function _process_bounce(frm) {
    frappe.prompt(
        [{ fieldname: "notes", fieldtype: "Small Text", label: __("Bounce Reason / Notes"), reqd: 1 }],
        (values) => {
            frappe.call({
                method: "cheque_tracker.cheque_tracker.doctype.cheque.cheque_financial.process_bounce",
                args: { cheque_name: frm.doc.name, notes: values.notes },
                freeze: true,
                freeze_message: __("Processing Bounce…"),
                callback(r) {
                    frm.reload_doc();
                    if (r.message) {
                        frappe.confirm(
                            __("Reversal Journal Entry {0} created in Draft. Open it now?", [r.message]),
                            () => frappe.set_route("Form", "Journal Entry", r.message)
                        );
                    } else {
                        frappe.msgprint({
                            title: __("Bounce Processed"),
                            message: __("Cheque has been marked as Bounced."),
                            indicator: "red",
                        });
                    }
                },
            });
        },
        __("Process Bounce"),
        __("Confirm")
    );
}

// ---------------------------------------------------------------------------
// Action: Non-financial status change
// ---------------------------------------------------------------------------
function _change_status(frm, new_status) {
    frappe.call({
        method: "cheque_tracker.cheque_tracker.doctype.cheque.cheque.change_cheque_status",
        args: { cheque_name: frm.doc.name, new_status },
        freeze: true,
        freeze_message: __("Updating status…"),
        callback(r) {
            if (r.message && r.message.status === "ok") {
                frm.reload_doc();
                frappe.show_alert({ message: __("Status updated to {0}", [new_status]), indicator: "green" });
            }
        },
    });
}
