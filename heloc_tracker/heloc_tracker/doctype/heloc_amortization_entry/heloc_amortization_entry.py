import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class HELOCAmortizationEntry(Document):
	"""
	One payment against a HELOC Tranche - either a Scheduled row (created by
	Generate Schedule) or a Manual one (added by hand for a correction,
	back-entry, or off-schedule payment).

	This is a submittable doctype, and Submit is the only thing that posts
	anything to the GL:
	- Draft = not posted. Safe to create, edit, or delete freely. A whole
	  schedule of Draft entries is effectively a what-if simulation - the
	  numbers are all there to review (and feed the Tranche/Facility charts
	  and rollups) without a single Journal Entry existing yet.
	- Submitted = posted. before_submit() creates and submits the Journal
	  Entry and updates the Tranche's Current Balance. After this, the
	  entry's figures are locked (standard Frappe submittable behavior) -
	  correct it via Cancel + Amend, not by editing a submitted doc.
	- Cancelled = reversed. on_cancel() cancels the linked Journal Entry
	  (if it's still submitted - a no-op if the cancellation came from the
	  Journal Entry side already, see journal_entry_hooks.py) and rolls the
	  Tranche's Current Balance back.
	"""

	def validate(self):
		self.validate_tranche()
		self.validate_amounts()
		self.compute_closing_balance()

	def validate_tranche(self):
		if not self.tranche:
			frappe.throw(_("Tranche is required."))
		if not self.company:
			self.company = frappe.db.get_value("HELOC Tranche", self.tranche, "company")

	def validate_amounts(self):
		total = flt(self.principal_portion) + flt(self.interest_portion)
		if abs(total - flt(self.scheduled_payment)) > 0.01:
			frappe.throw(
				_(
					"Principal ({0}) + Interest ({1}) must equal the Payment Amount ({2}) - "
					"otherwise the Journal Entry this posts won't balance."
				).format(self.principal_portion, self.interest_portion, self.scheduled_payment)
			)

	def compute_closing_balance(self):
		self.closing_balance = flt(flt(self.opening_balance) - flt(self.principal_portion), 2)

	def before_submit(self):
		self.post_to_gl()

	def post_to_gl(self):
		"""
		Creates and submits the Journal Entry for this entry (debit interest
		+ principal, credit bank) and updates the parent Tranche's Current
		Balance. Runs as part of Submit - nothing else triggers this.
		"""
		tranche = frappe.get_doc("HELOC Tranche", self.tranche)

		# Row-lock the Tranche for the rest of this transaction so two
		# concurrent submits (double-click, two sessions) can't both read
		# the same Current Balance and post against it twice.
		frappe.db.get_value("HELOC Tranche", tranche.name, for_update=True)
		tranche.reload()

		if tranche.status == "Closed":
			frappe.throw(_("Tranche {0} is Closed - can't post payments against it.").format(tranche.name))

		if not (tranche.liability_account and tranche.interest_expense_account and tranche.bank_account):
			frappe.throw(
				_(
					"Liability Account, Interest Expense Account and Bank Account must all be set "
					"on Tranche {0} before submitting a payment against it."
				).format(tranche.name)
			)

		if not self.skip_opening_balance_check and abs(flt(self.opening_balance) - flt(tranche.current_balance)) > 0.01:
			frappe.throw(
				_(
					"This entry's Opening Balance ({0}) doesn't match {1}'s Current Balance ({2}). "
					"Check for a skipped, reordered, or edited entry before submitting - or tick "
					"'Skip Opening Balance Check' if this is an intentional correction or back-entry."
				).format(self.opening_balance, tranche.name, tranche.current_balance)
			)

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = tranche.company
		je.posting_date = self.payment_date
		je.user_remark = _("HELOC payment - {0} - {1}").format(tranche.tranche_name, self.payment_date)

		# If the parent Facility has a Cost Center set up for Budget-style
		# reporting, tag it on the real expense/liability lines so ERPNext's
		# own reports can filter by it. Not tagged on the Bank line.
		cost_center = frappe.db.get_value("HELOC Facility", tranche.heloc, "cost_center") if tranche.heloc else None

		accounts = []
		if flt(self.interest_portion) > 0:
			accounts.append({
				"account": tranche.interest_expense_account,
				"debit_in_account_currency": flt(self.interest_portion),
				"credit_in_account_currency": 0,
				"cost_center": cost_center,
			})
		if flt(self.principal_portion) > 0:
			accounts.append({
				"account": tranche.liability_account,
				"debit_in_account_currency": flt(self.principal_portion),
				"credit_in_account_currency": 0,
				"cost_center": cost_center,
			})
		accounts.append({
			"account": tranche.bank_account,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": flt(self.scheduled_payment),
		})

		je.set("accounts", accounts)
		je.insert()
		je.submit()

		self.journal_entry = je.name

		tranche.current_balance = self.closing_balance
		tranche.save()

		if tranche.heloc:
			frappe.get_doc("HELOC Facility", tranche.heloc).refresh_balance()

	def on_cancel(self):
		self.reverse_posting()

	def reverse_posting(self):
		"""
		Cancels the linked Journal Entry (if it hasn't already been
		cancelled directly - see journal_entry_hooks.py) and rolls the
		Tranche's Current Balance back to this entry's Opening Balance.

		Only allowed when this entry is the *last* one posted against its
		Tranche (its Closing Balance still matches the Tranche's Current
		Balance). Cancelling an entry buried in the middle of the posted
		history would leave the running balance ambiguous, since Current
		Balance is derived by walking forward one submitted entry at a
		time rather than stored independently per entry - so cancellation
		has to unwind in reverse order, same as the posting order itself.
		"""
		if not self.journal_entry:
			return

		tranche = frappe.get_doc("HELOC Tranche", self.tranche)

		frappe.db.get_value("HELOC Tranche", tranche.name, for_update=True)
		tranche.reload()

		if abs(flt(tranche.current_balance) - flt(self.closing_balance)) > 0.01:
			frappe.throw(
				_(
					"Can't cancel this entry - {0}'s Current Balance ({1}) no longer matches this "
					"entry's Closing Balance ({2}), which means a later entry has posted against it "
					"since. Cancel later entries first, in reverse order."
				).format(tranche.name, tranche.current_balance, self.closing_balance)
			)

		je = frappe.get_doc("Journal Entry", self.journal_entry)
		if je.docstatus == 1:
			frappe.flags.heloc_tracker_allow_cancel = True
			je.cancel()
			frappe.flags.heloc_tracker_allow_cancel = False

		tranche.current_balance = flt(self.opening_balance)
		tranche.save()

		if tranche.heloc:
			frappe.get_doc("HELOC Facility", tranche.heloc).refresh_balance()
