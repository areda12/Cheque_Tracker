"""Idempotent local development seed for cheque_tracker.

Mirrors the EEI production environment closely enough that regression
assertions run against realistic documents.  Safe to run repeatedly:
every step is a get-or-create keyed on a natural identifier.

Usage:
    bench --site cheque.localhost execute cheque_tracker.tests.seed_local.run

See BUILD_INSTRUCTIONS.md §2 (Phase 0) and Appendix A.4.
"""

import frappe
from frappe.utils import flt

# --------------------------------------------------------------------------
# Canonical names.  The company misspelling is canonical in ERPNext — see
# BUILD_INSTRUCTIONS.md §1.6.  Never "Industries".
# --------------------------------------------------------------------------
COMPANY = "Egyptian For Engineering Industires"
ABBR = "EEI"
CURRENCY = "EGP"
COUNTRY = "Egypt"

BANK_CIB = "Commercial International Bank"
BANK_KFH = "Kuwait Finance House (KFH) Egypt"

GL_BANK = f"CIB Current - {ABBR}"
GL_CASH = f"Main Cash - {ABBR}"
GL_DEBTORS = f"Debtors - {ABBR}"
GL_CREDITORS = f"Creditors - {ABBR}"

BANK_ACCOUNT = f"CIB Current - {BANK_CIB}"

CUSTOMER = "ElMansour Elevators"
SUPPLIER = "Egyptian Electricity Distribution Co"

TREASURY_USER = "treasury@eei.localhost"
AUDITOR_USER = "auditor@eei.localhost"

ITEM_CODE = "EEI-SERVICE-01"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _log(msg):
	print(f"[seed] {msg}")


def _get_or_create(doctype, name, payload, submit=False):
	"""Return existing doc name, else insert `payload`."""
	if frappe.db.exists(doctype, name):
		return name
	doc = frappe.get_doc({"doctype": doctype, **payload})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	_log(f"created {doctype} {doc.name}")
	return doc.name


# --------------------------------------------------------------------------
# 1. company
# --------------------------------------------------------------------------
def ensure_company():
	if frappe.db.exists("Company", COMPANY):
		return COMPANY

	_log(f"creating Company {COMPANY} (builds chart of accounts — slow)")
	company = frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": COMPANY,
			"abbr": ABBR,
			"default_currency": CURRENCY,
			"country": COUNTRY,
			"create_chart_of_accounts_based_on": "Standard Template",
			"chart_of_accounts": "Standard",
			"valuation_method": "FIFO",
		}
	)
	company.flags.ignore_permissions = True
	company.insert(ignore_permissions=True)
	_log(f"created Company {COMPANY}")
	return COMPANY


# --------------------------------------------------------------------------
# 2. GL accounts
# --------------------------------------------------------------------------
def _find_parent(account_name_fragments, root_type):
	"""Locate a group account to hang a new ledger under."""
	for fragment in account_name_fragments:
		parent = frappe.db.get_value(
			"Account",
			{
				"company": COMPANY,
				"is_group": 1,
				"account_name": fragment,
			},
			"name",
		)
		if parent:
			return parent
	# fall back to the root of the right type
	return frappe.db.get_value(
		"Account", {"company": COMPANY, "is_group": 1, "root_type": root_type, "parent_account": ["in", ("", None)]}, "name"
	)


def ensure_accounts():
	"""CIB Current (Bank) and Main Cash (Cash); assert Debtors/Creditors exist."""
	if not frappe.db.exists("Account", GL_BANK):
		parent = _find_parent(["Bank Accounts", "Current Assets"], "Asset")
		_get_or_create(
			"Account",
			GL_BANK,
			{
				"account_name": "CIB Current",
				"company": COMPANY,
				"parent_account": parent,
				"account_type": "Bank",
				"account_currency": CURRENCY,
				"is_group": 0,
			},
		)

	if not frappe.db.exists("Account", GL_CASH):
		parent = _find_parent(["Cash In Hand", "Current Assets"], "Asset")
		_get_or_create(
			"Account",
			GL_CASH,
			{
				"account_name": "Main Cash",
				"company": COMPANY,
				"parent_account": parent,
				"account_type": "Cash",
				"account_currency": CURRENCY,
				"is_group": 0,
			},
		)

	for required in (GL_DEBTORS, GL_CREDITORS):
		if not frappe.db.exists("Account", required):
			raise RuntimeError(
				f"{required} missing — the standard chart of accounts did not build as expected."
			)

	return GL_BANK, GL_CASH


# --------------------------------------------------------------------------
# 3. banks + bank account
# --------------------------------------------------------------------------
def ensure_banks():
	for bank in (BANK_CIB, BANK_KFH):
		_get_or_create("Bank", bank, {"bank_name": bank})
	return BANK_CIB, BANK_KFH


def ensure_bank_account():
	"""Bank Account autonames to '<account_name> - <bank>' (erpnext bank_account.py:51)."""
	return _get_or_create(
		"Bank Account",
		BANK_ACCOUNT,
		{
			"account_name": "CIB Current",
			"bank": BANK_CIB,
			"account": GL_BANK,
			"company": COMPANY,
			"is_company_account": 1,
			"is_default": 1,
		},
	)


# --------------------------------------------------------------------------
# 4. parties
# --------------------------------------------------------------------------
def ensure_parties():
	_get_or_create(
		"Customer",
		CUSTOMER,
		{
			"customer_name": CUSTOMER,
			"customer_type": "Company",
			"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
			or "All Customer Groups",
			"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories",
		},
	)
	_get_or_create(
		"Supplier",
		SUPPLIER,
		{
			"supplier_name": SUPPLIER,
			"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
			or "All Supplier Groups",
		},
	)
	return CUSTOMER, SUPPLIER


# --------------------------------------------------------------------------
# 5. roles + users
# --------------------------------------------------------------------------
def ensure_users():
	"""Treasury User / Cheque Auditor roles ship as app fixtures; assign them."""
	for role in ("Treasury User", "Cheque Auditor"):
		_get_or_create("Role", role, {"role_name": role, "desk_access": 1})

	for email, first_name, roles in (
		(TREASURY_USER, "Treasury", ["Treasury User", "Accounts User"]),
		(AUDITOR_USER, "Auditor", ["Cheque Auditor"]),
	):
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"send_welcome_email": 0,
					"enabled": 1,
				}
			)
			user.flags.ignore_permissions = True
			user.insert(ignore_permissions=True)
			_log(f"created User {email}")

		user = frappe.get_doc("User", email)
		existing = {r.role for r in user.roles}
		missing = [r for r in roles if r not in existing and frappe.db.exists("Role", r)]
		if missing:
			# never rewrite the child table wholesale — see BUILD_INSTRUCTIONS A.5 (30/07 incident)
			for role in missing:
				user.append("roles", {"role": role})
			user.save(ignore_permissions=True)
			_log(f"granted {missing} to {email}")

	return TREASURY_USER, AUDITOR_USER


# --------------------------------------------------------------------------
# 6. settings
# --------------------------------------------------------------------------
def ensure_settings():
	settings = frappe.get_single("Cheque Tracker Settings")
	changed = False
	for field, value in (
		("default_bank_account", BANK_ACCOUNT),
		("default_bank_gl_account", GL_BANK),
		("default_cash_account", GL_CASH),
	):
		if settings.get(field) != value:
			settings.set(field, value)
			changed = True
	if changed:
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
		_log("configured Cheque Tracker Settings")
	return settings


# --------------------------------------------------------------------------
# 7. cheque book
# --------------------------------------------------------------------------
def ensure_cheque_book(start="500001", end="500025"):
	existing = frappe.db.get_value(
		"Cheque Book",
		{"company": COMPANY, "start_cheque_no": start, "docstatus": 1},
		"name",
	)
	if existing:
		return existing

	book = frappe.get_doc(
		{
			"doctype": "Cheque Book",
			"company": COMPANY,
			"bank_account": BANK_ACCOUNT,
			"sequence_type": "Numeric",
			"start_cheque_no": start,
			"end_cheque_no": end,
			"issue_date": "2026-01-01",
			"safe_location": "Main Safe — Head Office",
		}
	)
	book.flags.ignore_permissions = True
	book.insert(ignore_permissions=True)
	book.submit()
	_log(f"created Cheque Book {book.name} ({start}–{end})")
	return book.name


# --------------------------------------------------------------------------
# 8. sales invoice + draft payment entry (backing for CHQ-2026-00002)
# --------------------------------------------------------------------------
def ensure_item():
	return _get_or_create(
		"Item",
		ITEM_CODE,
		{
			"item_code": ITEM_CODE,
			"item_name": "Engineering Services",
			"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
			"stock_uom": "Nos",
			"is_stock_item": 0,
		},
	)


def ensure_sales_invoice(amount=73000.0):
	existing = frappe.db.get_value(
		"Sales Invoice",
		{"customer": CUSTOMER, "company": COMPANY, "docstatus": 1, "grand_total": amount},
		"name",
	)
	if existing:
		return existing

	ensure_item()
	si = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": COMPANY,
			"customer": CUSTOMER,
			"currency": CURRENCY,
			"posting_date": "2026-08-01",
			"due_date": "2026-09-10",
			"debit_to": GL_DEBTORS,
			"items": [
				{
					"item_code": ITEM_CODE,
					"qty": 1,
					"rate": amount,
					"income_account": frappe.db.get_value(
						"Account", {"company": COMPANY, "account_type": "Income Account", "is_group": 0}, "name"
					),
					"cost_center": frappe.db.get_value(
						"Cost Center", {"company": COMPANY, "is_group": 0}, "name"
					),
				}
			],
		}
	)
	si.flags.ignore_permissions = True
	si.insert(ignore_permissions=True)
	si.submit()
	_log(f"created Sales Invoice {si.name} for {amount}")
	return si.name


def ensure_draft_payment_entry(sales_invoice, amount=73000.0, cheque_no="10049616"):
	"""Draft PE against the invoice — EEI's model keeps it draft until the cheque clears."""
	existing = frappe.db.get_value(
		"Payment Entry",
		{"party": CUSTOMER, "company": COMPANY, "docstatus": 0, "reference_no": cheque_no},
		"name",
	)
	if existing:
		return existing

	pe = frappe.get_doc(
		{
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"company": COMPANY,
			"posting_date": "2026-08-10",
			"mode_of_payment": "Cheque",
			"party_type": "Customer",
			"party": CUSTOMER,
			"paid_from": GL_DEBTORS,
			"paid_to": GL_BANK,
			"paid_amount": amount,
			"received_amount": amount,
			"source_exchange_rate": 1,
			"target_exchange_rate": 1,
			"reference_no": cheque_no,
			"reference_date": "2026-09-10",
			"references": [
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": sales_invoice,
					"allocated_amount": amount,
				}
			],
		}
	)
	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)
	_log(f"created draft Payment Entry {pe.name} (stays draft until clearance)")
	return pe.name


# --------------------------------------------------------------------------
# 9. the two production cheques (Appendix A.4)
# --------------------------------------------------------------------------
def _existing_cheque(cheque_no):
	return frappe.db.get_value(
		"Cheque", {"cheque_no": cheque_no, "company": COMPANY, "docstatus": ["<", 2]}, "name"
	)


def ensure_incoming_cheque(payment_entry, amount=73000.0, cheque_no="10049616"):
	"""A.4 — CHQ-2026-00002 analogue: incoming PDC from ElMansour Elevators."""
	existing = _existing_cheque(cheque_no)
	if existing:
		return existing

	cheque = frappe.get_doc(
		{
			"doctype": "Cheque",
			"cheque_type": "Incoming",
			"company": COMPANY,
			"party_type": "Customer",
			"party": CUSTOMER,
			"amount": amount,
			"currency": CURRENCY,
			"received_date": "2026-08-10",
			"due_date": "2026-09-10",
			"cheque_no": cheque_no,
			"drawee_bank": BANK_KFH,
			"drawer_name": CUSTOMER,
			"clearance_type": "Deposit",
			"bank_account": BANK_ACCOUNT,
			"reference_doctype": "Payment Entry",
			"reference_name": payment_entry,
			"remarks": "PDC — seeded from Appendix A.4 (CHQ-2026-00002 analogue).",
		}
	)
	cheque.flags.ignore_permissions = True
	cheque.insert(ignore_permissions=True)
	cheque.submit()
	_log(f"created Incoming Cheque {cheque.name} ({amount} {CURRENCY}, due 2026-09-10)")
	return cheque.name


def ensure_outgoing_cheque(cheque_book, amount=20119.0):
	"""A.4 — CHQ-2026-00001 analogue: outgoing, handed to an external collector, no PE link."""
	# Idempotency must be checked BEFORE allocating a leaf — otherwise every run
	# reserves a fresh leaf and the natural key never matches.
	existing = frappe.db.get_value(
		"Cheque",
		{
			"cheque_type": "Outgoing",
			"company": COMPANY,
			"party": SUPPLIER,
			"amount": amount,
			"docstatus": ["<", 2],
		},
		"name",
	)
	if existing:
		return existing

	leaf = frappe.db.get_value(
		"Cheque Leaf", {"cheque_book": cheque_book, "leaf_status": "Unused"}, ["name", "cheque_no"], as_dict=True
	)
	if not leaf:
		raise RuntimeError(f"No unused leaf in {cheque_book}")

	cheque = frappe.get_doc(
		{
			"doctype": "Cheque",
			"cheque_type": "Outgoing",
			"company": COMPANY,
			"party_type": "Supplier",
			"party": SUPPLIER,
			"amount": amount,
			"currency": CURRENCY,
			"issue_date": "2026-08-01",
			"due_date": "2026-08-30",
			"cheque_no": leaf.cheque_no,
			"drawee_bank": BANK_CIB,
			"drawer_name": COMPANY,
			"bank_account": BANK_ACCOUNT,
			"cheque_book": cheque_book,
			"custody_location": "استلمه مندوب شركة الكهرباء",
			"remarks": "تسليم شيك لمندوب شركة الكهرباء — seeded from Appendix A.4 (CHQ-2026-00001 analogue).",
		}
	)
	cheque.flags.ignore_permissions = True
	cheque.insert(ignore_permissions=True)
	cheque.submit()
	_log(f"created Outgoing Cheque {cheque.name} ({amount} {CURRENCY}, due 2026-08-30)")
	return cheque.name


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def run():
	"""Idempotent full seed."""
	frappe.flags.in_import = True

	ensure_company()
	ensure_accounts()
	ensure_banks()
	ensure_bank_account()
	ensure_parties()
	ensure_users()
	ensure_settings()

	book = ensure_cheque_book()
	si = ensure_sales_invoice()
	pe = ensure_draft_payment_entry(si)

	incoming = ensure_incoming_cheque(pe)
	outgoing = ensure_outgoing_cheque(book)

	frappe.db.commit()

	_log("--- seed complete ---")
	_log(f"company        : {COMPANY}")
	_log(f"bank account   : {BANK_ACCOUNT}  (GL {GL_BANK})")
	_log(f"cheque book    : {book}")
	_log(f"sales invoice  : {si}")
	_log(f"draft PE       : {pe}")
	_log(f"incoming cheque: {incoming}")
	_log(f"outgoing cheque: {outgoing}")
	return {
		"company": COMPANY,
		"bank_account": BANK_ACCOUNT,
		"cheque_book": book,
		"sales_invoice": si,
		"payment_entry": pe,
		"incoming_cheque": incoming,
		"outgoing_cheque": outgoing,
	}
