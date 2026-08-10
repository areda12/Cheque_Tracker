frappe.query_reports["Bounce Rate by Customer"] = {
    filters: [
        { fieldname: "company",   label: __("Company"),   fieldtype: "Link", options: "Company", default: frappe.defaults.get_default("company") },
        { fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_months(frappe.datetime.get_today(), -12) },
        { fieldname: "to_date",   label: __("To Date"),   fieldtype: "Date", default: frappe.datetime.get_today() },
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        // One in five cheques bouncing is a credit-hold conversation, not a
        // number to scroll past.
        if (column.fieldname === "bounce_pct" && data && data.bounce_pct >= 20) {
            value = `<span style="color: var(--red-500); font-weight: 600;">${value}</span>`;
        }
        return value;
    },
};
