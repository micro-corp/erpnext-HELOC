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

		if (frm.doc.cost_center) {
			frm.add_custom_button(__("Sync Budget"), () => {
				open_sync_budget_dialog(frm);
			}, __("Budget"));
		}
	},
});

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
				fieldname: "term_months",
				label: __("Term (Months)"),
				fieldtype: "Int",
				reqd: 1,
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

function open_sync_budget_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Sync Budget"),
		fields: [
			{
				fieldname: "fiscal_year",
				label: __("Fiscal Year"),
				fieldtype: "Link",
				options: "Fiscal Year",
				reqd: 1,
				default: frappe.defaults.get_default("fiscal_year"),
			},
			{
				fieldname: "note",
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"Sums every linked tranche's interest and principal for this Fiscal Year into an ERPNext Budget document against this facility's Cost Center. If a Budget already exists for that Cost Center and Fiscal Year, it's replaced (cancelled and re-created as an amendment if already submitted)."
				)}</p>`,
			},
		],
		primary_action_label: __("Sync"),
		primary_action(values) {
			frm.call("sync_budget", { fiscal_year: values.fiscal_year }).then(() => {
				dialog.hide();
				frm.reload_doc();
			});
		},
	});
	dialog.show();
}
