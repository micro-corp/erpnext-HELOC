import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate


class HELOCTranche(Document):
	def validate(self):
		# Keep current_balance sane if nothing has been posted yet
		if self.is_new() and not self.current_balance:
			self.current_balance = self.original_principal

		self.validate_single_revolving_tranche()
		self.warn_if_fixed_without_revolving()
		self.validate_accounts()
		self.validate_closed_status()

	def warn_if_fixed_without_revolving(self):
		"""
		Non-blocking - Fixed tranches normally originate from carving out
		part of the Revolving Portion, so creating one by hand before a
		Revolving tranche exists is unusual (though not necessarily wrong,
		e.g. importing historical data). Flag it rather than silently
		allowing it to look intentional.
		"""
		if self.tranche_type != "Fixed (Pr\u00eat Li\u00e9)" or not self.heloc or not self.is_new():
			return

		has_revolving = frappe.get_all(
			"HELOC Tranche",
			filters={"heloc": self.heloc, "tranche_type": "Revolving"},
			limit=1,
		)
		if not has_revolving:
			frappe.msgprint(
				_(
					"This facility has no Revolving tranche yet. Fixed tranches are usually carved "
					"out of the Revolving Portion - double check this is intentional (e.g. importing "
					"an existing tranche) rather than a sign the Revolving tranche is missing."
				),
				indicator="orange",
				alert=True,
			)

	def validate_closed_status(self):
		if self.status == "Closed" and abs(flt(self.current_balance)) > 0.01:
			frappe.throw(
				_(
					"Can't set Status to Closed while Current Balance is {0}. Post remaining "
					"payments first, or correct the balance if the tranche is actually paid off."
				).format(self.current_balance)
			)

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

	def on_trash(self):
		posted = frappe.get_all(
			"HELOC Amortization Entry",
			filters={"tranche": self.name, "docstatus": 1},
			limit=1,
		)
		if posted:
			frappe.throw(
				_(
					"This tranche has posted payment(s) with linked Journal Entries and can't be "
					"deleted. Set Status to Closed instead."
				)
			)

	@frappe.whitelist()
	def get_schedule_rows(self):
		"""
		Every HELOC Amortization Entry against this tranche (Draft,
		Submitted, or Cancelled), oldest first. Used by the client script
		to render the summary stats and charts - Draft rows are included
		deliberately, so a generated-but-not-yet-submitted schedule shows
		up as a projection/simulation before a single Journal Entry exists.
		"""
		return frappe.get_all(
			"HELOC Amortization Entry",
			filters={"tranche": self.name, "docstatus": ["!=", 2]},
			fields=[
				"name", "payment_date", "opening_balance", "scheduled_payment",
				"principal_portion", "interest_portion", "closing_balance",
				"docstatus", "entry_type", "journal_entry",
			],
			order_by="payment_date asc",
		)

	@frappe.whitelist()
	def generate_schedule(self):
		"""
		Builds a standard level-payment amortization schedule for a
		Fixed (Prêt Lié) tranche as a set of Draft HELOC Amortization Entry
		documents - one per scheduled payment. Nothing posts to the GL at
		this point; each entry only posts when it's individually Submitted
		(directly, or via Post Next Payment). That makes the freshly
		generated schedule a safe what-if simulation you can review (and
		even delete/regenerate) before committing anything.

		Revolving tranches don't amortize on a fixed formula (rate/balance
		both float), so this is refused for those - add manual entries
		instead as statements arrive.
		"""
		if self.tranche_type == "Revolving":
			frappe.throw(
				_(
					"Revolving tranches don't have a fixed amortization schedule "
					"since the balance and rate both change. Add manual payment "
					"entries instead each statement."
				)
			)

		missing = []
		if not self.original_principal:
			missing.append(_("Original Principal"))
		if self.interest_rate is None:
			missing.append(_("Interest Rate"))
		if not self.tranche_term_months:
			missing.append(_("Tranche Term (Months)"))
		if not self.total_amortization_months:
			missing.append(_("Total Amortization (Months)"))
		if not self.start_date:
			missing.append(_("Start Date"))
		if missing:
			frappe.throw(_("Required before generating a schedule: {0}").format(", ".join(missing)))

		if self.tranche_term_months > self.total_amortization_months:
			frappe.throw(_("Tranche Term ({0} months) can't be longer than Total Amortization ({1} months).").format(
				self.tranche_term_months, self.total_amortization_months
			))

		existing = frappe.get_all("HELOC Amortization Entry", filters={"tranche": self.name}, limit=1)
		if existing:
			frappe.throw(
				_(
					"This tranche already has amortization entries. Delete the existing Draft "
					"entries (or cancel/delete the Submitted ones, if that's really what you want) "
					"before regenerating."
				)
			)

		principal = flt(self.original_principal)
		monthly_rate = flt(self.interest_rate) / 100 / 12
		n_amort = int(self.total_amortization_months)
		n_term = int(self.tranche_term_months)

		# Payment is calculated off the FULL amortization period, even though
		# only n_term rows get generated - standard Canadian mortgage/Prêt
		# Lié convention. If the term equals the full amortization, this
		# tranche pays itself off completely; otherwise a balance remains at
		# the end of the term for renewal into a new rate/tranche.
		if monthly_rate > 0:
			payment = principal * monthly_rate / (1 - (1 + monthly_rate) ** -n_amort)
		else:
			payment = principal / n_amort

		balance = principal
		fully_amortizes_within_term = n_term >= n_amort

		created = 0
		for i in range(n_term):
			payment_date = add_months(getdate(self.start_date), i + 1)
			interest = balance * monthly_rate
			principal_portion = payment - interest

			# Only absorb rounding into the final row if the term actually
			# reaches full payoff - otherwise a real balance should remain
			# for renewal, not be artificially zeroed out.
			if fully_amortizes_within_term and i == n_term - 1:
				principal_portion = balance
				payment_local = principal_portion + interest
			else:
				payment_local = payment

			closing = balance - principal_portion

			entry = frappe.new_doc("HELOC Amortization Entry")
			entry.update({
				"tranche": self.name,
				"payment_date": payment_date,
				"entry_type": "Scheduled",
				"opening_balance": flt(balance, 2),
				"scheduled_payment": flt(payment_local, 2),
				"principal_portion": flt(principal_portion, 2),
				"interest_portion": flt(interest, 2),
				"closing_balance": flt(closing, 2),
			})
			entry.insert()
			created += 1

			balance = closing

		self.current_balance = self.original_principal
		self.save()
		frappe.msgprint(
			_(
				"Generated {0} Draft schedule entries over the {1}-month term (payment calculated "
				"on a {2}-month amortization). Nothing has posted to the GL yet - review them, then "
				"Submit each one (or use Post Next Payment) as real statements arrive."
			).format(created, n_term, n_amort)
		)

	@frappe.whitelist()
	def post_next_payment(self):
		"""
		Convenience wrapper around Submit: finds the earliest Draft
		amortization entry for this tranche and submits it. Submitting is
		what actually posts the Journal Entry now (see
		HELOCAmortizationEntry.before_submit) - this just saves opening the
		entry and clicking Submit yourself for the common case of working
		through entries in date order.
		"""
		entries = frappe.get_all(
			"HELOC Amortization Entry",
			filters={"tranche": self.name, "docstatus": 0},
			fields=["name"],
			order_by="payment_date asc",
		)
		if not entries:
			frappe.throw(_("No Draft amortization entries remain. Add one manually, or use Generate Schedule first."))

		entry = frappe.get_doc("HELOC Amortization Entry", entries[0].name)
		entry.submit()
		frappe.msgprint(_("Submitted {0}, posting Journal Entry {1}.").format(entry.name, entry.journal_entry))
		return entry.name
