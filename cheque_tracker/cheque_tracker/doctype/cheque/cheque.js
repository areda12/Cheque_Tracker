// Copyright (c) 2024, Ahmed Abbas and contributors
// License: MIT

/**
 * Cheque form – client-side controller.
 *
 * Selling-side action buttons (Incoming, submitted, status=Received):
 *   • Mark Cleared  → submits the Clearance JE (Dr Bank / Cr PDC Recv)
 *   • Mark Bounced  → cancels the Hand Over JE
 *
 * Non-financial custody transitions (deposit subflow):
 *   • Mark In Safe / Mark Deposited / Mark Presented
 */

const CLEARANCE_DEPOSIT = "Deposit";
const CLEARANCE_CASH    = "Cash";

const REFERENCE_DOCTYPE_MAP = {
    Customer: ["Sales Invoice", "Sales Order", "Delivery Note", "Payment Entry", "Journal Entry"],
    Supplier: ["Purchase Invoice", "Purchase Order", "Purchase Receipt", "Payment Entry", "Journal Entry"],
    Employee: ["Expense Claim", "Payment Entry", "Journal Entry"],
    Other:    ["Payment Entry", "Journal Entry"],
};

frappe.ui.form.on("Cheque", {
    setup(frm) {
        frm.set_query("bank_account", () => ({
            filters: {
                is_company_account: 1,
                ...(frm.doc.company ? { company: frm.doc.company } : {}),
            },
        }));

        frm.set_query("cheque_book", () => {
            const filters = { status: ["in", ["Draft", "Active"]] };
            if (frm.doc.company) filters.company = frm.doc.company;
            if (frm.doc.bank_account) filters.bank_account = frm.doc.bank_account;
            return { filters };
        });

        frm.set_query("cheque_leaf", () => {
            const filters = { leaf_status: "Available" };
            if (frm.doc.cheque_book) filters.cheque_book = frm.doc.cheque_book;
            return { filters };
        });

        frm.set_query("reference_doctype", () => {
            const allowed = _get_allowed_reference_doctypes(frm);
            return { filters: { name: ["in", allowed] } };
        });

        frm.set_query("reference_name", () => {
            const ref_dt = frm.doc.reference_doctype;
            if (!ref_dt) return {};

            const filters = {};
            const mapping = _get_party_field(ref_dt, frm.doc.party_type);
            if (mapping && frm.doc.party) {
                filters[mapping] = frm.doc.party;
            }

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

        frm.set_query("cost_center", () => {
            const filters = { is_group: 0 };
            if (frm.doc.company) filters.company = frm.doc.company;
            return { filters };
        });

        frm.set_query("cash_account", () => {
            const filters = { is_group: 0, account_type: "Cash" };
            if (frm.doc.company) filters.company = frm.doc.company;
            return { filters };
        });
    },

    refresh(frm) {
        _setup_buttons(frm);
        _toggle_cheque_book_fields(frm);
        _toggle_clearance_type_fields(frm);
    },

    status(frm) {
        _setup_buttons(frm);
    },

    cheque_type(frm) {
        _toggle_cheque_book_fields(frm);
        if (frm.doc.cheque_type === "Incoming") {
            frm.set_value("cheque_book", "");
            frm.set_value("cheque_leaf", "");
        }
    },

    clearance_type(frm) {
        _toggle_clearance_type_fields(frm);
        if (frm.doc.clearance_type === CLEARANCE_CASH) {
            frm.set_value("bank_account", "");
        } else {
            frm.set_value("cash_account", "");
        }
    },

    company(frm) {
        if (frm.doc.bank_account) {
            frappe.db.get_value("Bank Account", frm.doc.bank_account, "company", (r) => {
                if (r && r.company !== frm.doc.company) {
                    frm.set_value("bank_account", "");
                }
            });
        }
        frm.set_value("cost_center", "");
        if (frm.doc.company && !frm.doc.currency) {
            frappe.db.get_value("Company", frm.doc.company, "default_currency", (r) => {
                if (r && r.default_currency) {
                    frm.set_value("currency", r.default_currency);
                }
            });
        }
    },

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

    cheque_book(frm) {
        if (!frm.doc.cheque_book) {
            frm.set_value("cheque_leaf", "");
        }
    },

    party_type(frm) {
        frm.set_value("party", "");
        frm.set_value("reference_doctype", "");
        frm.set_value("reference_name", "");
    },

    party(frm) {
        frm.set_value("reference_name", "");
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

    reference_doctype(frm) {
        frm.set_value("reference_name", "");
    },
});


function _toggle_cheque_book_fields(frm) {
    const isIncoming = frm.doc.cheque_type === "Incoming";
    frm.toggle_display("cheque_book", !isIncoming);
    frm.toggle_display("cheque_leaf", !isIncoming);
}


function _toggle_clearance_type_fields(frm) {
    const isCash = frm.doc.clearance_type === CLEARANCE_CASH;
    const isIncoming = frm.doc.cheque_type === "Incoming";

    if (isIncoming) {
        frm.toggle_display("bank_account", !isCash);
        frm.toggle_display("cash_account", isCash);
        frm.toggle_reqd("bank_account", !isCash);
    } else {
        frm.toggle_display("bank_account", true);
        frm.toggle_display("cash_account", false);
        frm.toggle_display("clearance_type", false);
    }
}


function _get_allowed_reference_doctypes(frm) {
    const party_type = frm.doc.party_type;
    if (party_type && REFERENCE_DOCTYPE_MAP[party_type]) {
        return REFERENCE_DOCTYPE_MAP[party_type];
    }
    const all = new Set();
    Object.values(REFERENCE_DOCTYPE_MAP).forEach((arr) => arr.forEach((dt) => all.add(dt)));
    return Array.from(all);
}


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
        "Journal Entry":      {},
    };

    const dt_map = field_map[doctype];
    if (dt_map && dt_map[party_type]) {
        return dt_map[party_type];
    }
    return null;
}


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
    const isSubmitted = frm.doc.docstatus === 1;
    const isIncoming  = frm.doc.cheque_type === "Incoming";
    const status      = frm.doc.status || "Draft";

    frm.clear_custom_buttons();

    if (!isSubmitted) return;

    const isCash = frm.doc.clearance_type === CLEARANCE_CASH;

    // ------------------------------------------------------------------
    // Selling-side: Mark Cleared / Mark Bounced (only from Received)
    // ------------------------------------------------------------------
    if (isIncoming && status === "Received") {
        frm.add_custom_button(__("Mark Cleared"), () => {
            const d = new frappe.ui.Dialog({
                title: __("Mark Cheque as Cleared"),
                fields: [
                    {
                        fieldname: "cleared_date",
                        fieldtype: "Date",
                        label: __("Cleared Date"),
                        default: frappe.datetime.get_today(),
                        reqd: 1,
                    },
                    {
                        fieldname: "bank_account",
                        fieldtype: "Link",
                        options: "Bank Account",
                        label: __("Bank Account"),
                        default: frm.doc.bank_account,
                        reqd: 1,
                    },
                ],
                primary_action_label: __("Confirm"),
                primary_action(values) {
                    frm.call("mark_cleared", values).then(() => {
                        d.hide();
                        frm.reload_doc();
                    });
                },
            });
            d.show();
        }, __("Actions"));

        frm.add_custom_button(__("Mark Bounced"), () => {
            const d = new frappe.ui.Dialog({
                title: __("Mark Cheque as Bounced"),
                fields: [
                    {
                        fieldname: "reason",
                        fieldtype: "Small Text",
                        label: __("Reason / Bank Notice"),
                    },
                ],
                primary_action_label: __("Confirm Bounce"),
                primary_action(values) {
                    frappe.confirm(
                        __("This will cancel the Hand Over JE and revert AR. Continue?"),
                        () => {
                            frm.call("mark_bounced", values).then(() => {
                                d.hide();
                                frm.reload_doc();
                            });
                        }
                    );
                },
            });
            d.show();
        }, __("Actions"));
    }

    // ------------------------------------------------------------------
    // Non-financial custody transitions (deposit subflow)
    // ------------------------------------------------------------------
    if (isIncoming) {
        if (status === "Received") {
            frm.add_custom_button(__("Mark In Safe"), () => {
                _change_status(frm, "In Safe");
            }, __("Manage"));
        }
        if (!isCash) {
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
        }
    }

    // ------------------------------------------------------------------
    // Quick-view of linked JEs
    // ------------------------------------------------------------------
    if (frm.doc.handover_je) {
        frm.add_custom_button(__("View Hand Over JE"), () => {
            frappe.set_route("Form", "Journal Entry", frm.doc.handover_je);
        }, __("View"));
    }
    if (frm.doc.clearance_je) {
        frm.add_custom_button(__("View Clearance JE"), () => {
            frappe.set_route("Form", "Journal Entry", frm.doc.clearance_je);
        }, __("View"));
    }
}


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
