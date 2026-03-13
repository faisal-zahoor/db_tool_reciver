from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime


DEFAULT_COMMIT_EVERY = 200
DEFAULT_EXISTING_CHUNK = 500


def _coerce_rows(rows: Any) -> list[dict[str, Any]]:
    if isinstance(rows, str):
        rows = json.loads(rows)
    if not isinstance(rows, list):
        frappe.throw(_("rows must be a JSON array of objects"))

    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            frappe.throw(_("rows[{0}] must be an object").format(idx))
        normalized.append(dict(row))
    return normalized


def _coerce_lookup_fields(lookup_fields: Any) -> list[str]:
    if lookup_fields in (None, "", []):
        return []
    if isinstance(lookup_fields, str):
        try:
            parsed = json.loads(lookup_fields)
            if isinstance(parsed, list):
                lookup_fields = parsed
            else:
                lookup_fields = [v.strip() for v in lookup_fields.split(",") if v.strip()]
        except Exception:
            lookup_fields = [v.strip() for v in lookup_fields.split(",") if v.strip()]
    if not isinstance(lookup_fields, list):
        frappe.throw(_("lookup_fields must be a list or comma-separated string"))
    return [cstr(v).strip() for v in lookup_fields if cstr(v).strip()]


def _allowed_fields(doctype: str) -> set[str]:
    meta = frappe.get_meta(doctype)
    allowed = {"name"}
    allowed.update({cstr(df.fieldname or "").strip() for df in meta.fields if cstr(df.fieldname or "").strip()})
    return allowed


def _existing_map(doctype: str, key_field: str, keys: list[str], chunk_size: int = DEFAULT_EXISTING_CHUNK) -> dict[str, str]:
    out: dict[str, str] = {}
    if not keys:
        return out

    for start in range(0, len(keys), chunk_size):
        chunk = keys[start : start + chunk_size]
        rows = frappe.get_all(
            doctype,
            filters=[[doctype, key_field, "in", chunk]],
            fields=["name", key_field],
            limit_page_length=0,
        )
        for row in rows:
            key = cstr(row.get(key_field) or "").strip()
            if key:
                out[key] = cstr(row.get("name") or "").strip()
    return out


def _normalize_payload(row: dict[str, Any], allowed_fields: set[str]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k in allowed_fields and v not in (None, "")}


def _lookup_signature(payload: dict[str, Any], lookup_fields: list[str]) -> str:
    parts = [cstr(payload.get(field) or "").strip() for field in lookup_fields]
    return "||".join(parts)


def _find_existing_by_lookup(doctype: str, payload: dict[str, Any], lookup_fields: list[str]) -> str | None:
    filters = []
    for field in lookup_fields:
        value = payload.get(field)
        if value in (None, ""):
            return None
        filters.append([doctype, field, "=", value])
    return frappe.db.get_value(doctype, filters, "name")


def _bulk_upsert_impl(
    doctype: str,
    rows: list[dict[str, Any]],
    key_field: str | None = "name",
    lookup_fields: list[str] | None = None,
    update_existing: int = 1,
    ignore_mandatory: int = 1,
    commit_every: int = DEFAULT_COMMIT_EVERY,
) -> dict[str, Any]:
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(_("DocType {0} not found").format(doctype))

    update_existing = cint(update_existing)
    ignore_mandatory = cint(ignore_mandatory)
    commit_every = max(cint(commit_every), 1)

    allowed = _allowed_fields(doctype)
    key_field = cstr(key_field or "").strip() or None
    lookup_fields = [f for f in (lookup_fields or []) if f]

    if key_field and key_field not in allowed:
        frappe.throw(_("key_field {0} does not exist in {1}").format(key_field, doctype))
    for field in lookup_fields:
        if field not in allowed:
            frappe.throw(_("lookup field {0} does not exist in {1}").format(field, doctype))
    if not key_field and not lookup_fields:
        frappe.throw(_("Provide key_field or lookup_fields for matching"))

    cleaned_rows: list[dict[str, Any]] = []
    keys: list[str] = []
    for row in rows:
        payload = _normalize_payload(row, allowed)
        key_value = cstr(payload.get(key_field) or "").strip() if key_field else ""
        can_process_with_key = bool(key_field and key_value)
        can_process_with_lookup = bool(lookup_fields and all(payload.get(f) not in (None, "") for f in lookup_fields))
        if not (can_process_with_key or can_process_with_lookup):
            continue
        cleaned_rows.append(payload)
        if can_process_with_key:
            keys.append(key_value)

    existing = _existing_map(doctype, key_field, keys) if key_field else {}
    lookup_cache: dict[str, str] = {}

    stats = {
        "doctype": doctype,
        "key_field": key_field,
        "lookup_fields": lookup_fields,
        "received": len(rows),
        "processable": len(cleaned_rows),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "started_on": str(now_datetime()),
        "errors": [],
    }

    for idx, payload in enumerate(cleaned_rows, start=1):
        key_value = cstr(payload.get(key_field) or "").strip() if key_field else ""
        lookup_sig = _lookup_signature(payload, lookup_fields) if lookup_fields else ""
        try:
            existing_name = existing.get(key_value) if key_value else None
            if not existing_name and lookup_fields:
                existing_name = lookup_cache.get(lookup_sig)
                if not existing_name:
                    existing_name = _find_existing_by_lookup(doctype, payload, lookup_fields)
                    if existing_name:
                        lookup_cache[lookup_sig] = existing_name

            if existing_name:
                if not update_existing:
                    stats["skipped"] += 1
                    continue

                doc = frappe.get_doc(doctype, existing_name)
                for fieldname, value in payload.items():
                    if fieldname == "name":
                        continue
                    # Avoid changing the matching key on updates.
                    if fieldname == key_field:
                        continue
                    doc.set(fieldname, value)
                doc.flags.ignore_mandatory = bool(ignore_mandatory)
                doc.save(ignore_permissions=True)
                stats["updated"] += 1
            else:
                new_doc = frappe.get_doc({"doctype": doctype, **payload})
                new_doc.flags.ignore_mandatory = bool(ignore_mandatory)
                new_doc.insert(ignore_permissions=True)
                stats["created"] += 1
                if key_field and key_value:
                    existing[key_value] = new_doc.name
                if lookup_fields:
                    lookup_cache[lookup_sig] = new_doc.name
        except Exception as exc:
            stats["failed"] += 1
            if len(stats["errors"]) < 100:
                identifier = key_value or lookup_sig or "row"
                stats["errors"].append(f"{identifier}: {cstr(exc)}")

        if idx % commit_every == 0:
            frappe.db.commit()

    frappe.db.commit()
    stats["ended_on"] = str(now_datetime())
    return stats


@frappe.whitelist(methods=["POST"])
def bulk_upsert(
    doctype: str,
    rows: list[dict[str, Any]] | str,
    key_field: str | None = "name",
    lookup_fields: list[str] | str | None = None,
    update_existing: int = 1,
    ignore_mandatory: int = 1,
    commit_every: int = DEFAULT_COMMIT_EVERY,
):
    """Bulk create/update rows for a single doctype in one request."""
    parsed_rows = _coerce_rows(rows)
    return _bulk_upsert_impl(
        doctype=doctype,
        rows=parsed_rows,
        key_field=key_field,
        lookup_fields=_coerce_lookup_fields(lookup_fields),
        update_existing=update_existing,
        ignore_mandatory=ignore_mandatory,
        commit_every=commit_every,
    )


@frappe.whitelist(methods=["POST"])
def enqueue_bulk_upsert(
    doctype: str,
    rows: list[dict[str, Any]] | str,
    key_field: str | None = "name",
    lookup_fields: list[str] | str | None = None,
    update_existing: int = 1,
    ignore_mandatory: int = 1,
    commit_every: int = DEFAULT_COMMIT_EVERY,
    queue: str = "long",
    timeout: int = 7200,
):
    """Queue a bulk upsert job and return the job id immediately."""
    parsed_rows = _coerce_rows(rows)
    parsed_lookup_fields = _coerce_lookup_fields(lookup_fields)

    job = frappe.enqueue(
        "db_tool_reciver.api.bulk_upsert",
        queue=queue,
        timeout=cint(timeout),
        doctype=doctype,
        rows=parsed_rows,
        key_field=key_field,
        lookup_fields=parsed_lookup_fields,
        update_existing=update_existing,
        ignore_mandatory=ignore_mandatory,
        commit_every=commit_every,
        enqueue_after_commit=True,
    )

    return {
        "status": "queued",
        "job_id": getattr(job, "id", None),
        "doctype": doctype,
        "count": len(parsed_rows),
    }
