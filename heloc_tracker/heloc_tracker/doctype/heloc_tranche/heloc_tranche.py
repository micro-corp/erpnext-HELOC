import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate

PROTECTED_ROW_FIELDS = (
	"payment_date",
	"opening_balance",
	"scheduled_payment",
	"principal_portion",
	"interest_portion",
	"closing_balance",
)


class HELOCTranche(Document):
	def validate(self):
		# Keep current_balance sane if nothing has been posted yet
		if not self.amortization_schedule and not self.current_balance:
			self.current_balance = self.original_principal

		self.validate_single_revolving_tranche()
		self.validate_accounts()
		self.validate_posted_rows_unchanged()

	def validate_single_revolving_tranche(self):
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

	def validate_accounts(self):
		"""
		Cross-checks the three linked accounts actually belong to this
		Tranche's Company and are the expected type - nothing in ERPNext
		itself stops you from picking a mismatched or Group account here.
		"""
		checks = [
			("liability_account", "Liability", _("Liability Account")),
			("interest_expense_account", "Expense", _("Interest Expense Account")),
			("bank_account", "Asset", _("Bank Account")),
		]
		for fieldname, expected_root_type, label in checks:
			account = self.get(fieldname)
			if not account:
				continue

			acc = frappe.db.get_value(
				"Account", account, ["company", "root_type", "is_group"], as_dict=True
			)
			if not acc:
				continue

			if self.company and acc.company != self.company:
				frappe.throw(
					_("{0} ({1}) belongs to company {2}, not {3}.").format(
						label, account, acc.company, self.company
					)
				)
			if acc.is_group:
				frappe.throw(_("{0} ({1}) is a Group account and can't be posted to directly. Pick a ledger account.").format(label, account))
			if acc.root_type != expected_root_type:
				frappe.throw(
					_("{0} ({1}) is a {2} account - expected {3}.").format(
						label, account, acc.root_type, expected_root_type
					)
				)

	def validate_posted_rows_unchanged(self):
		"""
		Once a schedule row is posted (has a submitted Journal Entry behind
		it), its financial figures must not be editable in the grid -
		otherwise the app's numbers can silently drift away from what's
		actually in the GL. Also blocks deleting a posted row outright.
		"""
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before:
			return

		old_rows = {r.name: r for r in before.amortization_schedule if r.posted}
		new_rows = {r.name: r for r in self.amortization_schedule}

		for name, old_row in old_rows.items():
			new_row = new_rows.get(name)
			if new_row is None:
				frappe.throw(
					_("Row for {0} is posted (Journal Entry {1}) and can't be deleted.").format(
						old_row.payment_date, old_row.journal_entry
					)
				)
			for field in PROTECTED_ROW_FIELDS:
				old_val = old_row.get(field)
				new_val = new_row.get(field)
				changed = (old_val != new_val) if field == "payment_date" else (flt(old_val) != flt(new_val))
				if changed:
					frappe.throw(
						_("Row for {0} is already posted (Journal Entry {1}) - its figures can't be edited.").format(
							old_row.payment_date, old_row.journal_entry
						)
					)

	def on_trash(self):
		posted = [r for r in self.amortization_schedule if r.posted]
		if posted:
			frappe.throw(
				_("This tranche has {0} posted payment(s) with linked Journal Entries and can't be deleted. Set Status to Closed instead.").format(len(posted))
			)

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

		missing = []
		if not self.original_principal:
			missing.append(_("Original Principal"))
		if self.interest_rate is None:
			missing.append(_("Interest Rate"))
		if not self.term_months:
			missing.append(_("Term (Months)"))
		if not self.start_date:
			missing.append(_("Start Date"))
		if missing:
			frappe.throw(_("Required before generating a schedule: {0}").format(", ".join(missing)))

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
				"opening_balance": flt(balance, 2),
				"scheduled_payment": flt(payment_local, 2),
				"principal_portion": flt(principal_portion, 2),
				"interest_portion": flt(interest, 2),
				"closing_balance": flt(closing, 2),
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

		if abs(flt(row.opening_balance) - flt(self.current_balance)) > 0.01:
			frappe.throw(
				_(
					"This row's Opening Balance ({0}) doesn't match the tranche's Current Balance ({1}). "
					"Check for a skipped, reordered, or edited row before posting."
				).format(row.opening_balance, self.current_balance)
			)

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

		try:
			row.posted = 1
			row.journal_entry = je.name
			self.current_balance = row.closing_balance
			self.save()

			if self.heloc:
				frappe.get_doc("HELOC Facility", self.heloc).refresh_balance()
		except Exception:
			frappe.log_error(
				title="HELOC Tracker: payment JE posted but linking failed",
				message=frappe.get_traceback(),
			)
			frappe.throw(
				_(
					"Journal Entry {0} was created and submitted, but updating this tranche's record failed. "
					"The JE exists in your GL - check it manually and re-run Post Next Payment only after "
					"confirming this row isn't already covered, or you'll double-post."
				).format(je.name)
			)

		frappe.msgprint(_("Posted Journal Entry {0} for {1}.").format(je.name, row.payment_date))
		return je.name
