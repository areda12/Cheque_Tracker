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

        // "Unused" is the leaf state that can still be written. The filter used
        // to ask for "Available", which no leaf ever is, so the picker was empty.
        frm.set_query("cheque_leaf", () => {
            const filters = { leaf_status: "Unused" };
            if (frm.doc.cheque_book) filters.cheque_book = frm.doc.cheque_book;
            if (frm.doc.company) filters.company = frm.doc.company;
            if (frm.doc.bank_account) filters.bank_account = frm.doc.bank_account;
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
        _show_effective_date_intro(frm);
    },

    effective_date(frm) {
        _show_effective_date_intro(frm);
    },

    status(frm) {
        _setup_buttons(frm);
    },

    cheque_type(frm) {
        _toggle_cheque_book_fields(frm);
        if (frm.doc.cheque_type === "Incoming") {
            frm.set_value("cheque_book", "");
            frm.set_value("cheque_leaf", "");
        } else if (frm.doc.docstatus === 0 && frm.doc.cheque_no && !frm.doc.cheque_leaf) {
            // Typed while the type was Incoming; Cheque No is read-only for an
            // outgoing cheque, so a wrong number could not be fixed from here.
            frm.set_value("cheque_no", "");
            frappe.show_alert({
                message: __("Cheque No cleared: pick the Cheque Leaf, or leave both blank for the next Unused leaf."),
                indicator: "orange",
            });
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
        if (frm.doc.docstatus !== 0) return;
        if (!frm.doc.cheque_book) {
            frm.set_value("cheque_leaf", "");
            return;
        }
        // A leaf from another book cannot be used; let the user pick again.
        if (frm.doc.cheque_leaf) {
            frappe.db.get_value("Cheque Leaf", frm.doc.cheque_leaf, "cheque_book", (r) => {
                if (r && r.cheque_book !== frm.doc.cheque_book) {
                    frm.set_value("cheque_leaf", "");
                }
            });
        }
    },

    // The leaf is the clerk's choice; its number follows it. Clearing the leaf
    // clears the number too, which is what asks the server for the next Unused
    // leaf on save.
    cheque_leaf(frm) {
        if (frm.doc.cheque_type !== "Outgoing" || frm.doc.docstatus !== 0) return;
        if (!frm.doc.cheque_leaf) {
            frm.set_value("cheque_no", "");
            return;
        }
        frappe.db.get_value(
            "Cheque Leaf",
            frm.doc.cheque_leaf,
            ["cheque_no", "cheque_book", "company", "bank_account"],
            (r) => {
                if (!r) return;
                if (
                    (frm.doc.company && r.company !== frm.doc.company) ||
                    (frm.doc.bank_account && r.bank_account !== frm.doc.bank_account)
                ) {
                    frm.set_value("cheque_leaf", "");
                    frappe.show_alert({
                        message: __("That leaf belongs to another company or bank account."),
                        indicator: "red",
                    });
                    return;
                }
                frm.set_value("cheque_no", r.cheque_no);
                if (!frm.doc.bank_account && r.bank_account) {
                    frm.set_value("bank_account", r.bank_account);
                }
                if (!frm.doc.cheque_book && r.cheque_book) {
                    frm.set_value("cheque_book", r.cheque_book);
                }
            }
        );
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
    // An outgoing cheque's number comes from its leaf. Leaving both blank is
    // how a clerk asks for the next Unused leaf, so the desk must not insist on
    // a number the server is about to fill in.
    frm.toggle_reqd("cheque_no", frm.doc.cheque_type !== "Outgoing");
}


function _show_effective_date_intro(frm) {
    if (frm.doc.effective_date) {
        frm.set_intro(
            __("The next action will be recorded as having happened on {0}.", [
                frappe.datetime.str_to_user(frm.doc.effective_date),
            ]),
            "orange"
        );
    } else {
        frm.set_intro("");
    }
}


// On a submitted cheque the workflow makes the form read-only to anyone outside
// the state's allow_edit role — the Accounts User about to Clear a Deposited
// cheque, for one — so the date is parked through its own endpoint instead.
function _show_effective_date_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Set Effective Date"),
        fields: [
            {
                fieldname: "effective_date",
                fieldtype: "Datetime",
                label: __("Effective Date"),
                default: frm.doc.effective_date,
                description: __("When the next action really happened. Leave blank to record it as now."),
            },
        ],
        primary_action_label: __("Set"),
        primary_action(values) {
            frappe
                .xcall("cheque_tracker.cheque_tracker.doctype.cheque.cheque.set_effective_date", {
                    cheque_name: frm.doc.name,
                    effective_date: values.effective_date || null,
                })
                .then(() => {
                    d.hide();
                    frm.reload_doc();
                });
        },
    });
    d.show();
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
    if (frm.doc.replacement_cheque) {
        frm.add_custom_button(__("View Replacement"), () => {
            frappe.set_route("Form", "Cheque", frm.doc.replacement_cheque);
        }, __("View"));
    }
    if (frm.doc.original_cheque) {
        frm.add_custom_button(__("View Original (Replaced)"), () => {
            frappe.set_route("Form", "Cheque", frm.doc.original_cheque);
        }, __("View"));
    }

    // v1.2 — Clear is reachable from more states now: Deposited or Endorsed
    // (incoming), Handed Over or Presented (outgoing).
    const incoming_pre_clear =
        frm.doc.cheque_type === "Incoming" && ["Deposited", "Endorsed"].includes(frm.doc.status);
    const outgoing_pre_clear =
        frm.doc.cheque_type === "Outgoing" && ["Handed Over", "Presented"].includes(frm.doc.status);
    if (incoming_pre_clear || outgoing_pre_clear) {
        _wrap_clear_action(frm);
    }

    if (frm.doc.status === "Bounced" && !frm.doc.replacement_cheque) {
        _wrap_replace_action(frm);
    }

    _wrap_v12_actions(frm);

    // frappe.model.can_write, not frm.perm: the workflow's read-only mode
    // strips write from frm.perm, which would hide this from the very user it
    // exists for.
    if (frappe.model.can_write(frm.doctype)) {
        frm.add_custom_button(__("Set Effective Date"), () => _show_effective_date_dialog(frm));
    }

    if (
        frm.doc.cheque_type === "Outgoing" &&
        frm.doc.cheque_book &&
        frm.doc.cheque_leaf &&
        frappe.user.has_role("System Manager")
    ) {
        frm.add_custom_button(__("Reassign Leaf"), () => _show_reassign_leaf_dialog(frm));
    }
}


// System Manager correction for a cheque recorded against the wrong leaf. The
// server frees the old leaf back to Unused, moves the cheque (and its number)
// to the new one, and logs the reason on the timeline.
function _show_reassign_leaf_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Reassign Leaf of {0}", [frm.doc.name]),
        fields: [
            {
                fieldtype: "HTML",
                options: `<p>${__(
                    "Currently on leaf {0} of {1}. The new leaf must be an Unused leaf of the same book; the current one goes back to Unused.",
                    [
                        frappe.utils.escape_html(frm.doc.cheque_no || ""),
                        frappe.utils.escape_html(frm.doc.cheque_book || ""),
                    ]
                )}</p>`,
            },
            {
                fieldname: "new_leaf",
                fieldtype: "Link",
                options: "Cheque Leaf",
                label: __("New Cheque Leaf"),
                reqd: 1,
                get_query: () => ({
                    filters: { cheque_book: frm.doc.cheque_book, leaf_status: "Unused" },
                }),
            },
            {
                fieldname: "reason",
                fieldtype: "Small Text",
                label: __("Reason"),
                reqd: 1,
            },
        ],
        primary_action_label: __("Reassign Leaf"),
        primary_action(values) {
            frappe
                .xcall("cheque_tracker.cheque_tracker.doctype.cheque.cheque.reassign_cheque_leaf", {
                    cheque_name: frm.doc.name,
                    new_leaf: values.new_leaf,
                    reason: values.reason,
                })
                .then((r) => {
                    d.hide();
                    frm.reload_doc();
                    frappe.show_alert({
                        message: __("Cheque {0} is now on leaf {1}.", [frm.doc.name, r.cheque_no]),
                        indicator: "green",
                    });
                });
        },
    });
    d.show();
}


// ------------------------------------------------------------------ //
//  v1.2 workflow actions that need fields filled first                //
// ------------------------------------------------------------------ //
//
// apply_workflow reloads the document from the database before running the
// transition (frappe/model/workflow.py:123), so anything the user typed on the
// form but did not save is discarded. Every field a transition depends on has to
// be persisted BEFORE the action fires — which is what this wrapper does, and
// why the Endorse / Bounce / Cash Clear buttons cannot simply rely on the user
// filling the section in first.
function _wrap_action_with_fields(frm, action, opts) {
    const $btn = frm.page.menu.find(`[data-label="${encodeURIComponent(action)}"]`);
    if (!$btn.length || $btn.data("ct-wrapped")) return;
    $btn.data("ct-wrapped", true);

    $btn.off("click").on("click", () => {
        if (opts.is_ready && opts.is_ready(frm)) {
            frappe
                .xcall("frappe.model.workflow.apply_workflow", { doc: frm.doc, action })
                .then(() => frm.reload_doc());
            return;
        }

        const d = new frappe.ui.Dialog({
            title: opts.title,
            fields: opts.fields(frm),
            primary_action_label: opts.primary_label || __("Confirm"),
            primary_action(values) {
                Object.assign(frm.doc, values);
                frm.save()
                    .then(() =>
                        frappe.xcall("frappe.model.workflow.apply_workflow", {
                            doc: frm.doc,
                            action,
                        })
                    )
                    .then(() => {
                        d.hide();
                        frm.reload_doc();
                    });
            },
        });
        d.show();
    });
}


function _wrap_v12_actions(frm) {
    // Endorsement (تظهير) — §4.3
    _wrap_action_with_fields(frm, "Endorse", {
        title: __("Endorse Cheque"),
        primary_label: __("Confirm Endorsement"),
        is_ready: (f) =>
            f.doc.endorsement_date &&
            f.doc.endorsed_to_party_type &&
            (f.doc.endorsed_to_party_type === "Other"
                ? f.doc.endorsed_to_other_name
                : f.doc.endorsed_to_party),
        fields: (f) => [
            {
                fieldname: "endorsed_to_party_type",
                fieldtype: "Select",
                label: __("Endorsed To Party Type"),
                options: ["", "Supplier", "Employee", "Other"],
                default: f.doc.endorsed_to_party_type || "Supplier",
                reqd: 1,
            },
            {
                fieldname: "endorsed_to_party",
                fieldtype: "Dynamic Link",
                label: __("Endorsed To"),
                options: "endorsed_to_party_type",
                default: f.doc.endorsed_to_party,
                depends_on: "eval:doc.endorsed_to_party_type && doc.endorsed_to_party_type!='Other'",
                mandatory_depends_on:
                    "eval:doc.endorsed_to_party_type && doc.endorsed_to_party_type!='Other'",
            },
            {
                fieldname: "endorsed_to_other_name",
                fieldtype: "Data",
                label: __("Endorsed To (Name)"),
                default: f.doc.endorsed_to_other_name,
                depends_on: "eval:doc.endorsed_to_party_type=='Other'",
                mandatory_depends_on: "eval:doc.endorsed_to_party_type=='Other'",
            },
            {
                fieldname: "endorsement_date",
                fieldtype: "Date",
                label: __("Endorsement Date"),
                default: f.doc.endorsement_date || frappe.datetime.get_today(),
                reqd: 1,
            },
        ],
    });

    // Bounce reason is mandatory server-side — §4.4
    _wrap_action_with_fields(frm, "Bounce", {
        title: __("Bounce Cheque"),
        primary_label: __("Record Bounce"),
        is_ready: (f) => !!f.doc.bounce_reason,
        fields: (f) => [
            {
                fieldname: "bounce_reason",
                fieldtype: "Select",
                label: __("Bounce Reason"),
                options: [
                    "",
                    "Insufficient Funds",
                    "Signature Mismatch",
                    "Account Closed",
                    "Technical",
                    "Other",
                ],
                default: f.doc.bounce_reason,
                reqd: 1,
            },
        ],
    });

    // Cash clearance — §4.2
    _wrap_action_with_fields(frm, "Cash Clear", {
        title: __("Clear Cheque in Cash"),
        primary_label: __("Confirm Clearing"),
        is_ready: (f) => f.doc.cash_account && f.doc.cleared_date,
        fields: (f) => [
            {
                fieldname: "cash_account",
                fieldtype: "Link",
                options: "Account",
                label: __("Cash Account"),
                default: f.doc.cash_account,
                reqd: 1,
                get_query: () => ({
                    filters: { company: f.doc.company, account_type: "Cash", is_group: 0 },
                }),
            },
            {
                fieldname: "cleared_date",
                fieldtype: "Date",
                label: __("Cleared Date"),
                default: f.doc.cleared_date || frappe.datetime.get_today(),
                reqd: 1,
            },
            ..._clearance_override_fields(f),
        ],
    });
}


// A cheque with no Payment Entry or Journal Entry behind it posts nothing when
// it clears, so the server refuses the transition. Only a System Manager can
// waive that, and only with a reason — which is recorded on the timeline. The
// fields appear only when they are actually needed, so the normal path is
// unchanged.
function _clearance_override_fields(frm) {
    const has_accounting_doc =
        (["Payment Entry", "Journal Entry"].includes(frm.doc.reference_doctype) &&
            frm.doc.reference_name) ||
        frm.doc.clearance_je;

    if (has_accounting_doc) return [];
    if (!frappe.user.has_role("System Manager")) return [];

    return [
        { fieldtype: "Section Break" },
        {
            fieldtype: "HTML",
            options: `<div class="alert alert-warning" style="margin-bottom:10px">${__(
                "This cheque has no linked Payment Entry or Journal Entry. Clearing it will post nothing to the ledger."
            )}</div>`,
        },
        {
            fieldname: "clearance_override",
            fieldtype: "Check",
            label: __("Clear Without Accounting Document"),
            default: 0,
        },
        {
            fieldname: "clearance_override_reason",
            fieldtype: "Small Text",
            label: __("Override Reason"),
            depends_on: "eval:doc.clearance_override",
            mandatory_depends_on: "eval:doc.clearance_override",
        },
    ];
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
                ..._clearance_override_fields(frm),
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


// Patch the workflow's "Replace" action so clicking it opens a dialog
// offering "Link Existing Draft" or "Create New". Both paths set up the
// bidirectional original_cheque ↔ replacement_cheque link and apply the
// Replace workflow transition.
function _wrap_replace_action(frm) {
    const $btn = frm.page.menu.find(
        `[data-label="${encodeURIComponent("Replace")}"]`
    );
    if (!$btn.length || $btn.data("ct-wrapped")) return;
    $btn.data("ct-wrapped", true);

    $btn.off("click").on("click", () => _show_replace_dialog(frm));
}


function _show_replace_dialog(frm) {
    const isOutgoing = frm.doc.cheque_type === "Outgoing";

    const fields = [
        {
            fieldname: "mode",
            fieldtype: "Select",
            label: __("Replacement Source"),
            options: ["Create New", "Link Existing Draft"],
            default: "Create New",
            reqd: 1,
            onchange() {
                const mode = d.get_value("mode");
                d.set_df_property("existing_cheque", "hidden", mode !== "Link Existing Draft");
                d.set_df_property("new_section", "hidden", mode !== "Create New");
            },
        },

        // === Link Existing path ===
        {
            fieldname: "existing_cheque",
            fieldtype: "Link",
            options: "Cheque",
            label: __("Existing Draft Cheque"),
            hidden: 1,
            get_query: () => ({
                filters: {
                    docstatus: 0,
                    cheque_type: frm.doc.cheque_type,
                    party_type: frm.doc.party_type,
                    party: frm.doc.party,
                    reference_doctype: frm.doc.reference_doctype || "",
                    reference_name: frm.doc.reference_name || "",
                },
            }),
            description: __("Filtered to drafts with same type, party, and reference."),
        },

        // === Create New path ===
        { fieldname: "new_section", fieldtype: "Section Break", label: __("New Cheque Details") },
        {
            fieldname: "cheque_no", fieldtype: "Data", label: __("New Cheque No"),
            description: __("The number on the replacement cheque"),
        },
        { fieldname: "issue_date", fieldtype: "Date", label: __("Issue Date"),
          default: frappe.datetime.get_today() },
        { fieldname: "due_date", fieldtype: "Date", label: __("Due Date"),
          default: frappe.datetime.get_today() },
        { fieldname: "amount", fieldtype: "Currency", label: __("Amount"),
          default: frm.doc.amount,
          description: __("Defaults to original amount; change if replacement differs.") },
        { fieldname: "drawee_bank", fieldtype: "Link", options: "Bank",
          label: __("Drawee Bank"), default: frm.doc.drawee_bank },
    ];

    if (isOutgoing) {
        fields.push(
            { fieldname: "cheque_book", fieldtype: "Link", options: "Cheque Book",
              label: __("Cheque Book"),
              get_query: () => ({ filters: { status: "Active", company: frm.doc.company } }) },
            { fieldname: "cheque_leaf", fieldtype: "Link", options: "Cheque Leaf",
              label: __("Cheque Leaf"),
              get_query: () => ({
                  filters: {
                      cheque_book: d.get_value("cheque_book"),
                      leaf_status: "Unused",
                  },
              }) }
        );
    }

    const d = new frappe.ui.Dialog({
        title: __("Replace Cheque {0}", [frm.doc.name]),
        fields,
        primary_action_label: __("Replace"),
        primary_action(values) {
            const mode = values.mode;
            let call;
            if (mode === "Link Existing Draft") {
                if (!values.existing_cheque) {
                    frappe.throw(__("Select a draft cheque to link."));
                    return;
                }
                call = frm.call("link_replacement",
                    { replacement_name: values.existing_cheque });
            } else {
                // An outgoing replacement's number is its leaf's; only an
                // incoming one needs the number typed in.
                if ((!isOutgoing && !values.cheque_no) || !values.issue_date || !values.due_date) {
                    frappe.throw(__("Cheque No, Issue Date, and Due Date are required."));
                    return;
                }
                if (isOutgoing && (!values.cheque_book || !values.cheque_leaf)) {
                    frappe.throw(__("Cheque Book and Leaf are required for Outgoing replacements."));
                    return;
                }
                call = frm.call("create_replacement", {
                    cheque_no: values.cheque_no,
                    issue_date: values.issue_date,
                    due_date: values.due_date,
                    drawee_bank: values.drawee_bank,
                    cheque_book: values.cheque_book,
                    cheque_leaf: values.cheque_leaf,
                    amount: values.amount,
                });
            }
            call.then((r) => {
                d.hide();
                frm.reload_doc();
                if (mode === "Create New" && r.message) {
                    frappe.show_alert({
                        message: __("Replacement created: {0}", [r.message]),
                        indicator: "green",
                    });
                    setTimeout(() => frappe.set_route("Form", "Cheque", r.message), 800);
                }
            });
        },
    });
    d.show();
}
