from __future__ import annotations

import frappe

from db_tool_reciver.bulk_upsert import (
	DEFAULT_COMMIT_EVERY,
	bulk_upsert_impl,
	coerce_lookup_fields,
	coerce_rows,
)


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
    parsed_rows = coerce_rows(rows)
    return bulk_upsert_impl(
        doctype=doctype,
        rows=parsed_rows,
        key_field=key_field,
        lookup_fields=coerce_lookup_fields(lookup_fields),
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
    parsed_rows = coerce_rows(rows)
    parsed_lookup_fields = coerce_lookup_fields(lookup_fields)

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
