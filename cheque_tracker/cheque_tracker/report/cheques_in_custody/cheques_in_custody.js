frappe.query_reports["Cheques in Custody"] = {
    filters: [
        { fieldname: "company",     label: __("Company"), fieldtype: "Link",   options: "Company", default: frappe.defaults.get_default("company") },
        { fieldname: "cheque_type", label: __("Type"),    fieldtype: "Select", options: "\nIncoming\nOutgoing" },
        { fieldname: "holder",      label: __("Holder"),  fieldtype: "Data",   description: __("Matches a Current Holder user id exactly, or any part of an External Holder.") },
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        // A cheque nobody has moved in two months is the failure this report
        // exists to surface, so it is coloured rather than left to be counted.
        if (column.fieldname === "age_days" && data && data.age_days >= 60) {
            value = `<span style="color: var(--red-500); font-weight: 600;">${value}</span>`;
        }
        return value;
    },
};
