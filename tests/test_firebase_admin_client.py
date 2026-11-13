import os
import sys
import types
import unittest
from unittest.mock import patch

from utils import firebase_admin_client as client


class FirebaseAdminConfigurationTests(unittest.TestCase):
    def test_environment_takes_precedence_over_streamlit_secrets(self):
        fake_streamlit = types.SimpleNamespace(
            secrets={"BAKO_FIREBASE_AUTH_ENABLED": False, "FIREBASE_PROJECT_ID": "wrong-project"}
        )
        with patch.dict(sys.modules, {"streamlit": fake_streamlit}), patch.dict(
            os.environ,
            {"BAKO_FIREBASE_AUTH_ENABLED": "true", "FIREBASE_PROJECT_ID": "bako-sweet"},
            clear=False,
        ):
            self.assertEqual(client.runtime_setting("BAKO_FIREBASE_AUTH_ENABLED", "false"), "true")
            self.assertEqual(client.runtime_setting("FIREBASE_PROJECT_ID", "fallback"), "bako-sweet")

    def test_incomplete_streamlit_service_account_fails_closed(self):
        fake_streamlit = types.SimpleNamespace(
            secrets={"firebase_admin": {"type": "service_account", "project_id": "bako-sweet"}}
        )
        with patch.dict(sys.modules, {"streamlit": fake_streamlit}), patch.dict(
            os.environ, {"FIREBASE_PROJECT_ID": "bako-sweet"}, clear=False
        ):
            with self.assertRaises(client.FirebaseAdminConfigurationError):
                client._streamlit_admin_credentials()

    def test_service_account_project_mismatch_fails_closed(self):
        fake_streamlit = types.SimpleNamespace(
            secrets={
                "firebase_admin": {
                    "type": "service_account",
                    "project_id": "another-project",
                    "private_key": "synthetic",
                    "client_email": "synthetic@example.test",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        )
        with patch.dict(sys.modules, {"streamlit": fake_streamlit}), patch.dict(
            os.environ, {"FIREBASE_PROJECT_ID": "bako-sweet"}, clear=False
        ):
            with self.assertRaises(client.FirebaseAdminConfigurationError):
                client._streamlit_admin_credentials()

    def test_service_account_is_used_for_initialization(self):
        service_account = {
            "type": "service_account",
            "project_id": "bako-sweet",
            "private_key": "synthetic",
            "client_email": "synthetic@example.test",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        sentinel_app = object()
        with patch.object(client.firebase_admin, "get_app", side_effect=ValueError), patch.object(
            client, "_streamlit_admin_credentials", return_value=service_account
        ), patch.object(client.credentials, "Certificate", return_value="certificate") as certificate, patch.object(
            client.firebase_admin, "initialize_app", return_value=sentinel_app
        ) as initialize:
            self.assertIs(client.get_firebase_app(), sentinel_app)
        certificate.assert_called_once_with(service_account)
        initialize.assert_called_once_with("certificate", {"projectId": "bako-sweet"})

    def test_adc_failure_is_not_silently_downgraded(self):
        with patch.object(client.firebase_admin, "get_app", side_effect=ValueError), patch.object(
            client, "_streamlit_admin_credentials", return_value=None
        ), patch.object(
            client.credentials, "ApplicationDefault", side_effect=RuntimeError("ADC unavailable")
        ):
            with self.assertRaises(RuntimeError):
                client.get_firebase_app()


if __name__ == "__main__":
    unittest.main()
