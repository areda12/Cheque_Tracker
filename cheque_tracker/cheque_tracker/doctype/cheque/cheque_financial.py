# Copyright (c) 2024, Ahmed Abbas and contributors
# License: MIT
"""
Cheque Tracker accounting helpers.

v1.2 model (BUILD_INSTRUCTIONS §4.5 — Ahmed's confirmed decision)
-----------------------------------------------------------------
**The Payment Entry stays DRAFT until the cheque is actually collected.** The
receivable stays in `Debtors` until clearance; the tracker is the source of
truth for the cheque itself.

Consequently **the tracker posts nothing to the general ledger** (§4.5.5). The
linked Payment Entry is the only posting document, and it posts once, when the
cheque clears and the PE is submitted.

That is a deliberate reversal of the v1.1.x "clearance-only" model, which posted
its own Journal Entry (Dr Bank / Cr Debtors) on the Clear action. Keeping both
would have double-posted every collection: the JE and the submitted Payment
Entry describe the same event. `make_clearance_je` is therefore gone;
`cancel_clearance_je` stays, because cheques cleared under v1.1.x still carry a
`clearance_je` link that must be unwound if they are ever cancelled.

Because all posting moved to the Payment Entry, a cheque with nothing linked
would clear with no ledger effect whatsoever — recorded as collected while the
books never hear about it. That is **refused**, not warned about: a warning on a
screen is not a control, and an untracked collection is exactly the kind of thing
that gets discovered at year end. See `validate_clearance_has_accounting_document`.

A System Manager can still clear such a cheque deliberately by ticking
`clearance_override` and giving a reason; the override is recorded as a Cheque
Event naming them.
"""

import frappe
from frappe import _


# Fields whose disagreement between a Cheque and its Payment Entry is a hard
# error rather than a warning.
_AMOUNT_TOLERANCE = 0.005

# A cheque that clears must be backed by something that posts. `reference_doctype`
# is a plain Link to DocType and can legitimately point at a Sales Invoice or a
# Delivery Note — neither of which posts on clearance — so only these two count.
ACCOUNTING_DOCTYPES = ("Payment Entry", "Journal Entry")

# Who may waive that requirement.
CLEARANCE_OVERRIDE_ROLE = "System Manager"


def linked_accounting_document(cheque):
    """The document that will post when this cheque clears, or None.

    Accepts either a Cheque Document or any dict-like view of one, because the
    UI transition path validates before the status is written and passes a
    shadow copy.
    """
    reference_doctype = cheque.get("reference_doctype")
    reference_name = cheque.get("reference_name")
    if reference_doctype in ACCOUNTING_DOCTYPES and reference_name:
        if frappe.db.exists(reference_doctype, reference_name):
            return (reference_doctype, reference_name)

    # Cheques cleared under v1.1.x carry their own Journal Entry.
    clearance_je = cheque.get("clearance_je")
    if clearance_je and frappe.db.exists("Journal Entry", clearance_je):
        return ("Journal Entry", clearance_je)

    return None


def validate_clearance_override(cheque):
    """Only a System Manager may waive the accounting-document requirement.

    Checked on every save rather than only at clearance, so the flag cannot be
    quietly set by a Treasury user in advance and then relied on later.
    """
    if not cheque.get("clearance_override"):
        return

    if CLEARANCE_OVERRIDE_ROLE not in frappe.get_roles(frappe.session.user):
        frappe.throw(
            _(
                "Only a {0} can allow a cheque to clear without an accounting document."
            ).format(CLEARANCE_OVERRIDE_ROLE),
            frappe.PermissionError,
            title=_("Override not permitted"),
        )

    if not (cheque.get("clearance_override_reason") or "").strip():
        frappe.throw(
            _("Give a reason for clearing without an accounting document."),
            frappe.ValidationError,
        )


def validate_clearance_has_accounting_document(cheque):
    """Block a clearance that would post nothing.

    v1.2 moved all posting to the linked Payment Entry (DECISIONS.md D2). A
    cheque with nothing linked therefore clears without any ledger effect at
    all — the money is recorded as collected and the books never hear about it.
    That used to be a warning; it is now refused, because a warning on a screen
    is not a control.

    A System Manager can still override deliberately (`clearance_override` plus a
    reason), which is logged as a Cheque Event naming them.
    """
    if linked_accounting_document(cheque):
        return

    if cheque.get("clearance_override"):
        validate_clearance_override(cheque)
        return

    frappe.throw(
        _(
            "Cheque {0} has no linked Payment Entry or Journal Entry, so clearing it "
            "would post nothing to the ledger.<br><br>"
            "Link the Payment Entry that records this collection, or — if the "
            "accounting really is handled elsewhere — a {1} can tick "
            "<b>Clear Without Accounting Document</b> and give a reason."
        ).format(cheque.get("name") or _("(new)"), CLEARANCE_OVERRIDE_ROLE),
        frappe.ValidationError,
        title=_("Nothing would be posted"),
    )


def validate_payment_entry_link(cheque):
    """§4.5.3 — sanity-check a Cheque against the Payment Entry it points at.

    Amount mismatch throws: the PE is what will hit the ledger, so letting the
    two drift means the cheque record stops describing the money that moved.
    Party and cheque number mismatches only warn — a payment can legitimately be
    keyed against a group party, and the bank's reference formatting varies.
    """
    if cheque.reference_doctype != "Payment Entry" or not cheque.reference_name:
        return

    pe = frappe.db.get_value(
        "Payment Entry",
        cheque.reference_name,
        ["paid_amount", "party", "party_type", "reference_no", "docstatus", "company"],
        as_dict=True,
    )
    if not pe:
        return

    if abs(frappe.utils.flt(pe.paid_amount) - frappe.utils.flt(cheque.amount)) > _AMOUNT_TOLERANCE:
        frappe.throw(
            _(
                "Amount mismatch: Cheque {0} is {1} but Payment Entry {2} is {3}. "
                "The Payment Entry is what posts to the ledger — correct one of them."
            ).format(
                cheque.name or _("(new)"),
                frappe.utils.fmt_money(cheque.amount, currency=cheque.currency),
                cheque.reference_name,
                frappe.utils.fmt_money(pe.paid_amount, currency=cheque.currency),
            ),
            frappe.ValidationError,
            title=_("Cheque does not match its Payment Entry"),
        )

    if pe.party and cheque.party and pe.party != cheque.party:
        frappe.msgprint(
            _("Party differs: Cheque {0} is {1}, Payment Entry {2} is {3}.").format(
                cheque.name or _("(new)"), cheque.party, cheque.reference_name, pe.party
            ),
            indicator="orange",
            alert=True,
        )

    if pe.reference_no and cheque.cheque_no and pe.reference_no != cheque.cheque_no:
        frappe.msgprint(
            _("Cheque No differs: Cheque {0} is {1}, Payment Entry reference is {2}.").format(
                cheque.name or _("(new)"), cheque.cheque_no, pe.reference_no
            ),
            indicator="orange",
            alert=True,
        )


def validate_duplicate_cheque(cheque):
    """§4.5.4 — one physical cheque, one record.

    Keyed on cheque_no + drawee_bank + cheque_type, ignoring cancelled records
    so a corrected re-entry is still possible.
    """
    if not cheque.cheque_no or not cheque.cheque_type:
        return

    if not cheque.drawee_bank:
        # The key §4.5.4 defines is cheque_no + drawee_bank + cheque_type. Without
        # a drawee bank that key is incomplete, and matching on the number alone
        # would reject legitimate cheques: two banks hand out cheque number 9100
        # all the time. Incoming cheques always have one (before_save requires
        # it); Outgoing cheques that do not are already protected by the unique
        # index on Cheque Leaf (cheque_book + cheque_no), which is the stronger
        # guarantee anyway.
        return

    filters = {
        "cheque_no": cheque.cheque_no,
        "cheque_type": cheque.cheque_type,
        "drawee_bank": cheque.drawee_bank,
        "docstatus": ["<", 2],
    }
    if cheque.name:
        filters["name"] = ["!=", cheque.name]

    existing = frappe.db.get_value("Cheque", filters, ["name", "status"], as_dict=True)
    if not existing:
        return

    frappe.throw(
        _(
            "Cheque {0} already records cheque number {1} on {2} ({3}, currently {4}). "
            "Cancel that record first if this is a correction."
        ).format(
            existing.name,
            cheque.cheque_no,
            cheque.drawee_bank or _("(no drawee bank)"),
            cheque.cheque_type,
            existing.status,
        ),
        frappe.DuplicateEntryError,
        title=_("Duplicate cheque"),
    )


def validate_endorsement(cheque):
    """§4.3 — an endorsed cheque must name who it was endorsed to."""
    if cheque.status != "Endorsed":
        return

    if cheque.cheque_type != "Incoming":
        frappe.throw(_("Only Incoming cheques can be endorsed."), frappe.ValidationError)

    if not cheque.endorsed_to_party_type:
        frappe.throw(
            _("Endorsed To Party Type is required to endorse a cheque."),
            frappe.ValidationError,
        )

    if cheque.endorsed_to_party_type == "Other":
        if not cheque.endorsed_to_other_name:
            frappe.throw(
                _("Endorsed To (Name) is required when the counterparty is 'Other'."),
                frappe.ValidationError,
            )
    elif not cheque.endorsed_to_party:
        frappe.throw(
            _("Endorsed To is required to endorse a cheque."), frappe.ValidationError
        )

    if not cheque.endorsement_date:
        frappe.throw(_("Endorsement Date is required."), frappe.ValidationError)

    _validate_endorsement_payment_entry(cheque)


def _validate_endorsement_payment_entry(cheque):
    """The endorsement PE is not created for us (§4.3), but if someone links one
    its amount must agree — an endorsement that pays a different sum than the
    cheque is worth is a data-entry error."""
    if not cheque.endorsement_payment_entry:
        return

    pe = frappe.db.get_value(
        "Payment Entry", cheque.endorsement_payment_entry, ["paid_amount", "docstatus"], as_dict=True
    )
    if not pe:
        return

    if abs(frappe.utils.flt(pe.paid_amount) - frappe.utils.flt(cheque.amount)) > _AMOUNT_TOLERANCE:
        frappe.throw(
            _(
                "Endorsement Payment Entry {0} is for {1}, but this cheque is {2}."
            ).format(
                cheque.endorsement_payment_entry,
                frappe.utils.fmt_money(pe.paid_amount, currency=cheque.currency),
                frappe.utils.fmt_money(cheque.amount, currency=cheque.currency),
            ),
            frappe.ValidationError,
        )


def validate_bounce(cheque):
    """§4.4 — a bounce without a reason is not actionable."""
    if cheque.status == "Bounced" and not cheque.bounce_reason:
        frappe.throw(
            _("Bounce Reason is required when bouncing a cheque."),
            frappe.ValidationError,
        )


def settle_linked_payment_entry(cheque):
    """§4.5.2 — the cheque cleared, so the money really moved: submit the PE.

    Returns a short status string for the caller to log:
      "submitted"  — the draft PE was submitted (this is the GL posting)
      "already"    — the PE was already submitted; clearance_date stamped
      "pending"    — the acting user may not submit it; a ToDo was raised
      "none"       — no Payment Entry is linked
    """
    if cheque.reference_doctype != "Payment Entry" or not cheque.reference_name:
        # Reaching here means validate_clearance_has_accounting_document let it
        # through, i.e. a System Manager overrode deliberately. The caller logs
        # the override on the timeline.
        return "none"

    if not frappe.db.exists("Payment Entry", cheque.reference_name):
        return "none"

    pe = frappe.get_doc("Payment Entry", cheque.reference_name)

    if pe.docstatus == 2:
        frappe.throw(
            _("Payment Entry {0} is cancelled — it cannot settle cheque {1}.").format(
                pe.name, cheque.name
            ),
            frappe.ValidationError,
        )

    if pe.docstatus == 1:
        # Already posted: just stamp the bank clearance date.
        if cheque.cleared_date and pe.get("clearance_date") != cheque.cleared_date:
            pe.db_set("clearance_date", cheque.cleared_date, update_modified=False)
        return "already"

    if not _can_submit_payment_entry(pe):
        _raise_submission_todo(cheque, pe)
        return "pending"

    pe.flags.ignore_permissions = True
    _submit_through_workflow(pe)

    pe.reload()
    if pe.docstatus == 1 and cheque.cleared_date:
        pe.db_set("clearance_date", cheque.cleared_date, update_modified=False)

    if pe.docstatus != 1:
        # A workflow moved it forward but not all the way to submitted.
        _raise_submission_todo(cheque, pe)
        return "pending"

    return "submitted"


def _payment_entry_workflow():
    return frappe.db.get_value(
        "Workflow", {"document_type": "Payment Entry", "is_active": 1}, "name"
    )


def _can_submit_payment_entry(pe):
    """Whether the acting user may actually push this PE to submitted.

    With an approval workflow in play (production has an "Approval Pending by
    Accounting Manager" gate) submit permission is not enough — the user also
    needs a transition that lands on a docstatus-1 state.
    """
    if not frappe.has_permission("Payment Entry", "submit", doc=pe):
        return False

    if not _payment_entry_workflow():
        return True

    return bool(_submitting_transition(pe))


def _submitting_transition(pe):
    """A transition available to the acting user that lands the PE on submitted.

    Returns None when there is none — including when the PE has no workflow state
    at all. Frappe's `workflow_state` custom field is created without a default
    (frappe/workflow/doctype/workflow/workflow.py:43-62), so a Payment Entry
    created by API rather than through the desk can genuinely have none, and
    `get_transitions` raises `WorkflowStateError` for it. Treating that as "cannot
    submit" routes it to the ToDo path, which is the conservative answer: better a
    human looks at it than the approval gate gets skipped.
    """
    from frappe.model.workflow import WorkflowStateError, get_transitions

    workflow_name = _payment_entry_workflow()
    if not workflow_name:
        return None

    workflow = frappe.get_doc("Workflow", workflow_name)
    submitted_states = {s.state for s in workflow.states if str(s.doc_status) == "1"}

    if not pe.get(workflow.workflow_state_field):
        return None

    try:
        transitions = get_transitions(pe, workflow, raise_exception=True) or []
    except WorkflowStateError:
        return None

    for transition in transitions:
        if transition.get("next_state") in submitted_states:
            return transition
    return None


def _submit_through_workflow(pe):
    """Submit the PE, going through its workflow when one is active.

    §4.5.2 is explicit that the workflow must be respected rather than
    circumvented — a forced `pe.submit()` would step around the approval gate
    the finance team put there on purpose.
    """
    if not _payment_entry_workflow():
        pe.submit()
        return

    from frappe.model.workflow import apply_workflow

    transition = _submitting_transition(pe)
    if not transition:
        return
    apply_workflow(pe, transition.get("action"))


def _raise_submission_todo(cheque, pe):
    """Leave the cheque Cleared, flag it, and put the PE on someone's desk."""
    cheque.db_set("pe_pending_submission", 1, update_modified=False)

    owner = _pending_approver(pe)
    description = _(
        "Cheque {0} cleared on {1}. Its Payment Entry {2} is still a draft and "
        "could not be submitted automatically — {3} lacks the required approval "
        "role. Submit {2} to post the collection."
    ).format(cheque.name, cheque.cleared_date or frappe.utils.today(), pe.name, frappe.session.user)

    existing = frappe.db.get_value(
        "ToDo",
        {
            "reference_type": "Payment Entry",
            "reference_name": pe.name,
            "status": "Open",
            "allocated_to": owner,
        },
        "name",
    )
    if not existing:
        todo = frappe.get_doc(
            {
                "doctype": "ToDo",
                "allocated_to": owner,
                "reference_type": "Payment Entry",
                "reference_name": pe.name,
                "description": description,
                "priority": "High",
                "date": frappe.utils.today(),
            }
        )
        todo.flags.ignore_permissions = True
        todo.insert(ignore_permissions=True)

    frappe.msgprint(
        description,
        title=_("Payment Entry pending submission"),
        indicator="orange",
    )


def _pending_approver(pe):
    """Best available owner for the follow-up ToDo.

    Prefers a user who actually holds a role allowed to move the PE forward,
    then the PE's owner, then the Administrator — never nobody, or the ToDo
    silently belongs to no one.
    """
    workflow_name = _payment_entry_workflow()
    if workflow_name:
        workflow = frappe.get_doc("Workflow", workflow_name)
        submitted_states = {s.state for s in workflow.states if str(s.doc_status) == "1"}
        state_field = workflow.workflow_state_field
        current_state = pe.get(state_field)

        allowed_roles = {
            t.allowed
            for t in workflow.transitions
            if t.state == current_state and t.next_state in submitted_states and t.allowed
        }
        for role in sorted(allowed_roles):
            holder = frappe.db.sql(
                """
                SELECT hr.parent
                FROM `tabHas Role` hr
                JOIN `tabUser` u ON u.name = hr.parent
                WHERE hr.role = %s AND hr.parenttype = 'User'
                  AND u.enabled = 1 AND u.name NOT IN ('Administrator', 'Guest')
                ORDER BY u.name
                LIMIT 1
                """,
                role,
            )
            if holder:
                return holder[0][0]

    return pe.owner or "Administrator"


def validate_replacement_candidate(original, replacement):
    """Validate that ``replacement`` is a legitimate replacement for
    ``original``. Throws frappe.ValidationError on hard failures; emits
    frappe.msgprint warnings on soft failures."""

    if replacement.name == original.name:
        frappe.throw(_("A cheque cannot replace itself."))

    if replacement.docstatus != 0:
        frappe.throw(_("Replacement cheque must be in Draft state, not {0}.").format(
            {0: "Draft", 1: "Submitted", 2: "Cancelled"}[replacement.docstatus]
        ))

    if replacement.cheque_type != original.cheque_type:
        frappe.throw(_("Replacement must be the same cheque type ({0}).").format(
            original.cheque_type
        ))

    if (replacement.party_type != original.party_type
            or replacement.party != original.party):
        frappe.throw(_("Replacement must be from/to the same party: {0} {1}.").format(
            original.party_type, original.party
        ))

    if (replacement.reference_doctype != original.reference_doctype
            or replacement.reference_name != original.reference_name):
        frappe.throw(_("Replacement must reference the same {0}: {1}.").format(
            original.reference_doctype or "(no reference)",
            original.reference_name or "(no reference)",
        ))

    if replacement.original_cheque:
        frappe.throw(_("Cheque {0} is already marked as a replacement for {1}.").format(
            replacement.name, replacement.original_cheque
        ))

    # Soft warning: amount mismatch is allowed (bank fees, partial replacement)
    # but worth surfacing.
    if replacement.amount != original.amount:
        frappe.msgprint(
            _("Note: replacement amount ({0}) differs from original ({1}). "
              "Continuing — verify this is intentional.").format(
                replacement.amount, original.amount
            ),
            indicator="orange",
            alert=True,
        )


def cancel_clearance_je(cheque):
    if not cheque.clearance_je:
        return
    je = frappe.get_doc("Journal Entry", cheque.clearance_je)
    if je.docstatus != 1:
        return
    je.flags.ignore_permissions = True
    # The Cheque still holds clearance_je as a back-link audit trail; bypass
    # Frappe's check_no_back_links_exist since the link is intentional.
    je.flags.ignore_links = True
    je.cancel()
