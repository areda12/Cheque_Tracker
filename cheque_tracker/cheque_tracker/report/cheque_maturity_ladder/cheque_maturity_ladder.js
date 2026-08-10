frappe.query_reports["Cheque Maturity Ladder"] = {
    filters: [
        { fieldname: "company",     label: __("Company"),     fieldtype: "Link",   options: "Company", default: frappe.defaults.get_default("company"), reqd: 1 },
        { fieldname: "from_date",   label: __("From Date"),   fieldtype: "Date",   default: frappe.datetime.month_start() },
        { fieldname: "to_date",     label: __("To Date"),     fieldtype: "Date",   default: frappe.datetime.add_months(frappe.datetime.month_end(), 11) },
        { fieldname: "cheque_type", label: __("Type"),        fieldtype: "Select", options: "\nIncoming\nOutgoing" },
    ],
};
