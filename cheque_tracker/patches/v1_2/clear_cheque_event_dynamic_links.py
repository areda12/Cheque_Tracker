import frappe


def execute():
    """
    G2 cleanup: Cheque Event.reference_name was changed from Dynamic Link
    to Data. Existing entries in tabDynamic Link for parenttype='Cheque Event'
    will otherwise persist and continue to block cancellation of referenced
    docs. Delete them.

    Safe to re-run: the WHERE clause matches only stale entries that no
    longer have a corresponding Dynamic Link field.
    """
    frappe.db.sql(
        "DELETE FROM `tabDynamic Link` WHERE parenttype = 'Cheque Event'"
    )
    frappe.db.commit()
