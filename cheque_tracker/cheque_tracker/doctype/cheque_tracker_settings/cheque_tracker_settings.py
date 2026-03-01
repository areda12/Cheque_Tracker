# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

import frappe
from frappe.model.document import Document


class ChequeTrackerSettings(Document):
    pass


def get_settings():
    """Return the singleton Cheque Tracker Settings doc (cached)."""
    return frappe.get_cached_doc("Cheque Tracker Settings")
