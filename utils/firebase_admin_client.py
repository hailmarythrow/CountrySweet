"""Firebase Admin/Cloud Firestore access for the dashboard.

Authentication intentionally uses Google Application Default Credentials (ADC).
No credential material is stored in this repository. On a developer machine,
ADC can be supplied by ``gcloud auth application-default login``; deployed
environments can provide ``GOOGLE_APPLICATION_CREDENTIALS`` through their
secret manager/runtime configuration.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
from typing import Any
from uuid import uuid4

import firebase_admin
from firebase_admin import credentials, firestore


FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "bako-sweet")
FIRESTORE_DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "(default)")
_APP_LOCK = threading.Lock()


def get_firebase_app() -> firebase_admin.App:
    """Return the process-wide Firebase Admin app, initializing it once."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        # Streamlit reruns the script in the same Python process. The lock and
        # second lookup make initialization safe across concurrent requests.
        with _APP_LOCK:
            try:
                return firebase_admin.get_app()
            except ValueError:
                return firebase_admin.initialize_app(
                    credentials.ApplicationDefault(),
                    {"projectId": FIREBASE_PROJECT_ID},
                )


def get_firestore_client() -> firestore.Client:
    """Return the Firestore client for the configured Firebase project."""
    return firestore.client(app=get_firebase_app(), database_id=FIRESTORE_DATABASE_ID)


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
        "projectId": FIREBASE_PROJECT_ID,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    reference.set(payload)
    snapshot = reference.get()
    try:
        if not snapshot.exists:
            raise RuntimeError("Firestore write completed, but the test document could not be read back.")
        return {"project_id": FIREBASE_PROJECT_ID, "document": snapshot.to_dict() or {}}
    finally:
        reference.delete()
