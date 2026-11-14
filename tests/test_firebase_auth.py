from datetime import datetime, timedelta, timezone
import os
import unittest
import sys
import types
from unittest.mock import patch

from utils.firebase_auth import (
    FirebaseAuthConfig,
    FirebaseEmployee,
    FirebasePermissionError,
    FirebaseUnauthorizedError,
    create_invitation,
    clear_auth_session,
    employee_from_verified_claims,
    invitation_digest,
    FIREBASE_AUTH_JS,
    remember_employee,
    require_permission,
    session_employee,
    validate_invitation,
    verify_id_token,
)
from utils.dashboard_snapshot import SnapshotSource


def claims(*, role="viewer", email_verified=True, expires=None):
    return {
        "uid": "uid-test-123",
        "email": "teammate@example.test",
        "name": "Synthetic Teammate",
        "email_verified": email_verified,
        "role": role,
        "exp": expires or (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
    }


class FirebaseAuthTests(unittest.TestCase):
    def setUp(self):
        self.config = FirebaseAuthConfig(
            enabled=True,
            project_id="bako-sweet",
            api_key="web-key",
            auth_domain="bako-sweet.firebaseapp.com",
            app_id="app-id",
        )

    def test_verified_roles_and_permissions(self):
        publisher = employee_from_verified_claims(claims(role="publisher"), config=self.config)
        self.assertTrue(publisher.can("publish_workbook"))
        self.assertFalse(publisher.can("manage_users"))

    def test_placeholder_web_configuration_is_not_treated_as_ready(self):
        placeholder = FirebaseAuthConfig(
            enabled=True,
            project_id="bako-sweet",
            api_key="PASTE_FIREBASE_WEB_API_KEY_HERE",
            auth_domain="bako-sweet.firebaseapp.com",
            app_id="PASTE_FIREBASE_WEB_APP_ID_HERE",
        )
        self.assertFalse(placeholder.web_ready)

    def test_auth_config_reads_streamlit_secrets_when_environment_is_absent(self):
        fake_streamlit = types.SimpleNamespace(
            secrets={
                "BAKO_FIREBASE_AUTH_ENABLED": True,
                "FIREBASE_PROJECT_ID": "bako-sweet",
                "BAKO_FIREBASE_WEB_API_KEY": "secret-web-key",
                "BAKO_FIREBASE_AUTH_DOMAIN": "bako-sweet.firebaseapp.com",
                "BAKO_FIREBASE_WEB_APP_ID": "secret-app-id",
                "BAKO_FIREBASE_REQUIRE_EMAIL_VERIFICATION": True,
            }
        )
        names = [
            "BAKO_FIREBASE_AUTH_ENABLED",
            "FIREBASE_PROJECT_ID",
            "BAKO_FIREBASE_WEB_API_KEY",
            "BAKO_FIREBASE_AUTH_DOMAIN",
            "BAKO_FIREBASE_WEB_APP_ID",
            "BAKO_FIREBASE_REQUIRE_EMAIL_VERIFICATION",
        ]
        with patch.dict(sys.modules, {"streamlit": fake_streamlit}), patch.dict(
            os.environ, {name: "" for name in names}, clear=False
        ):
            config = FirebaseAuthConfig.from_environment()
        self.assertTrue(config.enabled)
        self.assertTrue(config.web_ready)
        self.assertEqual(config.project_id, "bako-sweet")

    def test_unverified_identity_is_rejected(self):
        with self.assertRaises(FirebaseUnauthorizedError):
            employee_from_verified_claims(claims(email_verified=False), config=self.config)

    def test_viewer_cannot_publish(self):
        viewer = employee_from_verified_claims(claims(role="viewer"), config=self.config)
        with self.assertRaises(FirebasePermissionError):
            require_permission(viewer, "publish_workbook")

    def test_viewer_cannot_manage_or_approve_users(self):
        viewer = employee_from_verified_claims(claims(role="viewer"), config=self.config)
        with self.assertRaises(FirebasePermissionError):
            require_permission(viewer, "manage_users")

    def test_verification_panel_keeps_browser_session_and_safe_errors(self):
        self.assertIn("firebase.auth.Auth.Persistence.LOCAL", FIREBASE_AUTH_JS)
        self.assertIn("firebaseAuth.onIdTokenChanged((user) => runtime.handler", FIREBASE_AUTH_JS)
        self.assertIn("if (currentUser.emailVerified !== true)", FIREBASE_AUTH_JS)
        self.assertIn("setTriggerValue('id_token', idToken);", FIREBASE_AUTH_JS)
        self.assertIn("Firebase accepted the verification email request.", FIREBASE_AUTH_JS)
        self.assertIn("Firebase rejected the verification request.", FIREBASE_AUTH_JS)
        self.assertIn("Firebase configuration mismatch.", FIREBASE_AUTH_JS)
        self.assertIn("const runtimeKey = '__bakoFirebaseAuthRuntime_'", FIREBASE_AUTH_JS)
        self.assertIn("if (!runtime.listener) runtime.listener = firebaseAuth.onIdTokenChanged", FIREBASE_AUTH_JS)
        self.assertIn("Firebase token acquisition timed out.", FIREBASE_AUTH_JS)
        self.assertIn("return () => { disposed = true;", FIREBASE_AUTH_JS)
        self.assertNotIn("sessionStorage", FIREBASE_AUTH_JS)

    def test_session_contains_no_raw_token_and_expires(self):
        employee = employee_from_verified_claims(claims(role="admin"), config=self.config)
        state = {}
        remember_employee(state, employee)
        self.assertNotIn("id_token", state)
        self.assertEqual(session_employee(state).uid, employee.uid)
        state["firebase_employee"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.assertIsNone(session_employee(state))
        clear_auth_session(state)
        self.assertNotIn("firebase_employee", state)

    def test_server_verifies_token_without_retaining_it(self):
        employee = employee_from_verified_claims(claims(role="viewer"), config=self.config)
        with patch("utils.firebase_auth.auth.verify_id_token", return_value=claims(role="viewer")) as verifier, patch(
            "utils.firebase_auth.get_firebase_app", return_value=object()
        ):
            verified = verify_id_token("synthetic-token", config=self.config)
        self.assertEqual(verified.uid, employee.uid)
        self.assertTrue(verifier.call_args.kwargs["check_revoked"])

    def test_provider_permission_failure_is_safe_and_specific(self):
        class PermissionDeniedError(RuntimeError):
            code = "PERMISSION_DENIED"

        with patch(
            "utils.firebase_auth.auth.verify_id_token",
            side_effect=PermissionDeniedError("credential details must not reach the UI"),
        ), patch("utils.firebase_auth.get_firebase_app", return_value=object()):
            with self.assertRaisesRegex(FirebaseUnauthorizedError, "not authorized"):
                verify_id_token("synthetic-token", config=self.config)

    def test_snapshot_source_supports_verified_publication_identity(self):
        source = SnapshotSource(
            workbook_id="dev:checksum",
            filename="synthetic.xlsx",
            source_version="checksum",
            source_checksum="checksum",
            publisher_uid="verified-uid",
            publisher_display_name="Verified Publisher",
        )
        self.assertEqual(source.publisher_uid, "verified-uid")
        self.assertEqual(source.publisher_display_name, "Verified Publisher")

    def test_invitation_code_is_hashed_and_validated_without_live_firebase(self):
        class Snapshot:
            def __init__(self, values=None):
                self.exists = values is not None
                self._values = values or {}

            def to_dict(self):
                return dict(self._values)

        class Document:
            def __init__(self, store, key):
                self.store, self.key = store, key

            def set(self, values):
                self.store[self.key] = dict(values)

            def get(self):
                return Snapshot(self.store.get(self.key))

        class Collection:
            def __init__(self, store):
                self.store = store

            def document(self, key):
                return Document(self.store, key)

        class Client:
            def __init__(self):
                self.store = {}

            def collection(self, name):
                return Collection(self.store)

        client = Client()
        admin = FirebaseEmployee(
            uid="admin-uid",
            email="admin@example.test",
            display_name="Admin",
            role="admin",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        code, document_id = create_invitation(
            "teammate@example.test",
            display_name="Teammate",
            role="viewer",
            admin=admin,
            client=client,
        )
        self.assertNotEqual(code, document_id)
        self.assertEqual(document_id, invitation_digest(code))
        self.assertNotIn(code, repr(client.store))
        record = validate_invitation("teammate@example.test", code, client=client)
        self.assertEqual(record["role"], "viewer")
        self.assertIsNone(validate_invitation("other@example.test", code, client=client))


if __name__ == "__main__":
    unittest.main()
