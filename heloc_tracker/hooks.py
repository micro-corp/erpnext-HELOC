app_name = "heloc_tracker"
app_title = "HELOC Tracker"
app_publisher = "Jonathan"
app_description = "Borrower-side amortization tracking for hybrid HELOC / Prêt Lié liabilities"
app_email = "you@example.com"
app_license = "MIT"

# v16: the new Desktop screen auto-generates an icon per installed app from
# this hook. Without it the app still works fully, it just won't get a home
# screen tile - you'd navigate to it via search or a direct URL instead.
# Ref: https://docs.frappe.io/framework/user/en/apps-page
#
# No logo asset is bundled with this app (no /public/images dir), so the
# tile falls back to Frappe's default icon. Add a logo file and point
# "logo" at it (e.g. "/assets/heloc_tracker/images/logo.svg") if you want
# a custom one - I didn't fabricate a path since no such asset exists here.
add_to_apps_screen = [
	{
		"name": "heloc_tracker",
		"title": app_title,
		"route": "/app/heloc-facility",
		"logo": "/assets/heloc_tracker/images/logo.svg",
	}
]

# Keeps the app's numbers honest if a Journal Entry it posted gets touched
# from outside the app's own buttons - see journal_entry_hooks.py.
doc_events = {
	"Journal Entry": {
		"before_cancel": "heloc_tracker.heloc_tracker.journal_entry_hooks.before_cancel",
		"on_cancel": "heloc_tracker.heloc_tracker.journal_entry_hooks.on_cancel",
	}
}

# No fixtures, no scheduled jobs by default.
# Posting is manual (button click) by design, so nothing gets posted
# to the GL without you explicitly reviewing and confirming it.
