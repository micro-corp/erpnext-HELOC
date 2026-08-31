frappe.ui.form.on("HELOC Facility", {
	setup(frm) {
		// Reference-only field pointing at the 21000-level group account -
		// deliberately allowed to be a Group account, unlike everywhere else.
		frm.set_query("group_liability_account", () => ({
			filters: { company: frm.doc.company, root_type: "Liability" },
		}));

		// Credit Limit memo pair is a genuine contra relationship - the
		// offset side is commonly set up as either an Asset or a Liability
		// account depending on how the person wants it to read on reports,
		// so both fields allow either root_type rather than forcing one.
		const contra_filter = () => ({
			filters: { company: frm.doc.company, root_type: ["in", ["Asset", "Liability"]], is_group: 0 },
		});
		frm.set_query("credit_limit_asset_account", contra_filter);
		frm.set_query("credit_limit_offset_account", contra_filter);

		frm.set_query("cost_center", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));
	},

	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Refresh Balance"), () => {
			frm.call("refresh_balance").then(() => frm.reload_doc());
		});

		frm.add_custom_button(__("Carve Out New Tranche"), () => {
			open_carve_out_dialog(frm);
		}).addClass("btn-primary");

		// Only offer this when no Revolving tranche exists yet for this
		// facility - once one exists, HELOCTranche.validate() blocks a
		// second one anyway, so the button would just fail.
		frappe.db.get_list("HELOC Tranche", {
			filters: { heloc: frm.doc.name, tranche_type: "Revolving" },
			limit: 1,
		}).then((existing) => {
			if (existing.length) return;
			frm.add_custom_button(__("Create Revolving Tranche"), () => {
				frappe.new_doc("HELOC Tranche", {
					heloc: frm.doc.name,
					company: frm.doc.company,
					lender: frm.doc.lender,
					tranche_type: "Revolving",
				});
			}).addClass("btn-primary");
		});

		if (frm.doc.credit_limit_journal_entry) {
			frm.add_custom_button(__("Cancel Credit Limit Posting"), () => {
				frappe.confirm(
					__("This cancels Journal Entry {0}. Continue?", [frm.doc.credit_limit_journal_entry]),
					() => {
						frm.call("cancel_credit_limit_posting").then(() => frm.reload_doc());
					}
				);
			}, __("Credit Limit"));
		} else {
			frm.add_custom_button(__("Post Credit Limit"), () => {
				if (!frm.doc.credit_limit_asset_account || !frm.doc.credit_limit_offset_account) {
					frappe.msgprint(__("Set both Credit Limit Asset Account and Credit Limit Offset Account first."));
					return;
				}
				frappe.confirm(
					__("This posts a submitted Journal Entry debiting {0} and crediting {1} for the full Credit Limit ({2}). Continue?", [
						frm.doc.credit_limit_asset_account,
						frm.doc.credit_limit_offset_account,
						format_currency(frm.doc.credit_limit),
					]),
					() => {
						frm.call("post_credit_limit").then(() => frm.reload_doc());
					}
				);
			}, __("Credit Limit"));
		}

		// Sync Budget is intentionally not wired up here - it was built
		// against an assumed ERPNext Budget schema that doesn't match this
		// instance's actual v16 schema (one Budget per account, Income/
		// Expense accounts only). On hold pending a redesign - see
		// sync_budget()'s docstring in heloc_facility.py for details.

		render_facility_rollup(frm);
		render_facility_burndown(frm);
	},
});

function render_facility_rollup(frm) {
	const wrapper = frm.get_field("rollup_html").$wrapper;
	wrapper.html(`<p class="text-muted small">${__("Loading...")}</p>`);

	frm.call("get_rollup_stats").then((r) => {
		const s = r.message || {};
		if (!s.total_original_principal && s.total_original_principal !== 0) {
			wrapper.html(`<p class="text-muted small">${__("No tranches linked yet.")}</p>`);
			return;
		}

		const stat = (label, value) => `
			<div style="flex: 1 1 150px; min-width: 150px; padding: 10px 14px; border: 1px solid var(--border-color); border-radius: 6px;">
				<div class="text-muted small">${label}</div>
				<div style="font-size: 16px; font-weight: 600;">${format_currency(value, frm.doc.currency)}</div>
			</div>`;

		wrapper.html(`
			<div style="display: flex; flex-wrap: wrap; gap: 10px;">
				${stat(__("Total Original Principal"), s.total_original_principal)}
				${stat(__("Total Current Balance"), s.total_current_balance)}
				${stat(__("Total Principal (full schedule)"), s.total_principal_scheduled)}
				${stat(__("Total Interest (full schedule)"), s.total_interest_scheduled)}
				${stat(__("Principal Posted to Date"), s.total_principal_posted)}
				${stat(__("Interest Posted to Date"), s.total_interest_posted)}
			</div>
		`);
	});
}

function render_facility_burndown(frm) {
	const wrapper = frm.get_field("facility_burndown_chart").$wrapper;
	wrapper.empty();
	wrapper.html(`<p class="text-muted small">${__("Loading...")}</p>`);

	frm.call("get_burndown_data").then((r) => {
		wrapper.empty();
		const data = r.message || { labels: [], values: [] };
		if (!data.labels.length) {
			wrapper.html(`<p class="text-muted small">${__("No schedule rows yet across any linked tranche.")}</p>`);
			return;
		}
		new frappe.Chart(wrapper[0], {
			title: __("Total Balance Across All Tranches"),
			data: {
				labels: data.labels.map((d) => frappe.datetime.str_to_user(d)),
				datasets: [{ name: __("Total Balance"), values: data.values }],
			},
			type: "line",
			height: 240,
			colors: ["#c0392b"],
			lineOptions: { regionFill: 1 },
		});
	});
}

function open_carve_out_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Carve Out New Fixed Tranche from Revolving"),
		fields: [
			{
				fieldname: "tranche_name",
				label: __("New Tranche Name"),
				fieldtype: "Data",
				reqd: 1,
			},
			{
				fieldname: "amount",
				label: __("Amount to Carve Out"),
				fieldtype: "Currency",
				reqd: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "interest_rate",
				label: __("Annual Interest Rate (%)"),
				fieldtype: "Percent",
				reqd: 1,
			},
			{
				fieldname: "tranche_term_months",
				label: __("Tranche Term (Months)"),
				fieldtype: "Int",
				reqd: 1,
				description: __("How long the rate is locked in for"),
			},
			{
				fieldname: "total_amortization_months",
				label: __("Total Amortization (Months)"),
				fieldtype: "Int",
				reqd: 1,
				description: __("Full payoff horizon used to calculate the payment - can be longer than the Term"),
			},
			{
				fieldname: "start_date",
				label: __("Start Date"),
				fieldtype: "Date",
				reqd: 1,
				default: frappe.datetime.get_today(),
			},
			{ fieldtype: "Section Break", label: __("Accounts") },
			{
				fieldname: "liability_account",
				label: __("New Tranche Liability Account"),
				fieldtype: "Link",
				options: "Account",
				reqd: 1,
				description: __("The 2102N child account you've created on your COA for this tranche"),
				get_query: () => ({
					filters: { company: frm.doc.company, root_type: "Liability", is_group: 0 },
				}),
			},
			{
				fieldname: "interest_expense_account",
				label: __("Interest Expense Account"),
				fieldtype: "Link",
				options: "Account",
				reqd: 1,
				get_query: () => ({
					filters: { company: frm.doc.company, root_type: "Expense", is_group: 0 },
				}),
			},
			{
				fieldname: "bank_account",
				label: __("Bank Account (optional)"),
				fieldtype: "Link",
				options: "Account",
				description: __("Leave blank to reuse the Revolving tranche's bank account"),
				get_query: () => ({
					filters: { company: frm.doc.company, root_type: "Asset", is_group: 0 },
				}),
			},
		],
		primary_action_label: __("Carve Out"),
		primary_action(values) {
			frappe.confirm(
				__(
					"This creates {0} as a new Fixed tranche, generates its amortization schedule, and posts a submitted Journal Entry moving {1} from the Revolving Portion to it. Continue?",
					[values.tranche_name, values.amount]
				),
				() => {
					frm.call("carve_out_tranche", values).then(() => {
						dialog.hide();
						frm.reload_doc();
					});
				}
			);
		},
	});
	dialog.show();
}
