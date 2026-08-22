import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class HELOCFacility(Document):
	def validate(self):
		self.compute_totals()

		if self.credit_limit_journal_entry and self.has_value_changed("credit_limit"):
			frappe.msgprint(
				_(
					"Credit Limit changed but the memo Journal Entry {0} still reflects the old amount. "
					"Cancel it and re-post if you want the memo entry to match."
				).format(self.credit_limit_journal_entry),
				indicator="orange",
				alert=True,
			)

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
			je.cancel()

		self.credit_limit_journal_entry = None
		self.save()

		frappe.msgprint(_("Credit Limit memo entry cancelled."))

	@frappe.whitelist()
	def carve_out_tranche(
		self,
		tranche_name,
		amount,
		interest_rate,
		term_months,
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
			"term_months": term_months,
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
