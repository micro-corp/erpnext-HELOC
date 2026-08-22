import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate


class HELOCTranche(Document):
	def validate(self):
		# Keep current_balance sane if nothing has been posted yet
		if not self.amortization_schedule and not self.current_balance:
			self.current_balance = self.original_principal

		if self.tranche_type == "Revolving" and self.heloc:
			duplicate = frappe.get_all(
				"HELOC Tranche",
				filters={
					"heloc": self.heloc,
					"tranche_type": "Revolving",
					"name": ["!=", self.name or ""],
				},
				limit=1,
			)
			if duplicate:
				frappe.throw(_("This HELOC Facility already has a Revolving tranche ({0}). Only one is expected.").format(duplicate[0].name))

	@frappe.whitelist()
	def generate_schedule(self):
		"""
		Builds a standard level-payment amortization schedule for a
		Fixed (Prêt Lié) tranche. Revolving tranches don't amortize on
		a fixed formula (rate/balance both float), so this is refused
		for those - add/update rows manually instead as statements arrive.
		"""
		if self.tranche_type == "Revolving":
			frappe.throw(
				_(
					"Revolving tranches don't have a fixed amortization schedule "
					"since the balance and rate both change. Add/edit schedule "
					"rows manually each statement instead."
				)
			)

		if not (self.original_principal and self.interest_rate and self.term_months and self.start_date):
			frappe.throw(_("Original Principal, Interest Rate, Term (Months) and Start Date are all required to generate a schedule."))

		if self.amortization_schedule:
			frappe.throw(_("Schedule already has rows. Clear the existing schedule table first if you want to regenerate it."))

		principal = flt(self.original_principal)
		monthly_rate = flt(self.interest_rate) / 100 / 12
		n = int(self.term_months)

		if monthly_rate > 0:
			payment = principal * monthly_rate / (1 - (1 + monthly_rate) ** -n)
		else:
			payment = principal / n

		balance = principal
		payment_date = getdate(self.start_date)

		for i in range(n):
			payment_date = add_months(getdate(self.start_date), i + 1)
			interest = balance * monthly_rate
			principal_portion = payment - interest

			# last row: absorb any rounding so closing balance lands exactly at 0
			if i == n - 1:
				principal_portion = balance
				payment_local = principal_portion + interest
			else:
				payment_local = payment

			closing = balance - principal_portion

			self.append("amortization_schedule", {
				"payment_date": payment_date,
				"opening_balance": round(balance, 2),
				"scheduled_payment": round(payment_local, 2),
				"principal_portion": round(principal_portion, 2),
				"interest_portion": round(interest, 2),
				"closing_balance": round(closing, 2),
				"posted": 0,
			})

			balance = closing

		self.current_balance = self.original_principal
		self.save()
		frappe.msgprint(_("Generated {0} schedule rows.").format(n))

	@frappe.whitelist()
	def post_next_payment(self):
		"""
		Finds the earliest unposted schedule row, creates and submits a
		Journal Entry for it (debit interest + principal, credit bank),
		links it back to the row, and updates current_balance.
		Nothing posts automatically - this only runs when you click it.
		"""
		unposted = [r for r in self.amortization_schedule if not r.posted]
		if not unposted:
			frappe.throw(_("No unposted schedule rows remain."))

		row = sorted(unposted, key=lambda r: getdate(r.payment_date))[0]

		if not (self.liability_account and self.interest_expense_account and self.bank_account):
			frappe.throw(_("Liability Account, Interest Expense Account and Bank Account must all be set before posting."))

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = row.payment_date
		je.user_remark = _("HELOC payment - {0} - {1}").format(self.tranche_name, row.payment_date)

		accounts = []
		if flt(row.interest_portion) > 0:
			accounts.append({
				"account": self.interest_expense_account,
				"debit_in_account_currency": flt(row.interest_portion),
				"credit_in_account_currency": 0,
			})
		if flt(row.principal_portion) > 0:
			accounts.append({
				"account": self.liability_account,
				"debit_in_account_currency": flt(row.principal_portion),
				"credit_in_account_currency": 0,
			})
		accounts.append({
			"account": self.bank_account,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": flt(row.scheduled_payment),
		})

		je.set("accounts", accounts)
		je.insert()
		je.submit()

		row.posted = 1
		row.journal_entry = je.name
		self.current_balance = row.closing_balance
		self.save()

		if self.heloc:
			frappe.get_doc("HELOC Facility", self.heloc).refresh_balance()

		frappe.msgprint(_("Posted Journal Entry {0} for {1}.").format(je.name, row.payment_date))
		return je.name
