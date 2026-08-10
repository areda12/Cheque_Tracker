"""Payment Entry → Cheque mirroring (BUILD_INSTRUCTIONS §4.5.1).

EEI's confirmed model: **the Payment Entry stays DRAFT until the cheque is
actually collected**. The receivable stays in Debtors until clearance and the
tracker is the source of truth for the cheque itself.

This module keeps the two in step in one direction only — PE to Cheque:

* a draft Payment Entry with `mode_of_payment = Cheque` spawns a Draft Cheque
  prefilled from it, exactly once;
* while both are still drafts, edits to the PE's amount / dates / cheque number
  flow onto the Cheque.

Nothing here posts to the general ledger. The settlement direction (clearing the
cheque submits the PE) lives in `cheque_financial.settle_linked_payment_entry`.
"""

import frappe
from frappe import _
from frappe.utils import flt

CHEQUE_MODE = "Cheque"

# payment_type → cheque_type. A PE that receives money is backed by a cheque we
# were given; one that pays out is backed by a cheque we wrote.
_DIRECTION = {"Receive": "Incoming", "Pay": "Outgoing"}

# Fields kept in step while BOTH documents are still drafts.
_SYNCED_FIELDS = ("amount", "due_date", "cheque_no", "party", "party_type", "currency")


def on_payment_entry_update(doc, method=None):
    """doc_events hook. Cheap no-ops dominate, so bail out early and often."""
    if doc.docstatus != 0:
        # Submitted / cancelled PEs are not mirrored: a submitted PE has already
        # posted, which is not the model this app implements.
        return

    if (doc.mode_of_payment or "") != CHEQUE_MODE:
        return

    if doc.payment_type not in _DIRECTION:
        # Internal Transfer — no party, no cheque.
        return

    if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_import:
        return

    existing = _linked_cheque(doc.name)
    if existing:
        _sync_draft_cheque(existing, doc)
        return

    _create_draft_cheque(doc)


def _linked_cheque(payment_entry):
    """The one Cheque that points at this PE, if any.

    Cancelled cheques are excluded so a cancel-and-redo cycle can spawn a fresh
    one, but any Draft or submitted cheque counts — that is the idempotency
    guarantee §4.5.1 asks for.
    """
    return frappe.db.get_value(
        "Cheque",
        {
            "reference_doctype": "Payment Entry",
            "reference_name": payment_entry,
            "docstatus": ["<", 2],
        },
        "name",
    )


def _bank_account_for(doc):
    """The company Bank Account behind the PE's bank leg."""
    account = doc.paid_to if doc.payment_type == "Receive" else doc.paid_from
    if not account:
        return None
    return frappe.db.get_value(
        "Bank Account", {"account": account, "is_company_account": 1}, "name"
    )


def _create_draft_cheque(doc):
    cheque_type = _DIRECTION[doc.payment_type]

    cheque = frappe.new_doc("Cheque")
    cheque.cheque_type = cheque_type
    cheque.company = doc.company
    cheque.party_type = doc.party_type
    cheque.party = doc.party
    cheque.amount = flt(doc.paid_amount)
    cheque.currency = doc.paid_from_account_currency or doc.paid_to_account_currency
    cheque.cheque_no = doc.reference_no
    cheque.due_date = doc.reference_date
    cheque.bank_account = _bank_account_for(doc)
    cheque.reference_doctype = "Payment Entry"
    cheque.reference_name = doc.name

    if cheque_type == "Incoming":
        cheque.received_date = doc.posting_date
    else:
        cheque.issue_date = doc.posting_date

    missing = _missing_requirements(cheque)
    if missing:
        # Never block the Payment Entry: the accountant is mid-edit and the
        # cheque number or drawee bank may simply not be known yet. Say so once
        # and try again on the next save.
        frappe.msgprint(
            _("No Cheque was created for {0} yet — still missing: {1}.").format(
                doc.name, ", ".join(missing)
            ),
            title=_("Cheque Tracker"),
            indicator="orange",
        )
        return None

    cheque.flags.ignore_permissions = True

    # A problem in the tracker must never block the accountant's Payment Entry.
    # The PE is the document that matters to the ledger; a missing mirror record
    # is an inconvenience, a failed save is lost work. Roll back to a savepoint so
    # the failed insert cannot leave a half-written cheque behind, then say so
    # loudly — silently skipping would let cheques go untracked.
    savepoint = "ct_pe_autocreate"
    frappe.db.savepoint(savepoint)
    try:
        cheque.insert(ignore_permissions=True)
    except Exception as exc:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(
            title="Cheque Tracker: auto-create from Payment Entry failed",
            message=f"Payment Entry {doc.name}: {frappe.get_traceback(with_context=True)}",
        )
        frappe.msgprint(
            _("Could not create a Cheque for {0}: {1}").format(doc.name, exc),
            title=_("Cheque Tracker"),
            indicator="red",
        )
        return None

    frappe.msgprint(
        _("Draft Cheque {0} created for this Payment Entry.").format(
            frappe.utils.get_link_to_form("Cheque", cheque.name)
        ),
        title=_("Cheque Tracker"),
        indicator="blue",
    )
    return cheque.name


def _missing_requirements(cheque):
    """Fields the Cheque doctype requires that the PE may not carry yet."""
    missing = []
    if not cheque.cheque_no:
        missing.append(_("Reference No (cheque number)"))
    if not cheque.due_date:
        missing.append(_("Reference Date (cheque due date)"))
    if not cheque.party:
        missing.append(_("Party"))
    if cheque.cheque_type == "Outgoing" and not cheque.cheque_book:
        # Outgoing cheques draw a leaf from a Cheque Book, which the PE cannot
        # know about. Those are created from the Cheque side instead.
        missing.append(_("Cheque Book (create the outgoing cheque from the Cheque form)"))
    return missing


def _sync_draft_cheque(cheque_name, doc):
    """Mirror edits from a draft PE onto its still-Draft Cheque."""
    cheque = frappe.get_doc("Cheque", cheque_name)
    if cheque.docstatus != 0:
        # Submitted cheque: the tracker is now the source of truth for the
        # cheque's own particulars. Amount drift is caught by
        # cheque_financial.validate_payment_entry_link on the next Cheque save.
        return

    desired = {
        "amount": flt(doc.paid_amount),
        "due_date": doc.reference_date,
        "cheque_no": doc.reference_no,
        "party_type": doc.party_type,
        "party": doc.party,
    }
    # An outgoing cheque's number comes from its reserved leaf, not the PE.
    if cheque.cheque_type == "Outgoing":
        desired.pop("cheque_no")

    changed = {
        field: value
        for field, value in desired.items()
        if value and cheque.get(field) != value
    }
    if not changed:
        return

    cheque.update(changed)
    cheque.flags.ignore_permissions = True
    cheque.save(ignore_permissions=True)
