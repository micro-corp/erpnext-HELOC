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
	def get_rollup_stats(self):
		"""
		Aggregates key totals across every linked tranche: how much was
		originally borrowed, how much is outstanding now, and how much
		principal/interest the full schedule represents versus what's
		actually been posted so far.
		"""
		tranches = frappe.get_all(
			"HELOC Tranche",
			filters={"heloc": self.name},
			fields=["name", "original_principal"],
		)
		if not tranches:
			return {}

		rows = frappe.get_all(
			"HELOC Amortization Entry",
			filters={"tranche": ["in", [t.name for t in tranches]], "docstatus": ["!=", 2]},
			fields=["principal_portion", "interest_portion", "docstatus"],
		)
		posted_rows = [r for r in rows if r.docstatus == 1]

		return {
			"total_original_principal": flt(sum(flt(t.original_principal) for t in tranches), 2),
			"total_current_balance": flt(self.total_balance, 2),
			"total_principal_scheduled": flt(sum(flt(r.principal_portion) for r in rows), 2),
			"total_interest_scheduled": flt(sum(flt(r.interest_portion) for r in rows), 2),
			"total_principal_posted": flt(sum(flt(r.principal_portion) for r in posted_rows), 2),
			"total_interest_posted": flt(sum(flt(r.interest_portion) for r in posted_rows), 2),
		}

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
			filters={"tranche": ["in", [t.name for t in tranches]], "docstatus": ["!=", 2]},
			fields=["tranche", "payment_date", "closing_balance"],
			order_by="payment_date asc",
		)

		schedules = {t.name: [] for t in tranches}
		for row in rows:
			schedules[row.tranche].append((getdate(row.payment_date), flt(row.closing_balance)))

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
	def sync_budget(self, fiscal_year=None):
		"""
		DISABLED - on hold pending a redesign.

		This was built against an assumed Budget schema (one Budget doc per
		Cost Center with a child table of multiple accounts, single
		fiscal_year) that turned out not to match the actual ERPNext v16
		schema on this instance. The real schema, confirmed directly from
		erpnext/accounts/doctype/budget/budget.py on the develop branch:

		- One Budget document covers exactly ONE account (account: DF.Link),
		  not a table of many.
		- from_fiscal_year / to_fiscal_year (a range), not a single
		  fiscal_year field.
		- Critically: validate_account() hard-rejects any account whose
		  report_type isn't "Profit and Loss" - i.e. Budget can only ever
		  cover Income or Expense accounts. A Tranche's Liability Account
		  (principal) can NEVER be budgeted via ERPNext's Budget doctype,
		  with no override. Only interest could ever be synced this way.

		Left disabled rather than removed so the whitelisted method stays
		documented and doesn't silently 404 if anything still calls it.
		"""
		frappe.throw(
			_(
				"Budget sync is currently disabled pending a redesign around ERPNext's actual "
				"Budget schema (one Budget document per account, Income/Expense accounts only - "
				"principal can never be included). Nothing was changed on this Facility."
			)
		)
