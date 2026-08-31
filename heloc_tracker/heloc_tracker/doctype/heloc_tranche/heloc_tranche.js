frappe.ui.form.on("HELOC Tranche", {
	setup(frm) {
		// Straight type matches - these aren't contra fields, so no
		// asset/liability crossover here (unlike the Facility's credit
		// limit memo accounts below).
		frm.set_query("liability_account", () => ({
			filters: { company: frm.doc.company, root_type: "Liability", is_group: 0 },
		}));
		frm.set_query("interest_expense_account", () => ({
			filters: { company: frm.doc.company, root_type: "Expense", is_group: 0 },
		}));
		frm.set_query("bank_account", () => ({
			filters: { company: frm.doc.company, root_type: "Asset", is_group: 0 },
		}));
	},

	refresh(frm) {
		if (frm.is_new()) return;

		add_schedule_buttons(frm);

		frm.add_custom_button(__("Add Manual Payment"), () => {
			frappe.new_doc("HELOC Amortization Entry", {
				tranche: frm.doc.name,
				entry_type: "Manual",
				payment_date: frappe.datetime.get_today(),
				opening_balance: frm.doc.current_balance,
			});
		});

		load_schedule_and_render(frm);
	},
});

function add_schedule_buttons(frm) {
	// Generate Schedule - only offered for Fixed tranches with no
	// amortization entries yet at all (Draft or Submitted).
	if (frm.doc.tranche_type === "Fixed (Pr\u00eat Li\u00e9)") {
		frappe.db.count("HELOC Amortization Entry", { filters: { tranche: frm.doc.name } }).then((count) => {
			if (count > 0) return;
			frm.add_custom_button(__("Generate Schedule"), () => {
				frappe.confirm(
					__(
						"Generate the full amortization schedule as Draft entries from Original " +
						"Principal, Rate, Term and Start Date? Nothing posts to the GL until you " +
						"Submit each entry - this is safe to use as a simulation."
					),
					() => {
						frm.call("generate_schedule").then(() => {
							frm.reload_doc();
						});
					}
				);
			});
		});
	}

	// Post Next Payment - only offered when a Draft entry exists to submit.
	frappe.db.count("HELOC Amortization Entry", { filters: { tranche: frm.doc.name, docstatus: 0 } }).then((count) => {
		if (count === 0) return;
		frm.add_custom_button(__("Post Next Payment"), () => {
			frappe.confirm(
				__(
					"This Submits the earliest Draft amortization entry for this tranche, which " +
					"creates and submits its Journal Entry. Continue?"
				),
				() => {
					frm.call("post_next_payment").then(() => {
						frm.reload_doc();
					});
				}
			);
		}).addClass("btn-primary");
	});
}

function load_schedule_and_render(frm) {
	frm.call("get_schedule_rows").then((r) => {
		const rows = r.message || [];
		render_tranche_summary(frm, rows);
		render_tranche_charts(frm, rows);
	});
}

function render_tranche_summary(frm, rows) {
	const wrapper = frm.get_field("summary_html").$wrapper;
	rows = rows.slice().sort((a, b) => new Date(a.payment_date) - new Date(b.payment_date));

	if (!rows.length) {
		wrapper.html(`<p class="text-muted small">${__("Generate a schedule or add a manual payment entry to see a summary here.")}</p>`);
		return;
	}

	const beginning_balance = flt(rows[0].opening_balance);
	const ending_balance = flt(rows[rows.length - 1].closing_balance);
	const total_principal = rows.reduce((sum, r) => sum + flt(r.principal_portion), 0);
	const total_interest = rows.reduce((sum, r) => sum + flt(r.interest_portion), 0);
	const posted_rows = rows.filter((r) => r.docstatus === 1);
	const posted_principal = posted_rows.reduce((sum, r) => sum + flt(r.principal_portion), 0);
	const posted_interest = posted_rows.reduce((sum, r) => sum + flt(r.interest_portion), 0);
	const draft_count = rows.filter((r) => r.docstatus === 0).length;

	const stat = (label, value) => `
		<div style="flex: 1 1 150px; min-width: 150px; padding: 10px 14px; border: 1px solid var(--border-color); border-radius: 6px;">
			<div class="text-muted small">${label}</div>
			<div style="font-size: 16px; font-weight: 600;">${format_currency(value, frm.doc.currency)}</div>
		</div>`;

	const draft_note = draft_count
		? `<p class="text-muted small" style="margin-top: 8px;">${__("{0} Draft entr{1} included in the totals above but not yet posted to the GL.", [draft_count, draft_count === 1 ? "y" : "ies"])}</p>`
		: "";

	wrapper.html(`
		<div style="display: flex; flex-wrap: wrap; gap: 10px;">
			${stat(__("Beginning Balance"), beginning_balance)}
			${stat(__("Ending Balance (incl. Drafts)"), ending_balance)}
			${stat(__("Total Principal (full schedule)"), total_principal)}
			${stat(__("Total Interest (full schedule)"), total_interest)}
			${stat(__("Principal Posted to Date"), posted_principal)}
			${stat(__("Interest Posted to Date"), posted_interest)}
		</div>
		${draft_note}
	`);
}

function render_tranche_charts(frm, rows) {
	rows = rows.slice().sort((a, b) => new Date(a.payment_date) - new Date(b.payment_date));

	const burndown_wrapper = frm.get_field("burndown_chart").$wrapper;
	const pi_wrapper = frm.get_field("principal_interest_chart").$wrapper;
	burndown_wrapper.empty();
	pi_wrapper.empty();

	if (!rows.length) {
		burndown_wrapper.html(`<p class="text-muted small">${__("Generate a schedule or add a manual payment entry to see charts here.")}</p>`);
		pi_wrapper.html("");
		return;
	}

	const labels = rows.map((r) => frappe.datetime.str_to_user(r.payment_date));

	// Burndown: opening balance of row 1, then closing balance of every row after
	const balance_values = [flt(rows[0].opening_balance), ...rows.map((r) => flt(r.closing_balance))];
	const balance_labels = [__("Start"), ...labels];

	new frappe.Chart(burndown_wrapper[0], {
		title: __("Balance Burndown (incl. Drafts)"),
		data: {
			labels: balance_labels,
			datasets: [{ name: __("Balance"), values: balance_values }],
		},
		type: "line",
		height: 220,
		colors: ["#c0392b"],
		lineOptions: { regionFill: 1 },
	});

	new frappe.Chart(pi_wrapper[0], {
		title: __("Principal vs Interest per Payment"),
		data: {
			labels: labels,
			datasets: [
				{ name: __("Principal"), values: rows.map((r) => flt(r.principal_portion)) },
				{ name: __("Interest"), values: rows.map((r) => flt(r.interest_portion)) },
			],
		},
		type: "bar",
		height: 220,
		colors: ["#2d9d78", "#e67e22"],
		barOptions: { stacked: 1 },
	});
}

function flt(v) {
	return parseFloat(v) || 0;
}
