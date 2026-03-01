frappe.query_reports["Bounced Cheques Register"] = {
    filters: [
        { fieldname: "company",    label: __("Company"),    fieldtype: "Link",   options: "Company", default: frappe.defaults.get_default("company") },
        { fieldname: "cheque_type",label: __("Type"),       fieldtype: "Select", options: "\nIncoming\nOutgoing" },
        { fieldname: "from_date",  label: __("From Date"),  fieldtype: "Date" },
        { fieldname: "to_date",    label: __("To Date"),    fieldtype: "Date" },
        { fieldname: "party",      label: __("Party"),      fieldtype: "Data" },
    ],
};
