# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT

"""
Patch v1.0: add composite unique index on (cheque_book, cheque_no)
in tabCheque Leaf.

This is the database-level guard that makes the app-level check in
ChequeLeaf.before_insert() redundant under concurrent load.
"""

import frappe


def execute():
    try:
        frappe.db.sql(
            """
            ALTER TABLE `tabCheque Leaf`
            ADD UNIQUE INDEX `unique_book_cheque_no`
                (cheque_book(140), cheque_no(100))
            """
        )
        frappe.db.commit()
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate key name" in msg or "already exists" in msg:
            pass  # idempotent – index already in place
        else:
            raise
