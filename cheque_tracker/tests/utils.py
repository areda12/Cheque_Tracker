"""Deterministic, idempotent test environment for the cheque_tracker suite.

Why this exists
---------------
The suite used to resolve its fixtures with ``frappe.get_all("Company", limit=1)``.
``frappe.get_all`` orders by ``modified desc``, so the company — and therefore the
bank account, customer and currency every test ran against — changed from run to
run.  Because that lookup could return a company with no Bank Account, each test
carried a ``skipTest`` guard.  On a fresh site 16 of 34 tests errored and 12
skipped themselves, so the suite reported "no failures" while asserting almost
nothing.

This module pins the environment to the canonical EEI company built by
``seed_local``, plus one secondary company used solely by the cross-company
validation tests.  ``before_tests`` (wired from hooks.py) builds it once per run,
so the guards are no longer reachable and were replaced with hard assertions.
"""

import frappe

from cheque_tracker.tests import seed_local as seed

# Secondary company — only needed to prove cross-company validation rejects a
# bank account belonging to a different company.
SECONDARY_COMPANY = "_Test Company"
SECONDARY_BANK = "Test Bank - Cheque Tracker"
SECONDARY_GL_NAME = "Test Bank GL"


def _secondary_company():
	"""Guarantee a second company exists, using frappe's standard test records."""
	if frappe.db.exists("Company", SECONDARY_COMPANY):
		return SECONDARY_COMPANY

	from frappe.tests.utils import make_test_records

	make_test_records("Company", commit=True)
	if frappe.db.exists("Company", SECONDARY_COMPANY):
		return SECONDARY_COMPANY

	# Last resort: any company that is not the primary one.
	other = frappe.db.get_value("Company", {"name": ["!=", seed.COMPANY]}, "name")
	if not other:
		raise RuntimeError("cheque_tracker tests need a second Company and none could be created")
	return other


def _secondary_bank_account(company):
	"""A company bank account on the secondary company (cross-company tests)."""
	abbr = frappe.db.get_value("Company", company, "abbr")
	gl_name = f"{SECONDARY_GL_NAME} - {abbr}"

	if not frappe.db.exists("Account", gl_name):
		parent = frappe.db.get_value(
			"Account", {"company": company, "is_group": 1, "account_name": "Bank Accounts"}, "name"
		) or frappe.db.get_value(
			"Account", {"company": company, "is_group": 1, "account_name": "Current Assets"}, "name"
		)
		if not parent:
			raise RuntimeError(f"No group account to hang {gl_name} under on {company}")
		account = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": SECONDARY_GL_NAME,
				"company": company,
				"parent_account": parent,
				"account_type": "Bank",
				"is_group": 0,
			}
		)
		account.flags.ignore_permissions = True
		account.insert(ignore_permissions=True)

	if not frappe.db.exists("Bank", SECONDARY_BANK):
		bank = frappe.get_doc({"doctype": "Bank", "bank_name": SECONDARY_BANK})
		bank.flags.ignore_permissions = True
		bank.insert(ignore_permissions=True)

	# Bank Account autonames to "<account_name> - <bank>" (erpnext bank_account.py:51)
	name = f"{SECONDARY_GL_NAME} - {SECONDARY_BANK}"
	if not frappe.db.exists("Bank Account", name):
		bank_account = frappe.get_doc(
			{
				"doctype": "Bank Account",
				"account_name": SECONDARY_GL_NAME,
				"bank": SECONDARY_BANK,
				"account": gl_name,
				"company": company,
				"is_company_account": 1,
			}
		)
		bank_account.flags.ignore_permissions = True
		bank_account.insert(ignore_permissions=True)

	return name


def ensure_test_env():
	"""Build (idempotently) and return the canonical test environment."""
	seed.ensure_company()
	seed.ensure_accounts()
	seed.ensure_banks()
	seed.ensure_bank_account()
	seed.ensure_parties()
	seed.ensure_users()
	seed.ensure_settings()

	secondary = _secondary_company()
	secondary_bank_account = _secondary_bank_account(secondary)

	return {
		"company": seed.COMPANY,
		"abbr": seed.ABBR,
		"currency": seed.CURRENCY,
		"bank_account": seed.BANK_ACCOUNT,
		"bank_gl_account": seed.GL_BANK,
		"cash_account": seed.GL_CASH,
		"debtors": seed.GL_DEBTORS,
		"creditors": seed.GL_CREDITORS,
		"customer": seed.CUSTOMER,
		"supplier": seed.SUPPLIER,
		"drawee_bank": seed.BANK_KFH,
		"treasury_user": seed.TREASURY_USER,
		"auditor_user": seed.AUDITOR_USER,
		"secondary_company": secondary,
		"secondary_bank_account": secondary_bank_account,
	}


def get_test_env():
	"""Return the canonical env, building it if a test runs outside before_tests."""
	if not frappe.db.exists("Bank Account", seed.BANK_ACCOUNT):
		return ensure_test_env()

	secondary = SECONDARY_COMPANY if frappe.db.exists("Company", SECONDARY_COMPANY) else None
	secondary_bank_account = f"{SECONDARY_GL_NAME} - {SECONDARY_BANK}"
	if not secondary or not frappe.db.exists("Bank Account", secondary_bank_account):
		return ensure_test_env()

	return {
		"company": seed.COMPANY,
		"abbr": seed.ABBR,
		"currency": seed.CURRENCY,
		"bank_account": seed.BANK_ACCOUNT,
		"bank_gl_account": seed.GL_BANK,
		"cash_account": seed.GL_CASH,
		"debtors": seed.GL_DEBTORS,
		"creditors": seed.GL_CREDITORS,
		"customer": seed.CUSTOMER,
		"supplier": seed.SUPPLIER,
		"drawee_bank": seed.BANK_KFH,
		"treasury_user": seed.TREASURY_USER,
		"auditor_user": seed.AUDITOR_USER,
		"secondary_company": secondary,
		"secondary_bank_account": secondary_bank_account,
	}


def before_tests():
	"""hooks.py -> before_tests. Runs once per `bench run-tests --app cheque_tracker`."""
	env = ensure_test_env()
	frappe.db.commit()
	print(f"[cheque_tracker] test env ready: company={env['company']} bank_account={env['bank_account']}")
	return env
