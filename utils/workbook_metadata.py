"""Versioned shared-workbook metadata stored in Firestore.

This module stores metadata only. Workbook bytes remain outside Firestore and
are represented by a storage provider/reference pair. The dashboard does not
import this module yet; it is a preparation layer for a later authenticated
publishing workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any

from firebase_admin import firestore

from .firebase_admin_client import get_firestore_client
from .entra_auth import AuthenticatedEmployee


ACTIVE_WORKBOOK_COLLECTION = "active_workbook"
ACTIVE_WORKBOOK_DOCUMENT = "current"
WORKBOOK_VERSIONS_COLLECTION = "workbook_versions"
WORKBOOK_REFRESH_HISTORY_COLLECTION = "workbook_refresh_history"


def sha256_bytes(content: bytes) -> str:
    """Return a SHA-256 checksum for workbook bytes without storing them."""
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class WorkbookMetadata:
    """A Firestore-safe description of one successfully validated workbook."""

    active_workbook_id: str
    filename: str
    workbook_version: str
    storage_provider: str
    source_reference: str
    last_successful_update: datetime
    validation_status: str
    file_checksum: str
    publishing_user_id: str | None = None
    remote_drive_id: str | None = None
    remote_item_id: str | None = None
    remote_modified_at: datetime | None = None
    remote_etag: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "active_workbook_id",
            "filename",
            "workbook_version",
            "storage_provider",
            "source_reference",
            "validation_status",
            "file_checksum",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.last_successful_update, datetime):
            raise TypeError("last_successful_update must be a datetime")
        if self.last_successful_update.tzinfo is None:
            raise ValueError("last_successful_update must include timezone information")
        if self.publishing_user_id is not None and not isinstance(self.publishing_user_id, str):
            raise TypeError("publishing_user_id must be a string or None")
        for field_name in ("remote_drive_id", "remote_item_id", "remote_etag"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be a non-empty string or None")
        if self.remote_modified_at is not None:
            if not isinstance(self.remote_modified_at, datetime):
                raise TypeError("remote_modified_at must be a datetime or None")
            if self.remote_modified_at.tzinfo is None:
                raise ValueError("remote_modified_at must include timezone information")

    def to_firestore(self) -> dict[str, Any]:
        """Serialize metadata without any workbook binary content."""
        return {
            "active_workbook_id": self.active_workbook_id,
            "filename": self.filename,
            "workbook_version": self.workbook_version,
            "storage_provider": self.storage_provider,
            "source_reference": self.source_reference,
            "last_successful_update": self.last_successful_update.astimezone(timezone.utc),
            "publishing_user_id": self.publishing_user_id,
            "validation_status": self.validation_status,
            "file_checksum": self.file_checksum,
            "remote_drive_id": self.remote_drive_id,
            "remote_item_id": self.remote_item_id,
            "remote_modified_at": self.remote_modified_at.astimezone(timezone.utc) if self.remote_modified_at else None,
            "remote_etag": self.remote_etag,
        }

    @classmethod
    def from_firestore(cls, document: dict[str, Any]) -> "WorkbookMetadata":
        value = document.get("last_successful_update")
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime):
            raise ValueError("Firestore metadata has no valid last_successful_update")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return cls(
            active_workbook_id=str(document["active_workbook_id"]),
            filename=str(document["filename"]),
            workbook_version=str(document["workbook_version"]),
            storage_provider=str(document["storage_provider"]),
            source_reference=str(document["source_reference"]),
            last_successful_update=value,
            publishing_user_id=document.get("publishing_user_id"),
            validation_status=str(document["validation_status"]),
            file_checksum=str(document["file_checksum"]),
            remote_drive_id=document.get("remote_drive_id"),
            remote_item_id=document.get("remote_item_id"),
            remote_modified_at=_parse_optional_datetime(document.get("remote_modified_at")),
            remote_etag=document.get("remote_etag"),
        )


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError("Firestore metadata has an invalid optional datetime")
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _version_document_id(metadata: WorkbookMetadata) -> str:
    """Create a stable, slash-safe Firestore ID for a workbook version."""
    key = f"{metadata.active_workbook_id}\x00{metadata.workbook_version}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ConcurrentMetadataUpdateError(RuntimeError):
    """Raised when a refresh was based on an older active workbook version."""


def publish_workbook_metadata(
    metadata: WorkbookMetadata,
    *,
    client: firestore.Client | None = None,
    active_document_id: str = ACTIVE_WORKBOOK_DOCUMENT,
    expected_active_version: str | None = None,
) -> str:
    """Persist one version and atomically point ``current`` at it.

    Version documents are never removed or replaced by a later version, so an
    update preserves the prior metadata for audit and rollback workflows.
    """
    client = client or get_firestore_client()
    version_id = _version_document_id(metadata)
    version_ref = client.collection(WORKBOOK_VERSIONS_COLLECTION).document(version_id)
    active_ref = client.collection(ACTIVE_WORKBOOK_COLLECTION).document(active_document_id)
    version_document = metadata.to_firestore() | {
        "metadata_type": "workbook_version",
        "version_document_id": version_id,
        "published_at": datetime.now(timezone.utc),
    }
    active_document = metadata.to_firestore() | {
        "metadata_type": "active_workbook_pointer",
        "version_document_id": version_id,
    }
    if expected_active_version is None:
        batch = client.batch()
        batch.set(version_ref, version_document)
        batch.set(active_ref, active_document)
        batch.commit()
    else:
        transaction = client.transaction()

        @firestore.transactional
        def commit_if_current(transaction: firestore.Transaction) -> None:
            snapshot = active_ref.get(transaction=transaction)
            current_version = (snapshot.to_dict() or {}).get("workbook_version") if snapshot.exists else None
            if current_version != expected_active_version:
                raise ConcurrentMetadataUpdateError(
                    f"Active workbook changed from expected {expected_active_version!r} to {current_version!r}"
                )
            transaction.set(version_ref, version_document)
            transaction.set(active_ref, active_document)

        commit_if_current(transaction)
    return version_id


def get_active_workbook_metadata(
    *,
    client: firestore.Client | None = None,
    active_document_id: str = ACTIVE_WORKBOOK_DOCUMENT,
) -> WorkbookMetadata | None:
    """Read the current shared-workbook pointer, if one has been published."""
    client = client or get_firestore_client()
    snapshot = client.collection(ACTIVE_WORKBOOK_COLLECTION).document(active_document_id).get()
    return WorkbookMetadata.from_firestore(snapshot.to_dict() or {}) if snapshot.exists else None


def get_workbook_metadata_version(
    active_workbook_id: str,
    workbook_version: str,
    *,
    client: firestore.Client | None = None,
) -> WorkbookMetadata | None:
    """Read one immutable version from the metadata history."""
    probe = WorkbookMetadata(
        active_workbook_id=active_workbook_id,
        filename="metadata-probe",
        workbook_version=workbook_version,
        storage_provider="metadata-probe",
        source_reference="metadata-probe",
        last_successful_update=datetime.now(timezone.utc),
        validation_status="metadata-probe",
        file_checksum="metadata-probe",
    )
    client = client or get_firestore_client()
    snapshot = client.collection(WORKBOOK_VERSIONS_COLLECTION).document(_version_document_id(probe)).get()
    return WorkbookMetadata.from_firestore(snapshot.to_dict() or {}) if snapshot.exists else None


def list_workbook_metadata_versions(
    active_workbook_id: str,
    *,
    client: firestore.Client | None = None,
) -> list[WorkbookMetadata]:
    """Return all retained versions for one workbook ID, newest first."""
    client = client or get_firestore_client()
    versions: list[WorkbookMetadata] = []
    for snapshot in client.collection(WORKBOOK_VERSIONS_COLLECTION).stream():
        document = snapshot.to_dict() or {}
        if document.get("active_workbook_id") == active_workbook_id:
            versions.append(WorkbookMetadata.from_firestore(document))
    return sorted(versions, key=lambda item: item.last_successful_update, reverse=True)


def record_successful_refresh(
    metadata: WorkbookMetadata,
    employee: AuthenticatedEmployee,
    *,
    validation_outcome: str,
    client: firestore.Client | None = None,
) -> str:
    """Write one refresh audit record for an authenticated publisher/admin.

    The function requires the validated Entra identity object and a successful
    outcome; callers cannot supply a free-form employee name as authorization.
    """
    if not employee.employee_id.strip():
        raise ValueError("An authenticated employee ID is required")
    if not employee.can("publish_workbook"):
        raise PermissionError("Only an authenticated publisher or admin can record a workbook refresh")
    if not validation_outcome.strip() or validation_outcome.casefold() not in {"passed", "validated", "success", "successful"}:
        raise ValueError("Refresh history requires a successful validation outcome")
    client = client or get_firestore_client()
    reference = client.collection(WORKBOOK_REFRESH_HISTORY_COLLECTION).document()
    reference.set({
        "workbook_id": metadata.active_workbook_id,
        "source_version": metadata.workbook_version,
        "successful_refresh_timestamp": metadata.last_successful_update.astimezone(timezone.utc),
        "authenticated_employee_id": employee.employee_id,
        "display_name": employee.display_name,
        "validation_outcome": validation_outcome,
    })
    return reference.id
