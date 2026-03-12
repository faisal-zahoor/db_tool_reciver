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


def _bulk_upsert_impl(
    doctype: str,
    rows: list[dict[str, Any]],
    key_field: str,
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
    if key_field not in allowed:
        frappe.throw(_("key_field {0} does not exist in {1}").format(key_field, doctype))

    cleaned_rows: list[dict[str, Any]] = []
    keys: list[str] = []
    for row in rows:
        payload = _normalize_payload(row, allowed)
        key_value = cstr(payload.get(key_field) or "").strip()
        if not key_value:
            continue
        cleaned_rows.append(payload)
        keys.append(key_value)

    existing = _existing_map(doctype, key_field, keys)

    stats = {
        "doctype": doctype,
        "key_field": key_field,
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
        key_value = cstr(payload.get(key_field) or "").strip()
        try:
            existing_name = existing.get(key_value)
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
        except Exception as exc:
            stats["failed"] += 1
            if len(stats["errors"]) < 100:
                stats["errors"].append(f"{key_value}: {cstr(exc)}")

        if idx % commit_every == 0:
            frappe.db.commit()

    frappe.db.commit()
    stats["ended_on"] = str(now_datetime())
    return stats


@frappe.whitelist(methods=["POST"])
def bulk_upsert(
    doctype: str,
    rows: list[dict[str, Any]] | str,
    key_field: str = "name",
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
        update_existing=update_existing,
        ignore_mandatory=ignore_mandatory,
        commit_every=commit_every,
    )


@frappe.whitelist(methods=["POST"])
def enqueue_bulk_upsert(
    doctype: str,
    rows: list[dict[str, Any]] | str,
    key_field: str = "name",
    update_existing: int = 1,
    ignore_mandatory: int = 1,
    commit_every: int = DEFAULT_COMMIT_EVERY,
    queue: str = "long",
    timeout: int = 7200,
):
    """Queue a bulk upsert job and return the job id immediately."""
    parsed_rows = _coerce_rows(rows)

    job = frappe.enqueue(
        "db_tool_reciver.api.bulk_upsert",
        queue=queue,
        timeout=cint(timeout),
        doctype=doctype,
        rows=parsed_rows,
        key_field=key_field,
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
