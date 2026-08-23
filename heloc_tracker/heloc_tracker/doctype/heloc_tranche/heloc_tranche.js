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
	},
});
