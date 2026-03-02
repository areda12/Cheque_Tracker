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
 *
 * Filters & UX:
 *   • Bank Account → company bank accounts only
 *   • Cheque Book / Cheque Leaf → hidden for Incoming cheques
 *   • Reference DocType → filtered by party_type context
 *   • Reference Name → filtered by party + reference_doctype
 *   • Cheque Book → filtered by company & bank_account
 *   • Cheque Leaf → filtered by selected cheque_book
 */

// ─── Reference DocType mappings per Party Type ───────────────────────────
const REFERENCE_DOCTYPE_MAP = {
    Customer: ["Sales Invoice", "Sales Order", "Delivery Note", "Payment Entry", "Journal Entry"],
    Supplier: ["Purchase Invoice", "Purchase Order", "Purchase Receipt", "Payment Entry", "Journal Entry"],
    Employee: ["Expense Claim", "Payment Entry", "Journal Entry"],
    Other:    ["Payment Entry", "Journal Entry"],
};

frappe.ui.form.on("Cheque", {
    // ------------------------------------------------------------------
    // setup: one-time filters that don't depend on field values
    // ------------------------------------------------------------------
    setup(frm) {
        // 1) Bank Account → only company bank accounts
        frm.set_query("bank_account", () => ({
            filters: {
                is_company_account: 1,
                ...(frm.doc.company ? { company: frm.doc.company } : {}),
            },
        }));

        // Cheque Book → filtered by company (and bank_account if set)
        frm.set_query("cheque_book", () => {
            const filters = { status: ["in", ["Draft", "Active"]] };
            if (frm.doc.company) filters.company = frm.doc.company;
            if (frm.doc.bank_account) filters.bank_account = frm.doc.bank_account;
            return { filters };
        });

        // Cheque Leaf → filtered by selected cheque_book, only unused
        frm.set_query("cheque_leaf", () => {
            const filters = { leaf_status: "Available" };
            if (frm.doc.cheque_book) filters.cheque_book = frm.doc.cheque_book;
            return { filters };
        });

        // 3) Reference DocType → filtered by party_type context
        frm.set_query("reference_doctype", () => {
            const allowed = _get_allowed_reference_doctypes(frm);
            return {
                filters: {
                    name: ["in", allowed],
                },
            };
        });

        // 4) Reference Name → filtered by party + reference_doctype
        frm.set_query("reference_name", () => {
            const ref_dt = frm.doc.reference_doctype;
            if (!ref_dt) return {};

            const filters = {};

            // Map party_type to the correct field name on the reference doctype
            const mapping = _get_party_field(ref_dt, frm.doc.party_type);
            if (mapping && frm.doc.party) {
                filters[mapping] = frm.doc.party;
            }

            // Only show submitted documents for invoice/order types
            const submittable_doctypes = [
                "Sales Invoice", "Sales Order", "Delivery Note",
                "Purchase Invoice", "Purchase Order", "Purchase Receipt",
                "Expense Claim", "Payment Entry", "Journal Entry",
            ];
            if (submittable_doctypes.includes(ref_dt)) {
                filters.docstatus = 1;
            }

            if (frm.doc.company) {
                filters.company = frm.doc.company;
            }

            return { filters };
        });

        // Cost Center → filtered by company
        frm.set_query("cost_center", () => {
            const filters = { is_group: 0 };
            if (frm.doc.company) filters.company = frm.doc.company;
            return { filters };
        });

        // PDC Account → filtered by company
        frm.set_query("pdc_account", () => {
            const filters = { is_group: 0 };
            if (frm.doc.company) filters.company = frm.doc.company;
            return { filters };
        });
    },

    // ------------------------------------------------------------------
    // refresh: buttons + visibility toggles
    // ------------------------------------------------------------------
    refresh(frm) {
        _setup_buttons(frm);
        _toggle_cheque_book_fields(frm);
    },

    // Re-evaluate buttons when status changes in the form (workflow transitions)
    status(frm) {
        _setup_buttons(frm);
    },

    // ------------------------------------------------------------------
    // Field change handlers
    // ------------------------------------------------------------------

    // 2) Hide cheque_book & cheque_leaf for Incoming cheques
    cheque_type(frm) {
        _toggle_cheque_book_fields(frm);
        // Clear cheque book fields if switching to Incoming
        if (frm.doc.cheque_type === "Incoming") {
            frm.set_value("cheque_book", "");
            frm.set_value("cheque_leaf", "");
        }
    },

    // When company changes, validate dependent fields & auto-set currency
    company(frm) {
        if (frm.doc.bank_account) {
            // Validate existing bank_account still belongs to new company
            frappe.db.get_value("Bank Account", frm.doc.bank_account, "company", (r) => {
                if (r && r.company !== frm.doc.company) {
                    frm.set_value("bank_account", "");
                }
            });
        }
        frm.set_value("cost_center", "");
        frm.set_value("pdc_account", "");
        // Auto-fetch default currency from company
        if (frm.doc.company && !frm.doc.currency) {
            frappe.db.get_value("Company", frm.doc.company, "default_currency", (r) => {
                if (r && r.default_currency) {
                    frm.set_value("currency", r.default_currency);
                }
            });
        }
    },

    // When bank_account changes, clear cheque_book if it no longer matches
    bank_account(frm) {
        if (frm.doc.cheque_book) {
            frappe.db.get_value("Cheque Book", frm.doc.cheque_book, "bank_account", (r) => {
                if (r && r.bank_account !== frm.doc.bank_account) {
                    frm.set_value("cheque_book", "");
                    frm.set_value("cheque_leaf", "");
                }
            });
        }
    },

    // When cheque_book changes, clear cheque_leaf
    cheque_book(frm) {
        if (!frm.doc.cheque_book) {
            frm.set_value("cheque_leaf", "");
        }
    },

    // When party_type changes, clear dependent fields
    party_type(frm) {
        frm.set_value("party", "");
        frm.set_value("reference_doctype", "");
        frm.set_value("reference_name", "");
    },

    // When party changes, clear reference_name and auto-set drawer_name
    party(frm) {
        frm.set_value("reference_name", "");
        // Auto-populate drawer_name for incoming cheques
        if (frm.doc.cheque_type === "Incoming" && frm.doc.party && frm.doc.party_type) {
            const name_field = _get_name_field(frm.doc.party_type);
            if (name_field) {
                const dt = frm.doc.party_type === "Other" ? "Supplier" : frm.doc.party_type;
                frappe.db.get_value(dt, frm.doc.party, name_field, (r) => {
                    if (r && r[name_field] && !frm.doc.drawer_name) {
                        frm.set_value("drawer_name", r[name_field]);
                    }
                });
            }
        }
    },

    // When reference_doctype changes, clear reference_name
    reference_doctype(frm) {
        frm.set_value("reference_name", "");
    },
});


// ═══════════════════════════════════════════════════════════════════════
//  HELPER: Toggle cheque book/leaf visibility based on cheque_type
// ═══════════════════════════════════════════════════════════════════════

function _toggle_cheque_book_fields(frm) {
    const isIncoming = frm.doc.cheque_type === "Incoming";
    frm.toggle_display("cheque_book", !isIncoming);
    frm.toggle_display("cheque_leaf", !isIncoming);
}


// ═══════════════════════════════════════════════════════════════════════
//  HELPER: Get allowed reference doctypes based on party_type
// ═══════════════════════════════════════════════════════════════════════

function _get_allowed_reference_doctypes(frm) {
    const party_type = frm.doc.party_type;
    if (party_type && REFERENCE_DOCTYPE_MAP[party_type]) {
        return REFERENCE_DOCTYPE_MAP[party_type];
    }
    // Fallback: show all possible reference doctypes
    const all = new Set();
    Object.values(REFERENCE_DOCTYPE_MAP).forEach((arr) => arr.forEach((dt) => all.add(dt)));
    return Array.from(all);
}


// ═══════════════════════════════════════════════════════════════════════
//  HELPER: Map party_type to the correct field on reference doctypes
// ═══════════════════════════════════════════════════════════════════════

function _get_party_field(doctype, party_type) {
    const field_map = {
        "Sales Invoice":      { Customer: "customer" },
        "Sales Order":        { Customer: "customer" },
        "Delivery Note":      { Customer: "customer" },
        "Purchase Invoice":   { Supplier: "supplier" },
        "Purchase Order":     { Supplier: "supplier" },
        "Purchase Receipt":   { Supplier: "supplier" },
        "Expense Claim":      { Employee: "employee" },
        "Payment Entry":      { Customer: "party", Supplier: "party", Employee: "party" },
        "Journal Entry":      {},  // JE uses multi-row accounts, no single party field
    };

    const dt_map = field_map[doctype];
    if (dt_map && dt_map[party_type]) {
        return dt_map[party_type];
    }
    return null;
}


// ═══════════════════════════════════════════════════════════════════════
//  HELPER: Get the "name" field for each party type (for drawer_name)
// ═══════════════════════════════════════════════════════════════════════

function _get_name_field(party_type) {
    const map = {
        Customer: "customer_name",
        Supplier: "supplier_name",
        Employee: "employee_name",
    };
    return map[party_type] || null;
}


// ═══════════════════════════════════════════════════════════════════════
//  ACTION BUTTONS
// ═══════════════════════════════════════════════════════════════════════

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
