import frappe
from frappe import _


def before_cancel(doc, method=None):
	"""
	Runs before any Journal Entry is cancelled. If this JE was posted by
	HELOC Tracker for a carve-out or a credit limit memo, cancellation is
	blocked by default - unwinding either safely requires more than
	reversing one JE (a carve-out may have payments posted against the new
	tranche already; a credit-limit memo cancel should go through the
	Facility's own button so its link field clears in step). Our own
	controllers set a flag to bypass this when they cancel intentionally.

	Ordinary payment JEs (posted when a HELOC Amortization Entry is
	Submitted) are always allowed to cancel here - see on_cancel below for
	how those get reconciled. HELOCAmortizationEntry.reverse_posting()
	still enforces its own "only the most recently posted entry can be
	cancelled" rule when the reconciliation runs, so an out-of-order
	cancellation attempted from the Journal Entry side is still caught -
	just slightly later in the process, via that throw rolling back this
	cancellation entirely.
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
	payment JE (posted when a HELOC Amortization Entry was Submitted),
	reconcile automatically: cancel the linked HELOC Amortization Entry
	too and let its own on_cancel() revert the Tranche's Current Balance -
	so the app's numbers don't silently drift out of sync with the GL just
	because the cancellation didn't start from our own Submit/Cancel flow.

	HELOCAmortizationEntry.reverse_posting() sees the Journal Entry is
	already cancelled (docstatus 2) at this point and skips re-cancelling
	it, so there's no recursion between the two hooks.
	"""
	entries = frappe.get_all(
		"HELOC Amortization Entry",
		filters={"journal_entry": doc.name, "docstatus": 1},
		pluck="name",
	)
	if not entries:
		return

	for name in entries:
		entry = frappe.get_doc("HELOC Amortization Entry", name)
		entry.cancel()

	frappe.msgprint(
		_(
			"Journal Entry {0} was cancelled - cancelled the linked HELOC Tracker amortization "
			"entry/entries and reverted the tranche balance(s) to match."
		).format(doc.name),
		indicator="orange",
		alert=True,
	)
