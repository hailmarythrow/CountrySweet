"""Firebase Admin/Cloud Firestore access for the dashboard.

Authentication intentionally uses Google Application Default Credentials (ADC)
for local development. Streamlit Cloud can provide a nested ``[firebase_admin]``
service-account mapping through ``st.secrets``. No credential material is
stored in this repository, logged, or returned to the UI.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import firebase_admin
from firebase_admin import credentials, firestore


class FirebaseAdminConfigurationError(RuntimeError):
    """Raised when the deployment cannot safely initialize Firebase Admin."""


def runtime_setting(name: str, default: Any = "") -> Any:
    """Read a setting with local environment variables taking precedence.

    Streamlit Cloud exposes ``st.secrets`` only inside the running app.  The
    import is therefore deliberately lazy so this module remains usable by
    local scripts and tests that do not initialize Streamlit.
    """
    environment_value = os.getenv(name)
    if environment_value is not None and str(environment_value).strip():
        return environment_value
    try:
        import streamlit as st

        secrets = st.secrets
        value = secrets.get(name, default)
        return default if value is None else value
    except Exception:
        # Missing secrets.toml and non-Streamlit callers are normal local
        # states.  They must fall back to the documented default.
        return default


def _configured_project_id() -> str:
    value = str(runtime_setting("FIREBASE_PROJECT_ID", "bako-sweet")).strip()
    return value or "bako-sweet"


def _configured_database_id() -> str:
    value = str(runtime_setting("FIRESTORE_DATABASE_ID", "(default)")).strip()
    return value or "(default)"


def _streamlit_admin_credentials() -> dict[str, Any] | None:
    """Return a service-account mapping from ``[firebase_admin]`` if present."""
    try:
        import streamlit as st

        section = st.secrets.get("firebase_admin")
    except Exception:
        section = None
    if not isinstance(section, Mapping):
        return None
    values = {str(key): value for key, value in dict(section).items()}
    if not values:
        return None
    if str(values.get("type", "")).strip() != "service_account":
        raise FirebaseAdminConfigurationError(
            "The Streamlit [firebase_admin] secret must contain a service-account credential."
        )
    required = {"type", "project_id", "private_key", "client_email", "token_uri"}
    missing = sorted(key for key in required if not str(values.get(key, "")).strip())
    if missing:
        raise FirebaseAdminConfigurationError(
            "The Streamlit [firebase_admin] secret is incomplete. "
            "Provide the complete service-account fields in Streamlit Cloud Secrets."
        )
    configured_project = _configured_project_id()
    credential_project = str(values.get("project_id", "")).strip()
    if credential_project != configured_project:
        raise FirebaseAdminConfigurationError(
            "The Firebase Admin credential project does not match FIREBASE_PROJECT_ID."
        )
    return values


FIREBASE_PROJECT_ID = _configured_project_id()
FIRESTORE_DATABASE_ID = _configured_database_id()
_APP_LOCK = threading.Lock()


def get_firebase_app() -> firebase_admin.App:
    """Return the process-wide Firebase Admin app, initializing it once."""
    configured_project = _configured_project_id()
    if configured_project != "bako-sweet":
        raise FirebaseAdminConfigurationError(
            "FIREBASE_PROJECT_ID must identify the configured Bako Sweet Firebase project."
        )
    try:
        app = firebase_admin.get_app()
        existing_project = str(
            getattr(app, "project_id", None)
            or getattr(app, "options", {}).get("projectId", "")
        ).strip()
        if existing_project and existing_project != configured_project:
            raise FirebaseAdminConfigurationError(
                "The initialized Firebase app does not match FIREBASE_PROJECT_ID."
            )
        return app
    except ValueError:
        # Streamlit reruns the script in the same Python process. The lock and
        # second lookup make initialization safe across concurrent requests.
        with _APP_LOCK:
            try:
                app = firebase_admin.get_app()
                existing_project = str(
                    getattr(app, "project_id", None)
                    or getattr(app, "options", {}).get("projectId", "")
                ).strip()
                if existing_project and existing_project != configured_project:
                    raise FirebaseAdminConfigurationError(
                        "The initialized Firebase app does not match FIREBASE_PROJECT_ID."
                    )
                return app
            except ValueError:
                service_account = _streamlit_admin_credentials()
                if service_account is not None:
                    credential = credentials.Certificate(service_account)
                else:
                    # Local development continues to use ADC. On Streamlit
                    # Cloud, a missing [firebase_admin] secret causes the
                    # initialization to fail rather than silently downgrading
                    # to an unauthenticated deployment.
                    credential = credentials.ApplicationDefault()
                return firebase_admin.initialize_app(
                    credential,
                    {"projectId": configured_project},
                )


def get_firestore_client() -> firestore.Client:
    """Return the Firestore client for the configured Firebase project."""
    return firestore.client(app=get_firebase_app(), database_id=_configured_database_id())


def test_firestore_connectivity(*, collection: str = "_codex_connectivity_test") -> dict[str, Any]:
    """Write, read, and remove one harmless Firestore connectivity document.

    The temporary document contains no workbook or user data. It is removed
    after a successful read so the connectivity probe does not leave state in
    the project.
    """
    client = get_firestore_client()
    document_id = f"probe-{uuid4().hex}"
    reference = client.collection(collection).document(document_id)
    payload = {
        "purpose": "firebase-admin-connectivity-test",
        "projectId": _configured_project_id(),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    reference.set(payload)
    snapshot = reference.get()
    try:
        if not snapshot.exists:
            raise RuntimeError("Firestore write completed, but the test document could not be read back.")
        return {"project_id": _configured_project_id(), "document": snapshot.to_dict() or {}}
    finally:
        reference.delete()
