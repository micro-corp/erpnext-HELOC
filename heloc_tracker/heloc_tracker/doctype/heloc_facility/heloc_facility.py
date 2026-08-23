import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class HELOCFacility(Document):
	def validate(self):
		self.compute_totals()
		self.validate_accounts()

		if self.credit_limit_journal_entry and self.has_value_changed("credit_limit"):
			frappe.msgprint(
				_(
					"Credit Limit changed but the memo Journal Entry {0} still reflects the old amount. "
					"Cancel it and re-post if you want the memo entry to match."
				).format(self.credit_limit_journal_entry),
				indicator="orange",
				alert=True,
			)

	def validate_accounts(self):
		"""
		Server-side backstop for the same filters the client script applies -
		client-side get_query is just UX and can be bypassed via the API.
		The Credit Limit memo pair is a genuine contra relationship, so
		both sides are allowed to be either Asset or Liability type
		(matching whichever way the person set up their offset account),
		unlike a normal single-purpose account field.
		"""
		if self.group_liability_account:
			acc = frappe.db.get_value("Account", self.group_liability_account, ["company", "root_type"], as_dict=True)
			if acc:
				if self.company and acc.company != self.company:
					frappe.throw(_("Group Liability Account belongs to company {0}, not {1}.").format(acc.company, self.company))
				if acc.root_type != "Liability":
					frappe.throw(_("Group Liability Account ({0}) is a {1} account - expected Liability.").format(self.group_liability_account, acc.root_type))

		contra_fields = [
			("credit_limit_asset_account", _("Credit Limit Asset Account")),
			("credit_limit_offset_account", _("Credit Limit Offset Account")),
		]
		accounts_seen = []
		for fieldname, label in contra_fields:
			account = self.get(fieldname)
			if not account:
				continue
			acc = frappe.db.get_value("Account", account, ["company", "root_type", "is_group"], as_dict=True)
			if not acc:
				continue
			if self.company and acc.company != self.company:
				frappe.throw(_("{0} ({1}) belongs to company {2}, not {3}.").format(label, account, acc.company, self.company))
			if acc.is_group:
				frappe.throw(_("{0} ({1}) is a Group account and can't be posted to directly.").format(label, account))
			if acc.root_type not in ("Asset", "Liability"):
				frappe.throw(_("{0} ({1}) is a {2} account - expected Asset or Liability.").format(label, account, acc.root_type))
			accounts_seen.append(account)

		if len(accounts_seen) == 2 and accounts_seen[0] == accounts_seen[1]:
			frappe.throw(_("Credit Limit Asset Account and Credit Limit Offset Account can't be the same account."))

	def compute_totals(self):
		"""Sum Current Balance across every HELOC Tranche linked to this facility."""
		balances = []
		if self.name:
			balances = frappe.get_all(
				"HELOC Tranche",
				filters={"heloc": self.name},
				pluck="current_balance",
			)
		total = sum(flt(b) for b in balances)
		self.total_balance = total
		self.available_credit = flt(self.credit_limit) - total

	@frappe.whitelist()
	def refresh_balance(self):
		"""Manual recompute - useful after editing tranches directly or bulk changes."""
		self.compute_totals()
		self.save()
		return {"total_balance": self.total_balance, "available_credit": self.available_credit}

	@frappe.whitelist()
	def post_credit_limit(self):
		"""
		Posts the full Credit Limit as a balanced memo entry:
		debit the Credit Limit Asset Account, credit the Credit Limit Offset Account.
		This is informational only - it records the facility's total limit on the
		books for reporting, and does not touch the real liability accounts on the
		Tranches (Total Balance / Available Credit are computed separately in
		compute_totals and are unaffected by this).
		"""
		if self.credit_limit_journal_entry:
			frappe.throw(
				_("A Credit Limit memo entry ({0}) is already posted. Cancel it first if you need to re-post.").format(
					self.credit_limit_journal_entry
				)
			)

		if not flt(self.credit_limit) > 0:
			frappe.throw(_("Credit Limit must be greater than zero before posting."))

		if not (self.credit_limit_asset_account and self.credit_limit_offset_account):
			frappe.throw(_("Set both Credit Limit Asset Account and Credit Limit Offset Account first."))

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = frappe.utils.today()
		je.user_remark = _("HELOC credit limit memo - {0}").format(self.facility_name)
		je.set("accounts", [
			{
				"account": self.credit_limit_asset_account,
				"debit_in_account_currency": flt(self.credit_limit),
				"credit_in_account_currency": 0,
			},
			{
				"account": self.credit_limit_offset_account,
				"debit_in_account_currency": 0,
				"credit_in_account_currency": flt(self.credit_limit),
			},
		])
		je.insert()
		je.submit()

		self.credit_limit_journal_entry = je.name
		self.save()

		frappe.msgprint(_("Posted Credit Limit memo Journal Entry {0}.").format(je.name))
		return je.name

	@frappe.whitelist()
	def cancel_credit_limit_posting(self):
		"""Cancels the memo Journal Entry so the Credit Limit can be re-posted (e.g. after a limit change)."""
		if not self.credit_limit_journal_entry:
			frappe.throw(_("No Credit Limit memo entry is currently posted."))

		je = frappe.get_doc("Journal Entry", self.credit_limit_journal_entry)
		if je.docstatus == 1:
			frappe.flags.heloc_tracker_allow_cancel = True
			je.cancel()
			frappe.flags.heloc_tracker_allow_cancel = False

		self.credit_limit_journal_entry = None
		self.save()

		frappe.msgprint(_("Credit Limit memo entry cancelled."))

	@frappe.whitelist()
	def carve_out_tranche(
		self,
		tranche_name,
		amount,
		interest_rate,
		tranche_term_months,
		total_amortization_months,
		start_date,
		liability_account,
		interest_expense_account,
		bank_account=None,
	):
		"""
		Moves a chunk of the Revolving Portion into a new Fixed (Prêt Lié) tranche:
		- creates the new HELOC Tranche and generates its amortization schedule
		- posts one Journal Entry: debit Revolving liability, credit new tranche liability
		- reduces the Revolving tranche's Current Balance
		- refreshes this facility's rollup totals
		This is one atomic action end-to-end, matching how carve-outs are recorded
		on the Chart of Accounts (debit Revolving Portion, credit new Tranche N).
		"""
		amount = flt(amount)
		if amount <= 0:
			frappe.throw(_("Carve-out amount must be greater than zero."))

		revolving_names = frappe.get_all(
			"HELOC Tranche",
			filters={"heloc": self.name, "tranche_type": "Revolving"},
			pluck="name",
			limit=1,
		)
		if not revolving_names:
			frappe.throw(_("No Revolving tranche is linked to this facility yet. Create one first."))

		revolving = frappe.get_doc("HELOC Tranche", revolving_names[0])

		# Same lock pattern as post_next_payment - prevents two concurrent
		# carve-outs from both reading the same Revolving balance and
		# double-spending it.
		frappe.db.get_value("HELOC Tranche", revolving.name, for_update=True)
		revolving.reload()

		if flt(revolving.current_balance) < amount:
			frappe.throw(
				_("Revolving balance ({0}) is less than the carve-out amount ({1}).").format(
					revolving.current_balance, amount
				)
			)

		if not bank_account:
			bank_account = revolving.bank_account

		new_tranche = frappe.new_doc("HELOC Tranche")
		new_tranche.update({
			"tranche_name": tranche_name,
			"heloc": self.name,
			"company": self.company,
			"lender": self.lender,
			"tranche_type": "Fixed (Pr\u00eat Li\u00e9)",
			"liability_account": liability_account,
			"interest_expense_account": interest_expense_account,
			"bank_account": bank_account,
			"original_principal": amount,
			"interest_rate": interest_rate,
			"tranche_term_months": tranche_term_months,
			"total_amortization_months": total_amortization_months,
			"start_date": start_date,
		})
		new_tranche.insert()
		new_tranche.generate_schedule()

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = start_date
		je.user_remark = _("HELOC carve-out - {0} moved from Revolving to {1}").format(amount, tranche_name)
		je.set("accounts", [
			{
				"account": revolving.liability_account,
				"debit_in_account_currency": amount,
				"credit_in_account_currency": 0,
			},
			{
				"account": new_tranche.liability_account,
				"debit_in_account_currency": 0,
				"credit_in_account_currency": amount,
			},
		])
		je.insert()
		je.submit()

		new_tranche.carved_out_from = revolving.name
		new_tranche.carve_out_journal_entry = je.name
		new_tranche.save()

		revolving.current_balance = flt(revolving.current_balance) - amount
		revolving.save()

		self.compute_totals()
		self.save()

		frappe.msgprint(
			_("Carved out {0} into new tranche {1}. Journal Entry {2} posted.").format(
				amount, new_tranche.name, je.name
			)
		)
		return new_tranche.name

	def on_trash(self):
		linked = frappe.get_all("HELOC Tranche", filters={"heloc": self.name}, limit=1)
		if linked:
			frappe.throw(_("This facility still has linked HELOC Tranche records. Delete or reassign those first."))

	@frappe.whitelist()
	def get_burndown_data(self):
		"""
		Merges every linked tranche's schedule onto one shared timeline and
		sums each tranche's balance as-of each date, so the result is a real
		point-in-time total across the whole facility - not just adding up
		each tranche's own closing_balance column, which would be wrong
		whenever tranches have different payment dates.
		"""
		tranches = frappe.get_all(
			"HELOC Tranche",
			filters={"heloc": self.name},
			fields=["name", "original_principal", "start_date"],
		)
		if not tranches:
			return {"labels": [], "values": []}

		rows = frappe.get_all(
			"HELOC Amortization Entry",
			filters={"parent": ["in", [t.name for t in tranches]]},
			fields=["parent", "payment_date", "closing_balance"],
			order_by="payment_date asc",
		)

		schedules = {t.name: [] for t in tranches}
		for row in rows:
			schedules[row.parent].append((getdate(row.payment_date), flt(row.closing_balance)))

		all_dates = sorted({d for parent_rows in schedules.values() for d, _bal_unused in parent_rows})
		if not all_dates:
			return {"labels": [], "values": []}

		totals = []
		for as_of in all_dates:
			total = 0
			for t in tranches:
				parent_rows = schedules[t.name]
				# balance as of this date = closing_balance of the last row on
				# or before as_of, or original_principal if no row has posted
				# yet by this date
				balance = flt(t.original_principal)
				for d, bal in parent_rows:
					if d <= as_of:
						balance = bal
					else:
						break
				total += balance
			totals.append(round(total, 2))

		return {
			"labels": [d.strftime("%Y-%m-%d") for d in all_dates],
			"values": totals,
		}

	@frappe.whitelist()
	def sync_budget(self, fiscal_year):
		"""
		Aggregates every linked Tranche's amortization schedule (interest +
		principal) for the given Fiscal Year and pushes it into ERPNext's
		native Budget doctype, budgeted against this Facility's Cost Center.

		ERPNext only supports budgeting against a Cost Center or Project -
		there's no "budget against Account" option - so this requires
		Cost Center to be set on the Facility first.

		Known simplification: ERPNext applies one Monthly Distribution curve
		to every account in a Budget, but interest and principal each have
		their own (different) monthly shape on an amortizing loan. This sync
		sets accurate annual totals per account and leaves monthly
		distribution at ERPNext's default (even split) rather than faking a
		precise monthly curve that the tool can't actually represent.
		"""
		if not self.cost_center:
			frappe.throw(
				_(
					"Set Cost Center on this Facility first. ERPNext's Budget doctype only "
					"budgets against a Cost Center or Project - create a dedicated Cost Center "
					"for this facility and link it here."
				)
			)

		fy = frappe.db.get_value(
			"Fiscal Year", fiscal_year, ["year_start_date", "year_end_date"], as_dict=True
		)
		if not fy:
			frappe.throw(_("Fiscal Year {0} not found.").format(fiscal_year))

		tranches = frappe.get_all(
			"HELOC Tranche",
			filters={"heloc": self.name},
			fields=["name", "interest_expense_account", "liability_account"],
		)
		if not tranches:
			frappe.throw(_("No tranches are linked to this facility yet."))

		tranche_map = {t.name: t for t in tranches}

		rows = frappe.get_all(
			"HELOC Amortization Entry",
			filters={
				"parent": ["in", list(tranche_map.keys())],
				"payment_date": ["between", [fy.year_start_date, fy.year_end_date]],
			},
			fields=["parent", "principal_portion", "interest_portion"],
		)

		account_totals = {}
		for row in rows:
			t = tranche_map.get(row.parent)
			if not t:
				continue
			if t.interest_expense_account:
				account_totals[t.interest_expense_account] = flt(account_totals.get(t.interest_expense_account)) + flt(row.interest_portion)
			if t.liability_account:
				account_totals[t.liability_account] = flt(account_totals.get(t.liability_account)) + flt(row.principal_portion)

		if not account_totals:
			frappe.throw(
				_("No schedule rows fall within Fiscal Year {0} ({1} to {2}). Generate schedules first.").format(
					fiscal_year, fy.year_start_date, fy.year_end_date
				)
			)

		existing = frappe.get_all(
			"Budget",
			filters={
				"company": self.company,
				"fiscal_year": fiscal_year,
				"budget_against": "Cost Center",
				"cost_center": self.cost_center,
			},
			limit=1,
		)

		if existing:
			old = frappe.get_doc("Budget", existing[0].name)
			if old.docstatus == 1:
				# Submitted Budgets can't be edited in place - cancel and
				# create an amended replacement, the standard Frappe pattern
				# for revising a submitted document.
				frappe.flags.heloc_tracker_allow_cancel = True
				old.cancel()
				frappe.flags.heloc_tracker_allow_cancel = False
				budget = frappe.new_doc("Budget")
				budget.amended_from = old.name
			else:
				budget = old
		else:
			budget = frappe.new_doc("Budget")

		budget.company = self.company
		budget.fiscal_year = fiscal_year
		budget.budget_against = "Cost Center"
		budget.cost_center = self.cost_center
		budget.applicable_on_booking_actual_expenses = 1
		budget.applicable_on_material_request = 0
		budget.applicable_on_purchase_order = 0
		budget.action_if_annual_budget_exceeded = budget.action_if_annual_budget_exceeded or "Warn"
		budget.action_if_accumulated_monthly_budget_exceeded = budget.action_if_accumulated_monthly_budget_exceeded or "Warn"

		# This Budget doc is treated as fully owned/managed by this Facility -
		# its accounts table is replaced wholesale each sync rather than
		# merged, so don't add unrelated accounts to it manually.
		budget.set("accounts", [])
		for account, amount in account_totals.items():
			budget.append("accounts", {"account": account, "budget_amount": flt(amount, 2)})

		if budget.is_new():
			budget.insert()
		else:
			budget.save()
		budget.submit()

		self.last_synced_budget = budget.name
		self.save()

		frappe.msgprint(
			_("Synced Budget {0} for Fiscal Year {1} - {2} account(s), total {3}.").format(
				budget.name, fiscal_year, len(account_totals), flt(sum(account_totals.values()), 2)
			)
		)
		return budget.name
