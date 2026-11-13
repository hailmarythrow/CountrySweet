"""Storage-provider abstraction for future shared workbook persistence.

No provider is enabled here. This module deliberately fails clearly instead
of pretending that Firebase Storage or OneDrive/SharePoint is configured.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from datetime import datetime
import json
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

import requests


class StorageProvider(str, Enum):
    UNCONFIGURED = "unconfigured"
    FIREBASE_STORAGE = "firebase_storage"
    ONEDRIVE_SHAREPOINT = "onedrive_sharepoint"


class StorageNotConfiguredError(RuntimeError):
    """Raised when a provider has not yet been deliberately configured."""


class GraphAuthenticationError(RuntimeError):
    """Raised when a Microsoft Graph access token is unavailable or rejected."""


class GraphRequestError(RuntimeError):
    """Raised when Microsoft Graph cannot resolve the configured file."""


class WorkbookValidationError(RuntimeError):
    """Raised when a fetched workbook fails validation and cannot be published."""


@dataclass(frozen=True)
class WorkbookStorageReference:
    provider: StorageProvider
    source_reference: str
    drive_id: str | None = None
    item_id: str | None = None


@dataclass(frozen=True)
class RemoteWorkbookVersion:
    """Stable Graph metadata used to detect a changed remote workbook."""

    reference: WorkbookStorageReference
    filename: str
    version_id: str | None
    etag: str | None
    ctag: str | None
    last_modified_at: datetime | None
    size_bytes: int | None
    web_url: str | None

    @property
    def source_version(self) -> str:
        return self.version_id or self.etag or self.ctag or (
            self.last_modified_at.isoformat() if self.last_modified_at else "unknown"
        )


@dataclass(frozen=True)
class WorkbookValidationOutcome:
    """Adapter-neutral result required before a remote version is published."""

    is_valid: bool
    validation_status: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidatedRemoteWorkbook:
    content: bytes
    remote_version: RemoteWorkbookVersion
    validation: WorkbookValidationOutcome


class GraphAccessTokenProvider(Protocol):
    """A future Entra/MSAL integration supplies short-lived access tokens."""

    def get_access_token(self) -> str:
        ...


class WorkbookStorage(Protocol):
    """Interface implemented later by Firebase Storage or OneDrive adapters."""

    provider: StorageProvider

    def upload(self, content: bytes, filename: str) -> WorkbookStorageReference:
        ...

    def download(self, reference: WorkbookStorageReference) -> bytes:
        ...

    def get_remote_version(self, reference: WorkbookStorageReference) -> RemoteWorkbookVersion:
        ...

    def has_changed(self, reference: WorkbookStorageReference, known_version: str) -> bool:
        ...


class UnconfiguredWorkbookStorage:
    """Explicit placeholder that never creates a fake persistent connection."""

    provider = StorageProvider.UNCONFIGURED

    def upload(self, content: bytes, filename: str) -> WorkbookStorageReference:
        raise StorageNotConfiguredError(
            "No workbook storage provider is configured. Enable Firebase Storage "
            "or configure a OneDrive/SharePoint adapter before publishing files."
        )

    def download(self, reference: WorkbookStorageReference) -> bytes:
        raise StorageNotConfiguredError(
            f"Cannot retrieve {reference.source_reference!r}: storage provider "
            f"{reference.provider.value!r} is not configured."
        )


def storage_for(
    provider: StorageProvider | str,
    *,
    token_provider: GraphAccessTokenProvider | None = None,
) -> WorkbookStorage:
    """Return an explicitly configured adapter, or a safe unavailable adapter."""
    selected = StorageProvider(provider)
    if selected is StorageProvider.ONEDRIVE_SHAREPOINT and token_provider is not None:
        return MicrosoftGraphWorkbookStorage(token_provider)
    if selected is not StorageProvider.UNCONFIGURED:
        raise StorageNotConfiguredError(
            f"{selected.value} is reserved for a future adapter; no file transfer was attempted."
        )
    return UnconfiguredWorkbookStorage()


def local_upload_reference(path: str | Path) -> WorkbookStorageReference:
    """Describe a local upload without claiming it is shared or persistent."""
    return WorkbookStorageReference(StorageProvider.UNCONFIGURED, str(Path(path)))


def graph_source_reference(drive_id: str, item_id: str) -> str:
    """Build a stable source reference without using a public sharing link."""
    if not drive_id.strip() or not item_id.strip():
        raise ValueError("Graph drive_id and item_id are required")
    return f"graph://drives/{drive_id}/items/{item_id}"


class MicrosoftGraphWorkbookStorage:
    """Disabled-until-authorized Graph adapter for OneDrive/SharePoint files.

    The adapter uses delegated Graph access supplied by an external token
    provider. It does not implement login, cache tokens, or accept local paths.
    """

    provider = StorageProvider.ONEDRIVE_SHAREPOINT

    def __init__(
        self,
        token_provider: GraphAccessTokenProvider,
        *,
        graph_base_url: str = "https://graph.microsoft.com/v1.0",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = graph_base_url.rstrip("/")
        self._timeout = timeout_seconds

    def select_workbook(self, drive_id: str, item_id: str) -> RemoteWorkbookVersion:
        """Resolve an existing workbook by stable drive/item IDs."""
        reference = WorkbookStorageReference(
            self.provider,
            graph_source_reference(drive_id, item_id),
            drive_id=drive_id,
            item_id=item_id,
        )
        version = self.get_remote_version(reference)
        if not version.filename.casefold().endswith(".xlsx"):
            raise GraphRequestError(f"Selected Graph item is not an .xlsx workbook: {version.filename}")
        return version

    def upload(self, content: bytes, filename: str) -> WorkbookStorageReference:
        raise StorageNotConfiguredError(
            "Graph workbook selection is read-only in this preparation phase; "
            "publishing back to OneDrive/SharePoint is not enabled."
        )

    def download(self, reference: WorkbookStorageReference) -> bytes:
        content, _ = self.fetch_latest(reference)
        return content

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        try:
            token = self._token_provider.get_access_token()
        except Exception as exc:  # pragma: no cover - provider is supplied later
            raise GraphAuthenticationError(f"Unable to obtain a Microsoft Graph access token: {exc}") from exc
        if not token or not token.strip():
            raise GraphAuthenticationError("Microsoft Graph access token is empty")
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            response = requests.request(method, url, headers=headers, timeout=self._timeout, **kwargs)
        except requests.RequestException as exc:
            raise GraphRequestError(f"Microsoft Graph request failed: {exc}") from exc
        if response.status_code in {401, 403}:
            raise GraphAuthenticationError(
                f"Microsoft Graph returned HTTP {response.status_code}; verify delegated access and admin consent."
            )
        if not response.ok:
            detail = response.text[:500]
            raise GraphRequestError(f"Microsoft Graph returned HTTP {response.status_code}: {detail}")
        return response

    def get_remote_version(self, reference: WorkbookStorageReference) -> RemoteWorkbookVersion:
        drive_id, item_id = self._require_graph_ids(reference)
        path = f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}"
        response = self._request(
            "GET",
            path,
            params={"$select": "id,name,eTag,cTag,lastModifiedDateTime,size,file,webUrl,parentReference"},
        )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise GraphRequestError("Microsoft Graph returned invalid file metadata JSON") from exc
        if payload.get("id") != item_id:
            raise GraphRequestError("Graph returned metadata for a different item ID")
        if payload.get("file") is None:
            raise GraphRequestError("Selected Graph item is not a file")
        return self._version_from_payload(reference, payload)

    def has_changed(self, reference: WorkbookStorageReference, known_version: str) -> bool:
        return self.get_remote_version(reference).source_version != known_version

    def fetch_latest(self, reference: WorkbookStorageReference) -> tuple[bytes, RemoteWorkbookVersion]:
        drive_id, item_id = self._require_graph_ids(reference)
        path = f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}/content"
        response = self._request("GET", path, headers={"Accept": "application/octet-stream"})
        version = self.get_remote_version(reference)
        return response.content, version

    def fetch_and_validate(
        self,
        reference: WorkbookStorageReference,
        validator: Callable[[bytes], WorkbookValidationOutcome],
    ) -> ValidatedRemoteWorkbook:
        content, remote_version = self.fetch_latest(reference)
        validation = validator(content)
        if not validation.is_valid:
            raise WorkbookValidationError(
                f"Remote workbook {remote_version.filename!r} failed validation: {validation.validation_status}"
            )
        return ValidatedRemoteWorkbook(content, remote_version, validation)

    @staticmethod
    def _require_graph_ids(reference: WorkbookStorageReference) -> tuple[str, str]:
        if reference.provider is not StorageProvider.ONEDRIVE_SHAREPOINT:
            raise ValueError("Microsoft Graph requires an OneDrive/SharePoint storage reference")
        if not reference.drive_id or not reference.item_id:
            raise ValueError("Graph storage references must include drive_id and item_id")
        return reference.drive_id, reference.item_id

    @staticmethod
    def _version_from_payload(reference: WorkbookStorageReference, payload: Mapping[str, Any]) -> RemoteWorkbookVersion:
        modified = payload.get("lastModifiedDateTime")
        if isinstance(modified, str):
            modified = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        return RemoteWorkbookVersion(
            reference=reference,
            filename=str(payload.get("name") or ""),
            # Graph's eTag/cTag change when the remote file changes; the
            # driveItem id remains stable and is kept on the reference.
            version_id=payload.get("eTag") or payload.get("cTag") or (modified.isoformat() if modified else None),
            etag=payload.get("eTag"),
            ctag=payload.get("cTag"),
            last_modified_at=modified,
            size_bytes=payload.get("size"),
            web_url=payload.get("webUrl"),
        )


def storage_for_graph(token_provider: GraphAccessTokenProvider) -> MicrosoftGraphWorkbookStorage:
    """Construct Graph storage only when an authenticated token provider exists."""
    return MicrosoftGraphWorkbookStorage(token_provider)


def publish_validated_metadata(
    metadata: Any,
    validation: WorkbookValidationOutcome,
    *,
    client: Any = None,
    active_document_id: str = "current",
) -> str:
    """Publish metadata only after the injected validation result passes."""
    if not validation.is_valid:
        raise WorkbookValidationError(
            f"Refusing to publish unvalidated workbook metadata: {validation.validation_status}"
        )
    from .workbook_metadata import publish_workbook_metadata

    return publish_workbook_metadata(metadata, client=client, active_document_id=active_document_id)
