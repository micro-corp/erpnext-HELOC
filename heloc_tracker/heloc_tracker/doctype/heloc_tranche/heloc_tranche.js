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
		if (!frm.is_new() && frm.doc.tranche_type === "Fixed (Prêt Lié)" && (!frm.doc.amortization_schedule || frm.doc.amortization_schedule.length === 0)) {
			frm.add_custom_button(__("Generate Schedule"), () => {
				frappe.confirm(
					__("Generate the full amortization schedule from Original Principal, Rate, Term and Start Date?"),
					() => {
						frm.call("generate_schedule").then(() => frm.reload_doc());
					}
				);
			});
		}

		const has_unposted = (frm.doc.amortization_schedule || []).some(r => !r.posted);
		if (!frm.is_new() && has_unposted) {
			frm.add_custom_button(__("Post Next Payment"), () => {
				frappe.confirm(
					__("This creates and submits a Journal Entry for the earliest unposted schedule row. Continue?"),
					() => {
						frm.call("post_next_payment").then(() => frm.reload_doc());
					}
				);
			}).addClass("btn-primary");
		}

		render_tranche_charts(frm);
	},
});

function render_tranche_charts(frm) {
	const rows = (frm.doc.amortization_schedule || []).slice().sort((a, b) => new Date(a.payment_date) - new Date(b.payment_date));

	const burndown_wrapper = frm.get_field("burndown_chart").$wrapper;
	const pi_wrapper = frm.get_field("principal_interest_chart").$wrapper;
	burndown_wrapper.empty();
	pi_wrapper.empty();

	if (!rows.length) {
		burndown_wrapper.html(`<p class="text-muted small">${__("Generate or add schedule rows to see charts here.")}</p>`);
		pi_wrapper.html("");
		return;
	}

	const labels = rows.map(r => frappe.datetime.str_to_user(r.payment_date));

	// Burndown: opening balance of row 1, then closing balance of every row after
	const balance_values = [flt(rows[0].opening_balance), ...rows.map(r => flt(r.closing_balance))];
	const balance_labels = [__("Start"), ...labels];

	new frappe.Chart(burndown_wrapper[0], {
		title: __("Balance Burndown"),
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
				{ name: __("Principal"), values: rows.map(r => flt(r.principal_portion)) },
				{ name: __("Interest"), values: rows.map(r => flt(r.interest_portion)) },
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
