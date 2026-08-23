import frappe
from frappe import _
from frappe.utils import flt


def before_cancel(doc, method=None):
	"""
	Runs before any Journal Entry is cancelled. If this JE was posted by
	HELOC Tracker for a carve-out or a credit limit memo, cancellation is
	blocked by default - unwinding either safely requires more than
	reversing one JE (a carve-out may have payments posted against the new
	tranche already; a credit-limit memo cancel should go through the
	Facility's own button so its link field clears in step). Our own
	controllers set a flag to bypass this when they cancel intentionally.
	Ordinary payment JEs (from Post Next Payment) are always allowed to
	cancel - see on_cancel below for how those get reconciled.
	"""
	if frappe.flags.get("heloc_tracker_allow_cancel"):
		return

	carve_out_hit = frappe.get_all(
		"HELOC Tranche",
		filters={"carve_out_journal_entry": doc.name},
		limit=1,
	)
	if carve_out_hit:
		frappe.throw(
			_(
				"{0} is the carve-out entry for HELOC Tranche {1}. Cancelling it here isn't "
				"supported - it can't be safely auto-unwound if payments have posted against "
				"that tranche since. Handle this manually if you need to reverse the carve-out."
			).format(doc.name, carve_out_hit[0].name)
		)

	credit_limit_hit = frappe.get_all(
		"HELOC Facility",
		filters={"credit_limit_journal_entry": doc.name},
		limit=1,
	)
	if credit_limit_hit:
		frappe.throw(
			_(
				"{0} is the Credit Limit memo entry for HELOC Facility {1}. Use the "
				"'Cancel Credit Limit Posting' button on that record instead, so its link "
				"field clears correctly."
			).format(doc.name, credit_limit_hit[0].name)
		)


def on_cancel(doc, method=None):
	"""
	Runs after a Journal Entry is cancelled. If it was a HELOC Tracker
	payment JE (from Post Next Payment), reconcile automatically: un-post
	the schedule row, revert the tranche's Current Balance, and refresh
	the parent Facility - so the app's numbers don't silently drift out
	of sync with the GL just because the cancellation didn't go through
	our own button.
	"""
	rows = frappe.get_all(
		"HELOC Amortization Entry",
		filters={"journal_entry": doc.name},
		fields=["name", "parent", "opening_balance"],
	)
	if not rows:
		return

	for row in rows:
		tranche = frappe.get_doc("HELOC Tranche", row.parent)
		for schedule_row in tranche.amortization_schedule:
			if schedule_row.name == row.name:
				schedule_row.posted = 0
				schedule_row.journal_entry = None
				break
		tranche.current_balance = flt(row.opening_balance)
		tranche.save()

		if tranche.heloc:
			frappe.get_doc("HELOC Facility", tranche.heloc).refresh_balance()

	frappe.msgprint(
		_("Journal Entry {0} was cancelled - reverted the linked HELOC Tracker schedule row(s) and balance(s) to match.").format(doc.name),
		indicator="orange",
		alert=True,
	)
