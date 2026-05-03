// Copyright (c) 2024, Ahmed Abbas and contributors
// License: MIT

/**
 * Cheque form – client-side controller.
 *
 * Status transitions are driven by the Cheque Workflow's built-in
 * actions (Deposit / Clear / Bounce / Return / Cancel Cheque). The
 * Cheque controller's on_update hook fires the matching GL side
 * effect when status changes.
 *
 * This file adds:
 *   • Quick-view button for the linked Clearance JE.
 *   • A pre-Clear dialog wrapping the workflow's Clear action that
 *     prompts for cleared_date and bank_account when missing.
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

    frm.clear_custom_buttons();

    if (!isSubmitted) return;

    // Selling-side state transitions are now driven entirely by the
    // Cheque Workflow's built-in actions (Deposit / Clear / Bounce /
    // Return / Cancel Cheque). The on_update controller hook fires
    // the matching GL side effect when status changes.
    //
    // We add a couple of conveniences:
    //   • Quick-view button for the linked Clearance JE.
    //   • A pre-Clear dialog that prompts for cleared_date and
    //     bank_account, saves them, then triggers the workflow Clear.

    if (frm.doc.clearance_je) {
        frm.add_custom_button(__("View Clearance JE"), () => {
            frappe.set_route("Form", "Journal Entry", frm.doc.clearance_je);
        }, __("View"));
    }

    if (frm.doc.cheque_type === "Incoming" && frm.doc.status === "Deposited") {
        _wrap_clear_action(frm);
    }
}


// Patch the workflow's "Clear" action so that if cleared_date or
// bank_account is missing, we prompt for them, save, then apply the
// workflow action. If both are already set, the original behaviour
// runs unchanged.
function _wrap_clear_action(frm) {
    const $btn = frm.page.menu.find(
        `[data-label="${encodeURIComponent("Clear")}"]`
    );
    if (!$btn.length || $btn.data("ct-wrapped")) return;
    $btn.data("ct-wrapped", true);

    $btn.off("click").on("click", () => {
        if (frm.doc.cleared_date && frm.doc.bank_account) {
            frappe.xcall("frappe.model.workflow.apply_workflow", {
                doc: frm.doc,
                action: "Clear",
            }).then(() => frm.reload_doc());
            return;
        }

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
            primary_action_label: __("Confirm Clearing"),
            primary_action(values) {
                Object.assign(frm.doc, values);
                frm.save()
                    .then(() => frappe.xcall("frappe.model.workflow.apply_workflow", {
                        doc: frm.doc,
                        action: "Clear",
                    }))
                    .then(() => { d.hide(); frm.reload_doc(); });
            },
        });
        d.show();
    });
}
