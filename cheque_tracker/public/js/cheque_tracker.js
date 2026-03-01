// ─── Cheque Tracker – global client scripts ───────────────────────────────
frappe.provide("cheque_tracker");

cheque_tracker.STATUS_COLORS = {
    "Draft":     "grey",
    "Received":  "blue",
    "In Safe":   "purple",
    "Deposited": "yellow",
    "Presented": "orange",
    "Cleared":   "green",
    "Bounced":   "red",
    "Returned":  "red",
    "Cancelled": "darkgrey",
    "Replaced":  "lightblue",
};

cheque_tracker.refresh_indicator = function (frm) {
    if (frm.doc.status) {
        frm.page.set_indicator(
            frm.doc.status,
            cheque_tracker.STATUS_COLORS[frm.doc.status] || "grey"
        );
    }
};

cheque_tracker.prompt_and_call = function (frm, label, method, extra_fields, on_success) {
    extra_fields = extra_fields || [];
    frappe.prompt(
        [{ label: __("Notes"), fieldtype: "Small Text", fieldname: "notes" }].concat(extra_fields),
        function (values) {
            frappe.call({
                method: method,
                args: Object.assign({ cheque_name: frm.doc.name }, values),
                callback: function (r) {
                    if (!r.exc) {
                        frm.reload_doc();
                        if (on_success) on_success(r);
                    }
                },
            });
        },
        __(label),
        __("Confirm")
    );
};

// ─── Cheque Form ──────────────────────────────────────────────────────────
frappe.ui.form.on("Cheque", {
    refresh: function (frm) {
        cheque_tracker.refresh_indicator(frm);

        // Outgoing cheque_no is system-controlled → make it visually read-only
        frm.set_df_property(
            "cheque_no", "read_only",
            frm.doc.cheque_type === "Outgoing" ? 1 : 0
        );

        if (frm.doc.docstatus === 1 && !frm.doc.__islocal) {

            // ── Hand Over ──────────────────────────────────────────────
            if (frappe.user.has_role(["Treasury User", "System Manager"])) {
                frm.add_custom_button(__("Hand Over"), function () {
                    frappe.prompt(
                        [
                            {
                                label: __("To User"),
                                fieldtype: "Link",
                                options: "User",
                                fieldname: "to_user",
                                reqd: 1,
                            },
                            {
                                label: __("Location"),
                                fieldtype: "Data",
                                fieldname: "location",
                            },
                            {
                                label: __("Notes"),
                                fieldtype: "Small Text",
                                fieldname: "notes",
                            },
                        ],
                        function (values) {
                            frappe.call({
                                method: "cheque_tracker.cheque_tracker.doctype.cheque.cheque.hand_over_cheque",
                                args: {
                                    cheque_name: frm.doc.name,
                                    to_user: values.to_user,
                                    location: values.location || "",
                                    notes: values.notes || "",
                                },
                                callback: function (r) {
                                    if (!r.exc) frm.reload_doc();
                                },
                            });
                        },
                        __("Hand Over Cheque"),
                        __("Confirm")
                    );
                }, __("Actions"));
            }

            // ── Status transitions ─────────────────────────────────────
            const s = frm.doc.status;
            const tu = frappe.user.has_role(["Treasury User", "System Manager"]);
            const au = frappe.user.has_role(["Accounts User", "System Manager"]);

            if (tu && s === "Received") {
                frm.add_custom_button(__("Move to Safe"), () =>
                    cheque_tracker._transition(frm, "In Safe"), __("Actions"));
                frm.add_custom_button(__("Deposit"), () =>
                    cheque_tracker._transition(frm, "Deposited"), __("Actions"));
                frm.add_custom_button(__("Return"), () =>
                    cheque_tracker._transition(frm, "Returned", true), __("Actions"));
            }
            if (tu && s === "In Safe") {
                frm.add_custom_button(__("Deposit"), () =>
                    cheque_tracker._transition(frm, "Deposited"), __("Actions"));
                frm.add_custom_button(__("Return"), () =>
                    cheque_tracker._transition(frm, "Returned", true), __("Actions"));
            }
            if (tu && (s === "Deposited" || s === "Presented")) {
                frm.add_custom_button(__("Bounce"), () =>
                    cheque_tracker._transition(frm, "Bounced", true), __("Actions"));
            }
            if (tu && s === "Deposited") {
                frm.add_custom_button(__("Present"), () =>
                    cheque_tracker._transition(frm, "Presented"), __("Actions"));
            }
            if (au && (s === "Deposited" || s === "Presented")) {
                frm.add_custom_button(__("Clear"), () =>
                    cheque_tracker._transition(frm, "Cleared"), __("Actions"));
            }
            if (tu && s === "Bounced") {
                frm.add_custom_button(__("Replace"), () =>
                    cheque_tracker._transition(frm, "Replaced"), __("Actions"));
            }
        }
    },

    cheque_type: function (frm) {
        frm.set_df_property(
            "cheque_no", "read_only",
            frm.doc.cheque_type === "Outgoing" ? 1 : 0
        );
        frm.set_df_property(
            "cheque_book", "reqd",
            frm.doc.cheque_type === "Outgoing" ? 1 : 0
        );
    },
});

cheque_tracker._transition = function (frm, new_status, require_notes) {
    const fields = [];
    if (require_notes) {
        fields.push({ label: __("Notes / Reason"), fieldtype: "Small Text", fieldname: "notes", reqd: 1 });
    } else {
        fields.push({ label: __("Notes (optional)"), fieldtype: "Small Text", fieldname: "notes" });
    }
    frappe.prompt(
        fields,
        function (values) {
            frappe.call({
                method: "cheque_tracker.cheque_tracker.doctype.cheque.cheque.change_cheque_status",
                args: {
                    cheque_name: frm.doc.name,
                    new_status: new_status,
                    notes: values.notes || "",
                },
                callback: function (r) {
                    if (!r.exc) frm.reload_doc();
                },
            });
        },
        __("Confirm: ") + __(new_status),
        __("Confirm")
    );
};

// ─── Cheque Book Form ──────────────────────────────────────────────────────
frappe.ui.form.on("Cheque Book", {
    onload: function (frm) {
        frm.trigger("_recalc");
    },
    start_cheque_no: function (frm) { frm.trigger("_recalc"); },
    end_cheque_no:   function (frm) { frm.trigger("_recalc"); },
    digits_count:    function (frm) { frm.trigger("_recalc"); },
    prefix:          function (frm) { frm.trigger("_recalc"); },
    suffix:          function (frm) { frm.trigger("_recalc"); },
    sequence_type:   function (frm) { frm.trigger("_recalc"); },

    _recalc: function (frm) {
        const s = frm.doc.start_cheque_no;
        const e = frm.doc.end_cheque_no;
        if (frm.doc.sequence_type === "Numeric" && s && e) {
            const si = parseInt(s), ei = parseInt(e);
            if (!isNaN(si) && !isNaN(ei) && ei >= si) {
                frm.set_value("leaves_count", ei - si + 1);
            }
        }
        if (frm.doc.sequence_type === "Alphanumeric Pattern") {
            const d = frm.doc.digits_count || 6;
            const sample = "1".padStart(d, "0");
            frm.set_value(
                "pattern_example",
                `${frm.doc.prefix || ""}${sample}${frm.doc.suffix || ""}`
            );
        }
    },
});
