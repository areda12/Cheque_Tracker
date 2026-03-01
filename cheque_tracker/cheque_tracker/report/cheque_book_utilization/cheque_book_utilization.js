frappe.query_reports["Cheque Book Utilization"] = {
    filters: [
        { fieldname: "company", label: __("Company"), fieldtype: "Link",   options: "Company", default: frappe.defaults.get_default("company") },
        { fieldname: "status",  label: __("Status"),  fieldtype: "Select", options: "\nDraft\nActive\nExhausted\nCancelled" },
    ],
};
