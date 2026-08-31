app_name = "heloc_tracker"
app_title = "HELOC Tracker"
app_publisher = "Jonathan"
app_description = "Borrower-side amortization tracking for hybrid HELOC / Prêt Lié liabilities"
app_email = "you@example.com"
app_license = "MIT"

# v16: the new Desktop screen auto-generates an icon per installed app from
# this hook. logo.png lives under public/images/ - remember bench build
# --app heloc_tracker is required after any change here, static assets
# aren't served until built.
# Ref: https://docs.frappe.io/framework/user/en/apps-page
add_to_apps_screen = [
	{
		"name": "heloc_tracker",
		"title": app_title,
		"route": "/app/heloc-facility",
		"logo": "/assets/heloc_tracker/images/logo.png",
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
