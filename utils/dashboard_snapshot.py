"""Development-only persistence for validated dashboard-ready workbook data.

This module stores bounded, normalized dataframe records in a separate
Firestore namespace. It never stores workbook bytes or raw spreadsheet cells.
The namespace is disabled by default and is intentionally separate from the
production ``active_workbook/current`` metadata path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import math
import re
import uuid
from typing import Any, Mapping

import pandas as pd
from firebase_admin import firestore

from .excel_loader import SheetReport, WorkbookReport, normalize_frame
from .firebase_admin_client import get_firestore_client


DEFAULT_NAMESPACE = "bako_sweet_dev"
SNAPSHOT_SCHEMA_VERSION = "dashboard-snapshot-v1"
MAX_CHUNK_BYTES = 700_000
REQUIRED_SEMANTICS = frozenset({"inventory", "inventory_timeline"})
OPTIONAL_SEMANTICS = frozenset({
    "harvest_sales", "harvest_source", "sales_source", "packout_source",
    "purchasing_loads", "po_source", "loads_source", "prices_source", "pp_price_target_source",
})


class DashboardSnapshotError(RuntimeError):
    """Base error for development snapshot publication/restoration."""


class SnapshotValidationError(DashboardSnapshotError):
    """Raised when a report is not safe to publish."""


class ConcurrentSnapshotUpdateError(DashboardSnapshotError):
    """Raised when a stale publisher attempts to advance the active pointer."""


@dataclass(frozen=True)
class SnapshotSource:
    """Validated source metadata supplied by the workbook activation path."""

    workbook_id: str
    filename: str
    source_version: str
    source_checksum: str
    validation_status: str = "validated"
    publisher_uid: str | None = None
    publisher_display_name: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("workbook_id", "filename", "source_version", "source_checksum", "validation_status"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in ("publisher_uid", "publisher_display_name"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be a non-empty string or None")


@dataclass(frozen=True)
class DashboardSnapshot:
    """Immutable snapshot metadata and its restored report."""

    snapshot_id: str
    namespace: str
    source: SnapshotSource
    published_at: datetime
    report: WorkbookReport
    stats: Mapping[str, Any]


def _safe_namespace(namespace: str) -> str:
    value = re.sub(r"[^a-z0-9_]+", "_", str(namespace).strip().casefold()).strip("_")
    if not value:
        raise ValueError("Snapshot namespace must contain letters, numbers, or underscores")
    return value


def _collections(namespace: str) -> tuple[str, str, str]:
    safe = _safe_namespace(namespace)
    return (
        f"{safe}_dashboard_snapshots",
        f"{safe}_dashboard_snapshot_chunks",
        f"{safe}_dashboard_snapshot_active",
    )


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if hasattr(value, "item"):
        return _jsonable(value.item())
    if pd.isna(value):
        return None
    return str(value)


def _frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {str(column): _jsonable(value) for column, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _records_checksum(records: list[dict[str, Any]]) -> str:
    payload = json.dumps(records, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _chunk_records(records: list[dict[str, Any]], *, max_bytes: int = MAX_CHUNK_BYTES) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 2
    for record in records:
        encoded_size = len(json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) + 1
        if current and current_size + encoded_size > max_bytes:
            chunks.append(current)
            current = []
            current_size = 2
        current.append(record)
        current_size += encoded_size
    if current or not chunks:
        chunks.append(current)
    return chunks


def _encoded_chunk_payload(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    """Encode rows compactly while retaining a readable legacy fallback."""
    encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(encoded, compresslevel=6)
    if len(compressed) + 64 < len(encoded):
        return {"encoding": "gzip-json", "rows_gzip": compressed}, len(compressed)
    return {"rows": rows}, len(encoded)


def _decoded_chunk_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Read compressed new chunks and uncompressed snapshots from older versions."""
    rows = payload.get("rows")
    if isinstance(rows, list):
        return rows
    if payload.get("encoding") != "gzip-json":
        return None
    compressed = payload.get("rows_gzip")
    if not isinstance(compressed, (bytes, bytearray)):
        return None
    try:
        decoded = json.loads(gzip.decompress(bytes(compressed)).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, list) else None


def _semantic_frames(report: WorkbookReport) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Select unique source frames referenced by dashboard semantics."""
    frames: dict[str, pd.DataFrame] = {}
    semantic_sheets: dict[str, str] = {}
    for semantic, sheet_name in report.semantic_sheets.items():
        frame = report.frames.get(sheet_name)
        if frame is None:
            raise SnapshotValidationError(f"Dashboard semantic {semantic!r} references missing frame {sheet_name!r}")
        semantic_sheets[str(semantic)] = str(sheet_name)
        frames[str(sheet_name)] = frame.copy()
    missing = sorted(REQUIRED_SEMANTICS.difference(semantic_sheets))
    if missing:
        raise SnapshotValidationError("Required dashboard data is missing: " + ", ".join(missing))
    return frames, semantic_sheets


def _validated_source(source: SnapshotSource) -> None:
    if source.validation_status.casefold() not in {"validated", "passed", "success", "successful"}:
        raise SnapshotValidationError(f"Refusing to publish validation status {source.validation_status!r}")


def _batch_set(client: firestore.Client, references: list[tuple[Any, Mapping[str, Any]]]) -> None:
    for offset in range(0, len(references), 400):
        batch = client.batch()
        for reference, payload in references[offset:offset + 400]:
            batch.set(reference, dict(payload))
        batch.commit()


def _delete_documents(client: firestore.Client, references: list[Any]) -> None:
    for offset in range(0, len(references), 400):
        batch = client.batch()
        for reference in references[offset:offset + 400]:
            batch.delete(reference)
        if references[offset:offset + 400]:
            batch.commit()


def _get_documents(client: firestore.Client, references: list[Any]) -> list[Any]:
    """Read a set of Firestore documents in one RPC when supported.

    The small fallback keeps the in-memory test clients and older adapters
    working while production Firebase clients use ``get_all`` to avoid one
    network round trip per snapshot chunk.
    """
    if not references:
        return []
    get_all = getattr(client, "get_all", None)
    if callable(get_all):
        return list(get_all(references))
    return [reference.get() for reference in references]


def publish_dashboard_snapshot(
    report: WorkbookReport,
    source: SnapshotSource,
    *,
    client: firestore.Client | None = None,
    namespace: str = DEFAULT_NAMESPACE,
    expected_active_version: str | None = None,
    max_chunk_bytes: int = MAX_CHUNK_BYTES,
) -> DashboardSnapshot:
    """Publish validated dashboard data and atomically advance its dev pointer."""
    _validated_source(source)
    if report is None or not report.frames or not report.semantic_sheets:
        raise SnapshotValidationError("The workbook report contains no dashboard-ready frames")
    frames, semantic_sheets = _semantic_frames(report)
    client = client or get_firestore_client()
    snapshot_id = f"{source.source_checksum[:16]}-{uuid.uuid4().hex[:16]}"
    published_at = datetime.now(timezone.utc)
    snapshot_collection, chunk_collection, active_collection = _collections(namespace)
    snapshot_ref = client.collection(snapshot_collection).document(snapshot_id)
    chunk_references: list[Any] = []
    manifests: list[dict[str, Any]] = []
    frame_write_refs: list[tuple[Any, Mapping[str, Any]]] = []
    total_rows = 0
    total_bytes = 0
    try:
        for frame_name, original_frame in frames.items():
            frame = normalize_frame(original_frame)
            records = _frame_records(frame)
            chunks = _chunk_records(records, max_bytes=max_chunk_bytes)
            frame_checksum = _records_checksum(records)
            chunk_ids: list[str] = []
            for index, rows in enumerate(chunks):
                chunk_id = f"{snapshot_id}__{hashlib.sha1(frame_name.encode()).hexdigest()[:10]}__{index:05d}"
                chunk_ref = client.collection(chunk_collection).document(chunk_id)
                chunk_references.append(chunk_ref)
                chunk_ids.append(chunk_id)
                encoded_payload, stored_bytes = _encoded_chunk_payload(rows)
                total_bytes += stored_bytes
                total_rows += len(rows)
                frame_write_refs.append((chunk_ref, {
                    "snapshot_id": snapshot_id,
                    "frame_name": frame_name,
                    "chunk_index": index,
                    "row_count": len(rows),
                    "records_checksum": _records_checksum(rows),
                    **encoded_payload,
                }))
            manifests.append({
                "frame_name": frame_name,
                "columns": [str(column) for column in frame.columns],
                "row_count": len(records),
                "chunk_ids": chunk_ids,
                "records_checksum": frame_checksum,
                "datetime_columns": [str(column) for column in frame.columns if pd.api.types.is_datetime64_any_dtype(frame[column])],
                "diagnostics": {
                    "semantic": next((semantic for semantic, name in semantic_sheets.items() if name == frame_name), None),
                    "status": getattr(report.sheet_reports.get(frame_name), "status", "Restored from validated snapshot."),
                    "formula_cells": int(getattr(report.sheet_reports.get(frame_name), "formula_cells", 0)),
                    "formula_errors": int(getattr(report.sheet_reports.get(frame_name), "formula_errors", 0)),
                    "missing_formula_results": int(getattr(report.sheet_reports.get(frame_name), "missing_formula_results", 0)),
                    "formula_error_cells": list(getattr(report.sheet_reports.get(frame_name), "formula_error_cells", []) or []),
                    "missing_formula_cells": list(getattr(report.sheet_reports.get(frame_name), "missing_formula_cells", []) or []),
                    "missing_values": dict(getattr(report.sheet_reports.get(frame_name), "missing_values", {}) or {}),
                    "elapsed_ms": float(getattr(report.sheet_reports.get(frame_name), "elapsed_ms", 0.0)),
                },
            })
        _batch_set(client, frame_write_refs)
        metadata = {
            "snapshot_id": snapshot_id,
            "namespace": _safe_namespace(namespace),
            "status": "complete",
            "publication_outcome": "successful",
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "source_workbook_id": source.workbook_id,
            "source_filename": source.filename,
            "source_version": source.source_version,
            "source_checksum": source.source_checksum,
            "publication_outcome": "successful",
            "validation_status": source.validation_status,
            "publisher_uid": source.publisher_uid,
            "publisher_display_name": source.publisher_display_name,
            "published_at": published_at,
            "preferred_sheet": report.preferred_sheet,
            "available_sheets": [str(item) for item in report.available_sheets],
            "semantic_sheets": semantic_sheets,
            "deferred_sheets": [str(item) for item in report.deferred_sheets],
            "validation_warning_count": len(report.warnings),
            "validation_warnings": [str(item)[:500] for item in report.warnings[:50]],
            "timings": {str(key): float(value) for key, value in report.timings.items()},
            "missing_semantics": sorted(OPTIONAL_SEMANTICS.difference(semantic_sheets)),
            "manifests": manifests,
            "stats": {
                "frame_count": len(manifests),
                "chunk_count": len(frame_write_refs),
                "row_count": total_rows,
                "payload_bytes": total_bytes,
            },
        }
        snapshot_ref.set(metadata)
        stored_metadata = snapshot_ref.get().to_dict() or {}
        if stored_metadata.get("status") != "complete" or stored_metadata.get("snapshot_id") != snapshot_id:
            raise DashboardSnapshotError("Snapshot metadata verification failed")
        stored_chunks = {
            snapshot.id: snapshot.to_dict() or {}
            for snapshot in _get_documents(client, chunk_references)
        }
        if len(stored_chunks) != len(chunk_references):
            raise DashboardSnapshotError("Snapshot chunk verification failed: one or more chunks are missing.")
        for chunk_ref, payload in frame_write_refs:
            stored_chunk = stored_chunks.get(chunk_ref.id)
            if stored_chunk is None:
                raise DashboardSnapshotError(f"Snapshot chunk verification failed: {chunk_ref.id}")
            if stored_chunk.get("records_checksum") != payload["records_checksum"] or stored_chunk.get("row_count") != payload["row_count"]:
                raise DashboardSnapshotError(f"Snapshot chunk verification failed: {chunk_ref.id}")
        # Reconstruct and validate the complete report before the active
        # pointer can move. A failed restoration therefore cannot invalidate
        # the previously active snapshot.
        restored = _restore_snapshot_document(
            client,
            metadata,
            namespace=namespace,
            stored_chunks=stored_chunks,
        )
        pointer_ref = client.collection(active_collection).document("current")
        pointer = {
            "snapshot_id": snapshot_id,
            "snapshot_version": snapshot_id,
            "namespace": _safe_namespace(namespace),
            "published_at": published_at,
            "source_filename": source.filename,
            "source_version": source.source_version,
            "source_checksum": source.source_checksum,
            "publisher_uid": source.publisher_uid,
            "publisher_display_name": source.publisher_display_name,
        }
        if expected_active_version is None:
            pointer_ref.set(pointer)
        else:
            transaction = client.transaction()

            @firestore.transactional
            def commit_pointer(transaction: firestore.Transaction) -> None:
                current_snapshot = pointer_ref.get(transaction=transaction)
                current_version = (current_snapshot.to_dict() or {}).get("snapshot_version") if current_snapshot.exists else None
                if current_version != expected_active_version:
                    raise ConcurrentSnapshotUpdateError(
                        f"Active dashboard snapshot changed from expected {expected_active_version!r} to {current_version!r}"
                    )
                transaction.set(pointer_ref, pointer)

            commit_pointer(transaction)
        return DashboardSnapshot(snapshot_id, _safe_namespace(namespace), source, published_at, restored, metadata["stats"])
    except Exception:
        # Metadata and chunks are disposable if the pointer was not advanced.
        # The previous active pointer remains untouched.
        try:
            _delete_documents(client, chunk_references)
            snapshot_ref.delete()
        except Exception:
            pass
        raise


def _restore_snapshot_document(
    client: firestore.Client,
    metadata: Mapping[str, Any],
    *,
    namespace: str,
    stored_chunks: Mapping[str, Mapping[str, Any]] | None = None,
) -> WorkbookReport:
    _, chunk_collection, _ = _collections(namespace)
    frames: dict[str, pd.DataFrame] = {}
    manifests = list(metadata.get("manifests", []))
    if stored_chunks is None:
        chunk_refs_by_id = {
            str(chunk_id): client.collection(chunk_collection).document(str(chunk_id))
            for manifest in manifests
            for chunk_id in manifest.get("chunk_ids", [])
        }
        stored_chunks = {
            snapshot.id: snapshot.to_dict() or {}
            for snapshot in _get_documents(client, list(chunk_refs_by_id.values()))
        }
    for manifest in manifests:
        records: list[dict[str, Any]] = []
        for chunk_id in manifest.get("chunk_ids", []):
            payload = stored_chunks.get(str(chunk_id), {})
            rows = _decoded_chunk_rows(payload)
            if rows is None or payload.get("records_checksum") != _records_checksum(rows):
                raise DashboardSnapshotError(f"Snapshot chunk is missing or corrupt: {chunk_id}")
            records.extend(rows)
        if len(records) != int(manifest.get("row_count", -1)) or _records_checksum(records) != manifest.get("records_checksum"):
            raise DashboardSnapshotError(f"Snapshot frame verification failed: {manifest.get('frame_name')}")
        frame = pd.DataFrame(records, columns=manifest.get("columns") or None)
        for column in manifest.get("datetime_columns", []):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], errors="coerce")
        frames[str(manifest["frame_name"])] = normalize_frame(frame)
    semantic_sheets = {str(key): str(value) for key, value in (metadata.get("semantic_sheets") or {}).items()}
    reports = {
        sheet_name: SheetReport(
            name=sheet_name,
            status=str((next((item.get("diagnostics", {}) for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), {}) or {}).get("status") or "Restored from validated development snapshot."),
            semantic=(next((item.get("diagnostics", {}).get("semantic") for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), None) or next((semantic for semantic, name in semantic_sheets.items() if name == sheet_name), None)),
            row_count=len(frame),
            columns=[str(column) for column in frame.columns],
            missing_values=dict((next((item.get("diagnostics", {}) for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), {}) or {}).get("missing_values", {}) or {}),
            formula_cells=int((next((item.get("diagnostics", {}) for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), {}) or {}).get("formula_cells", 0)),
            formula_errors=int((next((item.get("diagnostics", {}) for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), {}) or {}).get("formula_errors", 0)),
            missing_formula_results=int((next((item.get("diagnostics", {}) for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), {}) or {}).get("missing_formula_results", 0)),
            formula_error_cells=list((next((item.get("diagnostics", {}) for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), {}) or {}).get("formula_error_cells", []) or []),
            missing_formula_cells=list((next((item.get("diagnostics", {}) for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), {}) or {}).get("missing_formula_cells", []) or []),
            elapsed_ms=float((next((item.get("diagnostics", {}) for item in metadata.get("manifests", []) if item.get("frame_name") == sheet_name), {}) or {}).get("elapsed_ms", 0.0)),
            frame=frame,
        )
        for sheet_name, frame in frames.items()
    }
    return WorkbookReport(
        available_sheets=[str(item) for item in metadata.get("available_sheets", [])],
        preferred_sheet=str(metadata.get("preferred_sheet") or ""),
        frames=frames,
        sheet_status={name: report.status for name, report in reports.items()},
        warnings=[str(item) for item in metadata.get("validation_warnings", [])] or [f"Restored from development snapshot published at {metadata.get('published_at')}"],
        sheet_reports=reports,
        semantic_sheets=semantic_sheets,
        timings={str(key): float(value) for key, value in (metadata.get("timings") or {}).items()},
        content_id=str(metadata.get("source_checksum") or ""),
        deferred_sheets=[str(item) for item in metadata.get("deferred_sheets", [])],
    )


def restore_dashboard_snapshot(
    *,
    client: firestore.Client | None = None,
    namespace: str = DEFAULT_NAMESPACE,
    snapshot_id: str | None = None,
) -> DashboardSnapshot | None:
    """Restore the active complete snapshot, verifying every chunk."""
    client = client or get_firestore_client()
    snapshot_collection, _, active_collection = _collections(namespace)
    if snapshot_id is None:
        pointer = client.collection(active_collection).document("current").get()
        if not pointer.exists:
            return None
        snapshot_id = str((pointer.to_dict() or {}).get("snapshot_id") or "")
    if not snapshot_id:
        return None
    metadata = client.collection(snapshot_collection).document(snapshot_id).get()
    if not metadata.exists:
        raise DashboardSnapshotError(f"Active snapshot metadata is missing: {snapshot_id}")
    payload = metadata.to_dict() or {}
    if payload.get("status") != "complete":
        raise DashboardSnapshotError(f"Snapshot is not complete: {snapshot_id}")
    report = _restore_snapshot_document(client, payload, namespace=namespace)
    source = SnapshotSource(
        workbook_id=str(payload["source_workbook_id"]),
        filename=str(payload["source_filename"]),
        source_version=str(payload["source_version"]),
        source_checksum=str(payload["source_checksum"]),
        validation_status=str(payload.get("validation_status", "validated")),
        publisher_uid=payload.get("publisher_uid"),
        publisher_display_name=payload.get("publisher_display_name"),
    )
    published_at = payload.get("published_at")
    if not isinstance(published_at, datetime):
        published_at = datetime.now(timezone.utc)
    return DashboardSnapshot(str(payload["snapshot_id"]), _safe_namespace(namespace), source, published_at, report, payload.get("stats", {}))


def active_snapshot_version(*, client: firestore.Client | None = None, namespace: str = DEFAULT_NAMESPACE) -> str | None:
    """Return the active version for optimistic refresh publishing."""
    client = client or get_firestore_client()
    _, _, active_collection = _collections(namespace)
    snapshot = client.collection(active_collection).document("current").get()
    return (snapshot.to_dict() or {}).get("snapshot_version") if snapshot.exists else None


def record_snapshot_refresh(
    *,
    snapshot_id: str,
    workbook_id: str,
    user_uid: str,
    first_name: str,
    display_name: str,
    client: firestore.Client | None = None,
    namespace: str = DEFAULT_NAMESPACE,
) -> dict[str, Any]:
    """Record a verified user's successful refresh on the active pointer.

    The workbook snapshot remains immutable.  Only the active pointer's
    refresh metadata is updated, using Firestore's server timestamp so every
    authorized session sees the same completion time and identity.
    """
    values = {
        "snapshot_id": str(snapshot_id).strip(),
        "workbook_id": str(workbook_id).strip(),
        "user_uid": str(user_uid).strip(),
        "first_name": str(first_name).strip(),
        "display_name": str(display_name).strip(),
    }
    if not all(values.values()):
        raise ValueError("Refresh metadata requires a snapshot, workbook, and verified user identity.")
    client = client or get_firestore_client()
    _, _, active_collection = _collections(namespace)
    pointer_ref = client.collection(active_collection).document("current")
    transaction = client.transaction()
    payload = {
        "last_refresh_snapshot_id": values["snapshot_id"],
        "last_refresh_workbook_id": values["workbook_id"],
        "last_refresh_uid": values["user_uid"],
        "last_refresh_first_name": values["first_name"],
        "last_refresh_display_name": values["display_name"],
        "last_refresh_at": firestore.SERVER_TIMESTAMP,
    }

    @firestore.transactional
    def write_refresh(transaction: firestore.Transaction) -> None:
        current = pointer_ref.get(transaction=transaction)
        current_data = current.to_dict() or {}
        if not current.exists or str(current_data.get("snapshot_id") or "") != values["snapshot_id"]:
            raise ConcurrentSnapshotUpdateError("The active dashboard snapshot changed before refresh metadata was recorded.")
        transaction.set(pointer_ref, payload, merge=True)

    write_refresh(transaction)
    refreshed = pointer_ref.get().to_dict() or {}
    return {
        "snapshot_id": refreshed.get("last_refresh_snapshot_id"),
        "workbook_id": refreshed.get("last_refresh_workbook_id"),
        "uid": refreshed.get("last_refresh_uid"),
        "first_name": refreshed.get("last_refresh_first_name"),
        "display_name": refreshed.get("last_refresh_display_name"),
        "refreshed_at": refreshed.get("last_refresh_at"),
    }


def read_snapshot_refresh_metadata(
    *,
    snapshot_id: str,
    client: firestore.Client | None = None,
    namespace: str = DEFAULT_NAMESPACE,
) -> dict[str, Any] | None:
    """Read the shared refresh metadata for the currently active snapshot."""
    client = client or get_firestore_client()
    _, _, active_collection = _collections(namespace)
    payload = client.collection(active_collection).document("current").get().to_dict() or {}
    if str(payload.get("last_refresh_snapshot_id") or "") != str(snapshot_id):
        return None
    return {
        "snapshot_id": payload.get("last_refresh_snapshot_id"),
        "workbook_id": payload.get("last_refresh_workbook_id"),
        "uid": payload.get("last_refresh_uid"),
        "first_name": payload.get("last_refresh_first_name"),
        "display_name": payload.get("last_refresh_display_name"),
        "refreshed_at": payload.get("last_refresh_at"),
    }
