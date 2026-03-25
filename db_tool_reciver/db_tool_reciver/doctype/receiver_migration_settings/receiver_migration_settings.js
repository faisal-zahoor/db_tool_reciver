frappe.ui.form.on("Receiver Migration Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Cleanup Imported Encounter History"), () => {
			run_cleanup(frm, "db_tool_reciver.api.cleanup_imported_patient_encounter_history", {
				patient: frm.doc.patient || "",
				limit: frm.doc.limit_rows || 0,
				dry_run: frm.doc.dry_run || 0,
			});
		});

		frm.add_custom_button(__("Cleanup Selected Encounters"), () => {
			if (!frm.doc.encounter_names_json) {
				frappe.msgprint(__("Enter encounter names JSON or comma-separated names first."));
				return;
			}

			run_cleanup(frm, "db_tool_reciver.api.cleanup_patient_encounter_history", {
				encounter_names: frm.doc.encounter_names_json,
				dry_run: frm.doc.dry_run || 0,
				clear_encounter_fields: 1,
			});
		});
	},
});

function run_cleanup(frm, method, args) {
	frappe.call({
		method,
		args,
		freeze: true,
		freeze_message: __("Cleaning migrated medical history..."),
		callback(r) {
			const result = r.message || {};
			frm.set_value("last_cleanup_summary", JSON.stringify(result, null, 2));
			frappe.msgprint(__("Cleanup finished."));
		},
	});
}
