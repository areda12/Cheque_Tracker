frappe.query_reports["Deposited Not Cleared"] = {
    filters: [
        { fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_default("company"), reqd: 1 },
    ],
};
