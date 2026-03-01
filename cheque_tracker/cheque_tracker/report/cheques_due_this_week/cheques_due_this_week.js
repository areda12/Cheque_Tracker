frappe.query_reports["Cheques Due This Week"] = {
    filters: [
        { fieldname: "company",    label: __("Company"),    fieldtype: "Link",   options: "Company", default: frappe.defaults.get_default("company") },
        { fieldname: "cheque_type",label: __("Type"),       fieldtype: "Select", options: "\nIncoming\nOutgoing" },
        { fieldname: "status",     label: __("Status"),     fieldtype: "Select", options: "\nDraft\nReceived\nIn Safe\nDeposited\nPresented\nCleared\nBounced\nReturned\nCancelled\nReplaced" },
        { fieldname: "from_date",  label: __("From Date"),  fieldtype: "Date",   default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "to_date",    label: __("To Date"),    fieldtype: "Date",   default: frappe.datetime.add_days(frappe.datetime.get_today(), 7), reqd: 1 },
        { fieldname: "party",      label: __("Party"),      fieldtype: "Data" },
    ],
};
