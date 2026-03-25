from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr


LEGACY_ENCOUNTER_FIELD = "custom_microcare_old_id"


def coerce_names(value: Any) -> list[str]:
	if value in (None, "", []):
		return []
	if isinstance(value, str):
		try:
			parsed = json.loads(value)
			if isinstance(parsed, list):
				value = parsed
			else:
				value = [part.strip() for part in value.split(",") if part.strip()]
		except Exception:
			value = [part.strip() for part in value.split(",") if part.strip()]
	if not isinstance(value, list):
		frappe.throw(_("encounter_names must be a list or comma-separated string"))
	return [cstr(item).strip() for item in value if cstr(item).strip()]


def supports_legacy_encounter_field() -> bool:
	return LEGACY_ENCOUNTER_FIELD in {
		cstr(df.fieldname or "").strip() for df in frappe.get_meta("Patient Encounter").fields
	}


def get_imported_patient_encounter_names(limit: int = 0, patient: str | None = None) -> list[str]:
	if not supports_legacy_encounter_field():
		frappe.throw(
			_(
				"Patient Encounter field {0} is required for imported-encounter cleanup."
			).format(frappe.bold(LEGACY_ENCOUNTER_FIELD))
		)

	filters: list[list[Any]] = [["Patient Encounter", LEGACY_ENCOUNTER_FIELD, "!=", ""]]
	if patient:
		filters.append(["Patient Encounter", "patient", "=", patient])

	return frappe.get_all(
		"Patient Encounter",
		filters=filters,
		pluck="name",
		limit_page_length=cint(limit) if cint(limit) > 0 else 0,
		order_by="modified desc",
	)


def _delete_linked_rows(
	doctype: str,
	fieldname: str,
	encounter_names: list[str],
	dry_run: bool,
	extra_filters: dict[str, Any] | None = None,
) -> int:
	total = 0
	for start in range(0, len(encounter_names), 500):
		chunk = encounter_names[start : start + 500]
		filters = {fieldname: ["in", chunk], **(extra_filters or {})}
		names = frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=0)
		total += len(names)
		if not dry_run and names:
			frappe.db.delete(doctype, {"name": ["in", names]})
	return total


def _clear_encounter_medical_fields(encounter_names: list[str], dry_run: bool) -> dict[str, int]:
	diagnosis_rows = symptoms_rows = encounters_updated = 0

	for start in range(0, len(encounter_names), 500):
		chunk = encounter_names[start : start + 500]
		diagnosis_rows += len(
			frappe.get_all(
				"Patient Encounter Diagnosis",
				filters={
					"parent": ["in", chunk],
					"parenttype": "Patient Encounter",
					"parentfield": "diagnosis",
				},
				pluck="name",
				limit_page_length=0,
			)
		)
		symptoms_rows += len(
			frappe.get_all(
				"Patient Encounter Symptom",
				filters={
					"parent": ["in", chunk],
					"parenttype": "Patient Encounter",
					"parentfield": "symptoms",
				},
				pluck="name",
				limit_page_length=0,
			)
		)

		if dry_run:
			encounters_updated += len(chunk)
			continue

		frappe.db.delete(
			"Patient Encounter Diagnosis",
			{
				"parent": ["in", chunk],
				"parenttype": "Patient Encounter",
				"parentfield": "diagnosis",
			},
		)
		frappe.db.delete(
			"Patient Encounter Symptom",
			{
				"parent": ["in", chunk],
				"parenttype": "Patient Encounter",
				"parentfield": "symptoms",
			},
		)
		for encounter_name in chunk:
			frappe.db.set_value(
				"Patient Encounter",
				encounter_name,
				{"encounter_comment": ""},
				update_modified=False,
			)
			encounters_updated += 1

	return {
		"encounters_updated": encounters_updated,
		"diagnosis_rows_deleted": diagnosis_rows,
		"symptom_rows_deleted": symptoms_rows,
	}


def cleanup_patient_encounter_history_impl(
	encounter_names: list[str],
	dry_run: int = 0,
	clear_encounter_fields: int = 1,
) -> dict[str, Any]:
	encounter_names = [name for name in encounter_names if name]
	dry_run = cint(dry_run)
	clear_encounter_fields = cint(clear_encounter_fields)

	if not encounter_names:
		return {
			"ok": True,
			"dry_run": bool(dry_run),
			"encounters": 0,
			"clinical_notes_deleted": 0,
			"patient_medical_records_deleted": 0,
			"encounters_updated": 0,
			"diagnosis_rows_deleted": 0,
			"symptom_rows_deleted": 0,
		}

	clinical_notes_deleted = _delete_linked_rows(
		"Clinical Note",
		"reference_name",
		encounter_names,
		bool(dry_run),
		extra_filters={"reference_doc": "Patient Encounter"},
	)
	patient_medical_records_deleted = _delete_linked_rows(
		"Patient Medical Record",
		"reference_name",
		encounter_names,
		bool(dry_run),
		extra_filters={"reference_doctype": "Patient Encounter"},
	)

	clear_stats = {
		"encounters_updated": 0,
		"diagnosis_rows_deleted": 0,
		"symptom_rows_deleted": 0,
	}
	if clear_encounter_fields:
		clear_stats = _clear_encounter_medical_fields(encounter_names, bool(dry_run))

	if not dry_run:
		frappe.db.commit()

	return {
		"ok": True,
		"dry_run": bool(dry_run),
		"encounters": len(encounter_names),
		"clinical_notes_deleted": clinical_notes_deleted,
		"patient_medical_records_deleted": patient_medical_records_deleted,
		**clear_stats,
	}
