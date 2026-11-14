"""Opt-in Firebase Authentication for the shared dashboard.

The browser performs the Firebase Email/Password exchange and sends only the
resulting short-lived ID token to Streamlit. The server verifies that token
with the Firebase Admin SDK, requires a verified email address, and stores only
the verified identity/role in Streamlit session state. Passwords and raw
tokens are never written to Firestore, logs, or the repository.

Authentication is disabled unless ``BAKO_FIREBASE_AUTH_ENABLED`` is true.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from base64 import b64encode
import hmac
import hashlib
import logging
import os
from pathlib import Path
import secrets
from typing import Any, Mapping

from firebase_admin import auth, firestore

from .firebase_admin_client import FIREBASE_PROJECT_ID, get_firebase_app, get_firestore_client, runtime_setting


LOGGER = logging.getLogger("country_sweet.dashboard")


AUTH_BOOTSTRAP_COLLECTION = "bako_sweet_auth_bootstrap"
AUTH_BOOTSTRAP_DOCUMENT = "current"
AUTH_INVITES_COLLECTION = "bako_sweet_auth_invites"
INVITE_TTL_SECONDS = 60 * 60 * 24 * 7


class FirebaseAuthError(RuntimeError):
    """Base error for authentication and authorization failures."""


class FirebaseConfigurationError(FirebaseAuthError):
    """Raised when authentication is enabled without complete web config."""


class FirebaseUnauthorizedError(FirebaseAuthError):
    """Raised when an ID token is invalid or lacks required claims."""


class FirebasePermissionError(FirebaseAuthError):
    """Raised when a verified user lacks a protected permission."""


ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"view_dashboard", "publish_workbook", "manage_users"}),
    "publisher": frozenset({"view_dashboard", "publish_workbook"}),
    "viewer": frozenset({"view_dashboard"}),
}


@dataclass(frozen=True)
class FirebaseAuthConfig:
    enabled: bool
    project_id: str
    api_key: str | None
    auth_domain: str | None
    app_id: str | None
    require_email_verification: bool = True

    @classmethod
    def from_environment(cls) -> "FirebaseAuthConfig":
        enabled = str(runtime_setting("BAKO_FIREBASE_AUTH_ENABLED", "false")).strip().casefold() in {
            "1", "true", "yes", "on"
        }
        return cls(
            enabled=enabled,
            project_id=str(runtime_setting("FIREBASE_PROJECT_ID", FIREBASE_PROJECT_ID)).strip(),
            api_key=str(runtime_setting("BAKO_FIREBASE_WEB_API_KEY", "")).strip() or None,
            auth_domain=str(runtime_setting("BAKO_FIREBASE_AUTH_DOMAIN", "")).strip() or None,
            app_id=str(runtime_setting("BAKO_FIREBASE_WEB_APP_ID", "")).strip() or None,
            require_email_verification=str(runtime_setting("BAKO_FIREBASE_REQUIRE_EMAIL_VERIFICATION", "true")).strip().casefold()
            not in {"0", "false", "no", "off"},
        )

    @property
    def web_ready(self) -> bool:
        def configured(value: str | None) -> bool:
            normalized = str(value or "").strip()
            return bool(normalized) and not normalized.upper().startswith(("PASTE_", "YOUR_"))

        return all(
            (
                configured(self.api_key),
                configured(self.auth_domain),
                configured(self.app_id),
                configured(self.project_id),
                self.project_id == "bako-sweet",
            )
        )


@dataclass(frozen=True)
class FirebaseEmployee:
    """Verified identity fields safe to retain in a Streamlit session."""

    uid: str
    email: str
    display_name: str
    role: str
    expires_at: datetime
    first_name: str | None = None

    @property
    def permissions(self) -> frozenset[str]:
        return ROLE_PERMISSIONS[self.role]

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def session_payload(self) -> dict[str, str]:
        return {
            "uid": self.uid,
            "email": self.email,
            "display_name": self.display_name,
            "first_name": self.first_name or "",
            "role": self.role,
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat(),
        }


def _claim_text(claims: Mapping[str, Any], name: str) -> str | None:
    value = claims.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _profile_first_name(claims: Mapping[str, Any], *, display_name: str, email: str) -> str | None:
    """Resolve only a trusted profile/claim first name; never infer from email."""
    explicit = _claim_text(claims, "first_name") or _claim_text(claims, "firstName")
    if explicit:
        return explicit.split()[0].strip(" ,.;:()[]{}") or None
    if display_name and display_name.casefold() != email.casefold():
        return display_name.split()[0].strip(" ,.;:()[]{}") or None
    return None


def _role_from_claims(claims: Mapping[str, Any]) -> str:
    values: set[str] = set()
    role = claims.get("role")
    roles = claims.get("roles", [])
    if isinstance(role, str):
        values.add(role.casefold().strip())
    if isinstance(roles, (list, tuple, set)):
        values.update(str(item).casefold().strip() for item in roles)
    values.discard("")
    for candidate in ("admin", "publisher", "viewer"):
        if candidate in values:
            return candidate
    raise FirebaseUnauthorizedError("The Firebase user has no approved dashboard role.")


def employee_from_verified_claims(
    claims: Mapping[str, Any],
    *,
    config: FirebaseAuthConfig | None = None,
) -> FirebaseEmployee:
    """Validate claims returned by ``firebase_admin.auth.verify_id_token``."""
    config = config or FirebaseAuthConfig.from_environment()
    uid = _claim_text(claims, "uid") or _claim_text(claims, "user_id")
    email = _claim_text(claims, "email")
    if not uid or not email:
        raise FirebaseUnauthorizedError("The Firebase identity has no stable UID or email address.")
    if config.require_email_verification and claims.get("email_verified") is not True:
        raise FirebaseUnauthorizedError("Verify your Firebase email address before signing in.")
    expires = claims.get("exp")
    if not isinstance(expires, (int, float)):
        raise FirebaseUnauthorizedError("The Firebase ID token has no valid expiration.")
    expires_at = datetime.fromtimestamp(float(expires), tz=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise FirebaseUnauthorizedError("The Firebase ID token has expired.")
    display_name = _claim_text(claims, "name") or email
    return FirebaseEmployee(
        uid=uid,
        email=email,
        display_name=display_name,
        role=_role_from_claims(claims),
        expires_at=expires_at,
        first_name=_profile_first_name(claims, display_name=display_name, email=email),
    )


def verify_id_token(
    id_token: str,
    *,
    config: FirebaseAuthConfig | None = None,
) -> FirebaseEmployee:
    """Verify one browser token without retaining the raw token."""
    config = config or FirebaseAuthConfig.from_environment()
    if not config.enabled:
        raise FirebaseConfigurationError("Firebase Authentication is disabled.")
    if not id_token or not id_token.strip():
        raise FirebaseUnauthorizedError("No Firebase ID token was supplied.")
    claims = _verify_claims(id_token, check_revoked=True)
    return employee_from_verified_claims(claims, config=config)


def _verify_claims(id_token: str, *, check_revoked: bool) -> Mapping[str, Any]:
    """Verify a Firebase ID token and return claims without retaining the token."""
    if not id_token or not str(id_token).strip():
        raise FirebaseUnauthorizedError("No Firebase ID token was supplied.")
    try:
        return auth.verify_id_token(
            str(id_token).strip(),
            app=get_firebase_app(),
            check_revoked=check_revoked,
        )
    except Exception as exc:  # firebase_admin raises provider-specific exceptions
        provider_type = type(exc).__name__
        provider_code = getattr(exc, "code", "")
        if not isinstance(provider_code, str) or len(provider_code) > 80:
            provider_code = ""
        # Keep diagnostics useful without writing token contents, credentials,
        # email addresses, or provider response bodies to the server log.
        LOGGER.warning(
            "auth.token_provider_failure provider_type=%s provider_code=%s check_revoked=%s",
            provider_type,
            provider_code,
            check_revoked,
        )
        failure_text = f"{provider_type} {provider_code}".casefold()
        if "revoked" in failure_text:
            message = "Firebase rejected this sign-in session because its token was revoked. Sign in again."
        elif "disabled" in failure_text:
            message = "This Firebase account is disabled. Contact an administrator."
        elif any(marker in failure_text for marker in ("permission", "forbidden", "unauthenticated")):
            message = (
                "Firebase server authentication is not authorized for this deployment. "
                "Check the deployed Firebase Admin credential and Authentication API access."
            )
        elif any(marker in failure_text for marker in ("certificate", "transport", "timeout", "deadline", "connection")):
            message = (
                "The deployed server could not reach Firebase's token-verification service. "
                "Retry after checking the deployment's Firebase network and credential configuration."
            )
        else:
            message = "The Firebase sign-in session is invalid or expired. Sign in again."
        raise FirebaseUnauthorizedError(message) from exc


def session_employee(session_state: Mapping[str, Any]) -> FirebaseEmployee | None:
    """Read the verified identity from session state, expiring it locally."""
    payload = session_state.get("firebase_employee")
    if not isinstance(payload, Mapping):
        return None
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
            return None
        role = str(payload["role"])
        if role not in ROLE_PERMISSIONS:
            return None
        return FirebaseEmployee(
            uid=str(payload["uid"]),
            email=str(payload["email"]),
            display_name=str(payload["display_name"]),
            role=role,
            expires_at=expires_at,
            first_name=str(payload.get("first_name") or "") or None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def remember_employee(session_state: dict[str, Any], employee: FirebaseEmployee) -> None:
    """Store verified identity fields only; never store an ID token."""
    session_state["firebase_employee"] = employee.session_payload()


def clear_auth_session(session_state: dict[str, Any]) -> None:
    session_state.pop("firebase_employee", None)


def require_permission(employee: FirebaseEmployee | None, permission: str) -> FirebaseEmployee:
    if employee is None:
        raise FirebaseUnauthorizedError("Sign in with an authorized Firebase account first.")
    if not employee.can(permission):
        raise FirebasePermissionError("Your Firebase role does not permit this action.")
    return employee


def web_config(config: FirebaseAuthConfig | None = None) -> dict[str, str]:
    config = config or FirebaseAuthConfig.from_environment()
    if not config.web_ready:
        raise FirebaseConfigurationError(
            "Firebase web authentication is enabled but BAKO_FIREBASE_WEB_API_KEY, "
            "BAKO_FIREBASE_AUTH_DOMAIN, and BAKO_FIREBASE_WEB_APP_ID are incomplete."
        )
    return {
        "apiKey": str(config.api_key),
        "authDomain": str(config.auth_domain),
        "projectId": config.project_id,
        "appId": str(config.app_id),
    }


def _invite_digest(invitation_code: str) -> str:
    """Return the deterministic digest stored for a one-time invite code."""
    return hashlib.sha256(str(invitation_code).encode("utf-8")).hexdigest()


def invitation_digest(invitation_code: str) -> str:
    """Expose a non-secret digest for matching an approval to a browser event."""
    return _invite_digest(invitation_code)


def token_digest(id_token: str) -> str:
    """Return a non-secret digest for de-duplicating browser events."""
    return hashlib.sha256(str(id_token).encode("utf-8")).hexdigest()


def normalize_person_name(value: str, *, field_name: str) -> str:
    """Normalize and validate a user-entered profile name server-side."""
    normalized = " ".join(str(value or "").strip().split())
    if not normalized or len(normalized) > 80:
        raise FirebaseUnauthorizedError(f"{field_name} must be between 1 and 80 characters.")
    allowed_punctuation = set(" .'-’")
    if not normalized[0].isalpha() or any(not (character.isalpha() or character in allowed_punctuation) for character in normalized):
        raise FirebaseUnauthorizedError(f"{field_name} contains unsupported characters.")
    if not any(character.isalpha() for character in normalized):
        raise FirebaseUnauthorizedError(f"{field_name} must include letters.")
    return normalized


def _utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def create_invitation(
    email: str,
    *,
    display_name: str | None,
    role: str,
    admin: FirebaseEmployee,
    client: firestore.Client | None = None,
) -> tuple[str, str]:
    """Create a hashed, one-time invitation and return its private code/id."""
    require_permission(admin, "manage_users")
    normalized_role = str(role).casefold().strip()
    if normalized_role not in ROLE_PERMISSIONS:
        raise ValueError("Invite role must be admin, publisher, or viewer.")
    normalized_email = str(email).strip().casefold()
    if "@" not in normalized_email:
        raise ValueError("Invite email must be a valid email address.")
    invitation_code = secrets.token_urlsafe(32)
    invitation_id = _invite_digest(invitation_code)
    client = client or get_firestore_client()
    now = datetime.now(timezone.utc)
    client.collection(AUTH_INVITES_COLLECTION).document(invitation_id).set(
        {
            "email": normalized_email,
            "display_name": str(display_name or normalized_email).strip(),
            "role": normalized_role,
            "status": "pending",
            "created_at": now,
            "expires_at": now + timedelta(seconds=INVITE_TTL_SECONDS),
            "invited_by_uid": admin.uid,
        }
    )
    return invitation_code, invitation_id


def invite_teammate(
    email: str,
    *,
    display_name: str | None,
    role: str,
    admin: FirebaseEmployee,
    client: firestore.Client | None = None,
) -> str:
    """Create an invite without creating an account or accepting a password."""
    invitation_code, _ = create_invitation(
        email,
        display_name=display_name,
        role=role,
        admin=admin,
        client=client,
    )
    return invitation_code


def list_invitations(
    *,
    admin: FirebaseEmployee,
    client: firestore.Client | None = None,
) -> list[dict[str, Any]]:
    """Return safe invitation metadata for an authenticated administrator.

    The raw one-time invitation code is never stored or returned. Expired
    pending invitations remain visible with an ``expired`` status so an
    administrator can distinguish them from invitations that were redeemed.
    """
    require_permission(admin, "manage_users")
    client = client or get_firestore_client()
    now = datetime.now(timezone.utc)
    invitations: list[dict[str, Any]] = []
    for snapshot in client.collection(AUTH_INVITES_COLLECTION).stream():
        record = dict(snapshot.to_dict() or {})
        status = str(record.get("status", "unknown")).strip().casefold() or "unknown"
        expires_at = _utc_datetime(record.get("expires_at"))
        if status == "pending" and expires_at is not None and expires_at <= now:
            status = "expired"
        invitations.append(
            {
                "id": snapshot.id,
                "email": str(record.get("email", "")).strip(),
                "role": str(record.get("role", "")).strip().casefold(),
                "status": status,
                "created_at": _utc_datetime(record.get("created_at")),
                "expires_at": expires_at,
            }
        )
    invitations.sort(key=lambda item: item.get("created_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return invitations


def cancel_invitation(
    invitation_id: str,
    *,
    admin: FirebaseEmployee,
    client: firestore.Client | None = None,
) -> None:
    """Cancel an unused invitation without deleting its audit record."""
    require_permission(admin, "manage_users")
    normalized_id = str(invitation_id).strip()
    if not normalized_id or len(normalized_id) != 64:
        raise FirebasePermissionError("That invitation reference is invalid.")
    client = client or get_firestore_client()
    reference = client.collection(AUTH_INVITES_COLLECTION).document(normalized_id)
    transaction = client.transaction()

    @firestore.transactional
    def cancel(transaction: firestore.Transaction) -> None:
        snapshot = reference.get(transaction=transaction)
        if not snapshot.exists:
            raise FirebasePermissionError("That invitation no longer exists.")
        record = dict(snapshot.to_dict() or {})
        if str(record.get("status", "")).casefold() != "pending":
            raise FirebasePermissionError("That invitation has already been used or cancelled.")
        transaction.update(
            reference,
            {
                "status": "cancelled",
                "cancelled_at": datetime.now(timezone.utc),
                "cancelled_by_uid": admin.uid,
            },
        )

    cancel(transaction)


def list_team_members(*, admin: FirebaseEmployee) -> list[dict[str, Any]]:
    """Return safe role metadata for authorized Firebase users."""
    require_permission(admin, "manage_users")
    members: list[dict[str, Any]] = []
    for user in auth.list_users().iterate_all():
        claims = dict(user.custom_claims or {})
        try:
            role = _role_from_claims(claims) if any(
                key in claims for key in ("role", "roles")
            ) else None
        except FirebaseUnauthorizedError:
            role = None
        if role is None:
            continue
        members.append(
            {
                "uid": user.uid,
                "email": user.email or "",
                "display_name": user.display_name or user.email or user.uid,
                "role": role,
                "email_verified": bool(user.email_verified),
                "disabled": bool(user.disabled),
            }
        )
    members.sort(key=lambda item: (str(item.get("email", "")).casefold(), str(item.get("uid", ""))))
    return members


def validate_invitation(
    email: str,
    invitation_code: str,
    *,
    client: firestore.Client | None = None,
) -> dict[str, Any] | None:
    """Validate an invitation without consuming it or storing its raw code."""
    normalized_email = str(email).strip().casefold()
    if not normalized_email or not invitation_code:
        return None
    client = client or get_firestore_client()
    snapshot = client.collection(AUTH_INVITES_COLLECTION).document(_invite_digest(invitation_code)).get()
    if not snapshot.exists:
        return None
    record = dict(snapshot.to_dict() or {})
    if str(record.get("status", "")).casefold() != "pending":
        return None
    if str(record.get("email", "")).strip().casefold() != normalized_email:
        return None
    expires_at = _utc_datetime(record.get("expires_at"))
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        return None
    return record


def consume_invitation(
    email: str,
    invitation_code: str,
    uid: str,
    *,
    client: firestore.Client | None = None,
) -> dict[str, Any]:
    """Atomically consume a valid invitation and return its approved role data."""
    normalized_email = str(email).strip().casefold()
    if not normalized_email or not invitation_code or not uid:
        raise FirebaseUnauthorizedError("A valid invitation, email, and Firebase UID are required.")
    client = client or get_firestore_client()
    invitation_ref = client.collection(AUTH_INVITES_COLLECTION).document(_invite_digest(invitation_code))
    transaction = client.transaction()

    @firestore.transactional
    def consume(transaction: firestore.Transaction) -> dict[str, Any]:
        snapshot = invitation_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise FirebaseUnauthorizedError("That invitation is invalid or has already been used.")
        record = dict(snapshot.to_dict() or {})
        if str(record.get("email", "")).strip().casefold() != normalized_email:
            raise FirebaseUnauthorizedError("The invitation email does not match the new account.")
        status = str(record.get("status", "")).casefold()
        if status == "used" and str(record.get("used_by_uid", "")) == str(uid):
            # Idempotent recovery for a profile/role update that failed after
            # this same Firebase account redeemed the invitation. This never
            # permits a different UID to reuse the invitation.
            return {
                "role": str(record.get("role", "")).casefold().strip(),
                "display_name": str(record.get("display_name") or normalized_email),
                "already_used": True,
            }
        if status != "pending":
            raise FirebaseUnauthorizedError("That invitation is invalid or has already been used.")
        expires_at = _utc_datetime(record.get("expires_at"))
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            raise FirebaseUnauthorizedError("That invitation has expired.")
        role = str(record.get("role", "")).casefold().strip()
        if role not in ROLE_PERMISSIONS:
            raise FirebaseUnauthorizedError("That invitation has no valid dashboard role.")
        transaction.update(
            invitation_ref,
            {
                "status": "used",
                "used_by_uid": str(uid),
                "used_at": datetime.now(timezone.utc),
            },
        )
        return {"role": role, "display_name": str(record.get("display_name") or normalized_email)}

    return consume(transaction)


def approve_new_account(
    id_token: str,
    invitation_code: str,
    *,
    first_name: str,
    last_name: str,
    config: FirebaseAuthConfig | None = None,
    client: firestore.Client | None = None,
) -> FirebaseEmployee:
    """Approve a newly registered, still-unverified account using an invite."""
    config = config or FirebaseAuthConfig.from_environment()
    if not config.enabled:
        raise FirebaseConfigurationError("Firebase Authentication is disabled.")
    claims = _verify_claims(id_token, check_revoked=False)
    uid = _claim_text(claims, "uid") or _claim_text(claims, "user_id")
    email = _claim_text(claims, "email")
    expires = claims.get("exp")
    if not uid or not email or not isinstance(expires, (int, float)):
        raise FirebaseUnauthorizedError("The new Firebase account identity is incomplete.")
    normalized_first_name = normalize_person_name(first_name, field_name="First name")
    normalized_last_name = normalize_person_name(last_name, field_name="Last name")
    display_name = f"{normalized_first_name} {normalized_last_name}"
    approved = consume_invitation(email, invitation_code, uid, client=client)
    approved_role = str(approved.get("role", "")).casefold().strip()
    if approved_role not in ROLE_PERMISSIONS:
        raise FirebaseUnauthorizedError("That invitation has no valid dashboard role.")
    user = auth.get_user(uid)
    # Update the profile before assigning the approved role. If the profile
    # update fails, the invitation remains recoverable for this same UID and
    # no partially-authorized account is created.
    auth.update_user(uid, display_name=display_name)
    user_claims = dict(user.custom_claims or {})
    user_claims["role"] = approved_role
    auth.set_custom_user_claims(uid, user_claims)
    return FirebaseEmployee(
        uid=uid,
        email=email,
        display_name=display_name,
        role=approved_role,
        expires_at=datetime.fromtimestamp(float(expires), tz=timezone.utc),
        first_name=normalized_first_name,
    )


def assign_role(uid: str, role: str, *, admin: FirebaseEmployee) -> None:
    """Assign an approved dashboard role from a trusted admin operation."""
    require_permission(admin, "manage_users")
    normalized_role = str(role).casefold().strip()
    if normalized_role not in ROLE_PERMISSIONS:
        raise ValueError("Role must be admin, publisher, or viewer.")
    user = auth.get_user(str(uid).strip())
    claims = dict(user.custom_claims or {})
    claims["role"] = normalized_role
    auth.set_custom_user_claims(user.uid, claims)
    auth.revoke_refresh_tokens(user.uid)


def revoke_user(uid: str, *, admin: FirebaseEmployee) -> None:
    """Disable a user and revoke refresh tokens after an admin revocation."""
    require_permission(admin, "manage_users")
    normalized_uid = str(uid).strip()
    if not normalized_uid or normalized_uid == admin.uid:
        raise FirebasePermissionError("An administrator cannot revoke their own active account.")
    auth.update_user(normalized_uid, disabled=True)
    auth.revoke_refresh_tokens(normalized_uid)


def bootstrap_admin(
    uid: str,
    *,
    bootstrap_token: str,
    client: firestore.Client | None = None,
) -> str:
    """Perform a one-time admin claim bootstrap for an existing verified user."""
    expected = os.getenv("BAKO_FIREBASE_BOOTSTRAP_TOKEN", "")
    if not expected or not hmac.compare_digest(str(bootstrap_token), expected):
        raise FirebasePermissionError("The one-time Firebase bootstrap token is invalid.")
    user = auth.get_user(str(uid).strip())
    if not user.email_verified:
        raise FirebaseUnauthorizedError("The bootstrap user must verify the Firebase email first.")
    client = client or get_firestore_client()
    marker = client.collection(AUTH_BOOTSTRAP_COLLECTION).document(AUTH_BOOTSTRAP_DOCUMENT)
    transaction = client.transaction()

    @firestore.transactional
    def claim_once(transaction: firestore.Transaction) -> None:
        existing = marker.get(transaction=transaction)
        if existing.exists:
            raise FirebasePermissionError("The Firebase admin bootstrap has already been completed.")
        transaction.set(marker, {"completed": True, "uid": user.uid, "email": user.email})

    claim_once(transaction)
    claims = dict(user.custom_claims or {})
    claims["role"] = "admin"
    auth.set_custom_user_claims(user.uid, claims)
    return user.uid


def render_firebase_auth_component(
    config: FirebaseAuthConfig,
    *,
    signup_approved: bool = False,
    signup_profile: Mapping[str, str] | None = None,
    signup_error: str = "",
    verification_only: bool = False,
    force_signout: bool = False,
) -> dict[str, Any]:
    """Render the opt-in browser login component and return event values."""
    import streamlit as st

    try:
        component = st.components.v2.component(
            "firebase_email_auth",
            html=FIREBASE_AUTH_HTML,
            css=FIREBASE_AUTH_CSS,
            js=FIREBASE_AUTH_JS,
        )
    except AttributeError as exc:  # pragma: no cover - requires Streamlit runtime
        raise FirebaseConfigurationError("This Streamlit version lacks components.v2 support.") from exc
    return component(
        data={
            "config": web_config(config),
            "assets": _auth_asset_data(),
            "authenticated": session_employee(st.session_state) is not None,
            "signup_approved": bool(signup_approved),
            "signup_profile": dict(signup_profile or {}),
            "signup_error": str(signup_error or ""),
            "verification_only": bool(verification_only),
            "force_signout": bool(force_signout),
        },
        key="firebase_email_auth",
    ) or {}


@lru_cache(maxsize=1)
def _auth_asset_data() -> dict[str, str]:
    """Embed the approved local auth artwork for the Streamlit component."""
    assets_dir = Path(__file__).resolve().parents[1] / "assets"
    farm_path = assets_dir / "bako_sweet_farm_background.jpg"
    if not farm_path.exists():
        # The JPEG is a transport-optimized derivative of the approved PNG;
        # retain the original PNG as the source/fallback for local installs.
        farm_path = assets_dir / "bako_sweet_farm_background.png"
    definitions = {
        "farm": (farm_path, "image/jpeg" if farm_path.suffix.lower() == ".jpg" else "image/png"),
        "green": (assets_dir / "bako_sweet_green_background.png", "image/png"),
        "logo": (assets_dir / "bakosweet.png", "image/png"),
    }
    encoded: dict[str, str] = {}
    for name, (path, mime) in definitions.items():
        if path.exists():
            encoded[name] = f"data:{mime};base64,{b64encode(path.read_bytes()).decode('ascii')}"
    return encoded


FIREBASE_AUTH_HTML = """
<div class="firebase-auth-shell">
  <svg class="firebase-auth-svg-defs" aria-hidden="true" width="0" height="0">
    <defs>
      <clipPath id="firebase-auth-farm-clip" clipPathUnits="objectBoundingBox">
        <path d="M 0 0 H .8 C .93 .25 1 .75 .8 1 H 0 Z"></path>
      </clipPath>
    </defs>
  </svg>
  <div class="firebase-auth-curve-layer" aria-hidden="true">
    <img id="firebase-auth-farm-image" class="firebase-auth-farm-image" alt="">
    <svg class="firebase-auth-curve-border" viewBox="0 0 100 100" preserveAspectRatio="none">
      <path d="M 80 0 C 93 25 100 75 80 100"></path>
    </svg>
  </div>
  <section class="firebase-auth-branding" aria-label="Bako Sweet branding">
    <div class="firebase-auth-branding-content">
      <img id="firebase-auth-logo" class="firebase-auth-logo" alt="Bako Sweet shield logo">
      <div class="firebase-auth-brand-name">Bako Sweet</div>
      <div class="firebase-auth-brand-subtitle">Inventory Dashboard</div>
      <svg class="firebase-auth-brand-rule" viewBox="0 0 300 72" aria-hidden="true" preserveAspectRatio="xMidYMid meet">
        <path d="M24 53 C75 30 154 20 269 36 C195 31 115 43 38 61 Q20 64 24 53 Z" fill="#F47C35"></path>
      </svg>
    </div>
  </section>
  <section class="firebase-auth-stage" aria-label="Authentication">
    <div class="firebase-auth-card">
      <h3 id="firebase-auth-title">Welcome to Bako Sweet</h3>
      <p id="firebase-auth-subtitle" class="firebase-auth-note">Sign in to access your inventory dashboard.</p>
      <div id="firebase-auth-loading" class="firebase-auth-loading" role="status" aria-live="polite">Checking your sign-in session…</div>
      <div id="firebase-auth-tabs" hidden></div>
      <div id="firebase-auth-login-panel" hidden>
        <label>Email<input id="firebase-auth-email" type="email" autocomplete="username"></label>
        <label>Password<input id="firebase-auth-password" type="password" autocomplete="current-password"></label>
        <button id="firebase-auth-reset" class="firebase-auth-link firebase-auth-forgot" type="button">Forgot password?</button>
        <div class="firebase-auth-actions"><button id="firebase-auth-login" class="firebase-auth-primary" type="button">Sign In</button><button id="firebase-auth-verify" type="button" hidden>Send verification email</button></div>
        <div class="firebase-auth-divider" aria-hidden="true"></div>
        <button id="firebase-auth-signup-tab" class="firebase-auth-link firebase-auth-invite-link" type="button">Have an invitation? <span>Create an Account</span></button>
        <div class="firebase-auth-footer">Secure, invitation-only access</div>
      </div>
      <div id="firebase-auth-signup-panel" hidden>
        <h3 class="firebase-auth-secondary-title">Create an Account</h3>
        <p class="firebase-auth-note">Use your approved invitation to join the dashboard.</p>
        <div class="firebase-auth-name-row"><label>First Name<input id="firebase-auth-signup-first-name" type="text" autocomplete="given-name" maxlength="80"></label><label>Last Name<input id="firebase-auth-signup-last-name" type="text" autocomplete="family-name" maxlength="80"></label></div>
        <label>Email<input id="firebase-auth-signup-email" type="email" autocomplete="email"></label>
        <label>Password<input id="firebase-auth-signup-password" type="password" autocomplete="new-password"></label>
        <label>Invitation Code<input id="firebase-auth-invite-code" type="text" autocomplete="one-time-code"></label>
        <button id="firebase-auth-signup" class="firebase-auth-primary" type="button">Create an Account</button>
        <button id="firebase-auth-login-tab" class="firebase-auth-link firebase-auth-back-link" type="button">Already have an account? Sign In</button>
      </div>
      <button id="firebase-auth-logout" type="button" hidden>Sign out</button>
      <div id="firebase-auth-status" role="status" aria-live="polite"></div>
    </div>
  </section>
</div>
"""

FIREBASE_AUTH_CSS = """
.firebase-auth-shell { width: 100%; min-height: 100vh; min-height: 100dvh; position: relative; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); overflow: hidden; background: #004f3d; color: #153d32; font-family: inherit; }
.firebase-auth-branding, .firebase-auth-stage { min-height: 100vh; min-height: 100dvh; position: relative; display: grid; place-items: center; box-sizing: border-box; }
.firebase-auth-branding { isolation: isolate; overflow: hidden; background: transparent; z-index: 3; }
.firebase-auth-stage { z-index: 1; }
.firebase-auth-svg-defs { position: absolute; width: 0; height: 0; overflow: hidden; }
.firebase-auth-curve-layer { position: absolute; inset: 0 auto 0 0; z-index: 2; width: calc(50% + clamp(28px, 4.5vw, 68px)); overflow: hidden; pointer-events: none; background: transparent; }
.firebase-auth-curve-layer::before { content: ""; position: absolute; inset: 0; z-index: 1; background: linear-gradient(to bottom, rgba(5, 15, 20, .05) 0%, rgba(5, 15, 20, .15) 35%, rgba(5, 15, 20, .42) 70%, rgba(5, 15, 20, .55) 100%), linear-gradient(135deg, rgba(255, 174, 71, .16), rgba(205, 95, 23, .1)), linear-gradient(135deg, rgba(5, 29, 22, .08), rgba(4, 45, 31, .26)); clip-path: url(#firebase-auth-farm-clip); }
.firebase-auth-farm-image { position: absolute; inset: 0; z-index: 0; width: 100%; height: 100%; object-fit: cover; object-position: center; clip-path: url(#firebase-auth-farm-clip); }
.firebase-auth-curve-border { position: absolute; inset: 0; z-index: 2; width: 100%; height: 100%; overflow: visible; }
.firebase-auth-curve-border path { fill: none; stroke: #f47c35; stroke-width: 1.8; stroke-linecap: round; }
.firebase-auth-branding-content { position: relative; z-index: 3; text-align: center; color: #fff; padding: 2rem; text-shadow: 0 2px 16px rgba(0,0,0,.35); }
.firebase-auth-logo { display: block; width: min(270px, 62vw); height: auto; margin: 0 auto 1.45rem; }
.firebase-auth-brand-name, .firebase-auth-brand-subtitle { color: #fff; font-size: clamp(2.35rem, 3.8vw, 3rem); font-weight: 800; line-height: 1.08; letter-spacing: -.03em; }
.firebase-auth-brand-subtitle { margin-top: .08rem; }
.firebase-auth-brand-rule { display: block; width: min(228px, 53vw); height: 55px; margin: .25rem auto 0; overflow: visible; }
.firebase-auth-stage { padding: clamp(1rem, 4vw, 3.5rem); background-color: #004f3d; background-image: var(--firebase-auth-green); background-size: cover; background-position: center; }
.firebase-auth-card { width: min(100%, 470px); padding: clamp(1.35rem, 3vw, 2.35rem); border: 1px solid rgba(255,247,232,.6); border-radius: 1rem; background: #fff7e8; color: #174a39; box-shadow: 0 18px 50px rgba(0, 30, 20, .25); box-sizing: border-box; }
.firebase-auth-card h3 { margin: 0 0 .35rem; color: #174a39; font-size: clamp(1.35rem, 2vw, 1.7rem); }.firebase-auth-note { color: #4a685c; margin: 0 0 1rem; }
.firebase-auth-secondary-title { font-size: 1.35rem !important; margin-top: 0 !important; }.firebase-auth-loading { color: #4a685c; min-height: 1.35rem; margin: .4rem 0 .8rem; }
.firebase-auth-card label { display: grid; gap: .3rem; margin: .7rem 0; color: #174a39; font-size: .84rem; font-weight: 700; }.firebase-auth-card input { border: 1px solid #b9cfc0; border-radius: .5rem; padding: .65rem .75rem; background: #fffdf7; color: #173e31; font: inherit; font-weight: 500; box-sizing: border-box; width: 100%; }.firebase-auth-card input:focus { outline: 3px solid rgba(244,124,53,.28); border-color: #f47c35; }
.firebase-auth-name-row { display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; }.firebase-auth-actions { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .95rem; }.firebase-auth-card button { border: 0; border-radius: .5rem; padding: .7rem .8rem; background: #f47c35; color: #173426; font-weight: 750; cursor: pointer; }.firebase-auth-card button[hidden] { display: none; }
.firebase-auth-primary { width: 100%; }.firebase-auth-link { padding: .15rem 0 !important; background: transparent !important; color: #315f4e !important; font-size: .82rem; font-weight: 600 !important; text-align: left; }.firebase-auth-link:hover, .firebase-auth-link:focus-visible { color: #d85e20 !important; text-decoration: underline; }.firebase-auth-forgot { margin-top: -.25rem; }.firebase-auth-invite-link { width: 100%; text-align: center; margin-top: .8rem; }.firebase-auth-invite-link span { color: #c9541c; font-weight: 800; }.firebase-auth-back-link { margin-top: .8rem; width: 100%; text-align: center; }.firebase-auth-divider { height: 1px; background: #d7e0d5; margin: 1.1rem 0 .2rem; }.firebase-auth-footer { color: #6d887a; font-size: .75rem; text-align: center; margin-top: .9rem; }
#firebase-auth-status { margin-top: .8rem; color: #9b3a23; font-size: .84rem; min-height: 0; }
@media (max-width: 760px) { .firebase-auth-shell { grid-template-columns: 1fr; min-height: auto; }.firebase-auth-branding { min-height: 320px; }.firebase-auth-stage { min-height: auto; padding: 1rem; }.firebase-auth-curve-layer { width: 100%; height: 320px; bottom: auto; }.firebase-auth-farm-image { clip-path: none; }.firebase-auth-curve-border { display: none; }.firebase-auth-card { margin: 0 auto; }.firebase-auth-branding-content { padding: 1.5rem; }.firebase-auth-logo { width: min(185px, 70vw); margin-bottom: 1rem; }.firebase-auth-brand-name, .firebase-auth-brand-subtitle { font-size: clamp(2rem, 8vw, 2.5rem); }.firebase-auth-brand-rule { width: min(200px, 65vw); height: 50px; } }
@media (max-width: 430px) { .firebase-auth-name-row { grid-template-columns: 1fr; gap: 0; }.firebase-auth-card { padding: 1.15rem; } }
"""

FIREBASE_AUTH_JS = """
export default function(component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component;
  const config = data?.config || {};
  const authShell = parentElement.querySelector('.firebase-auth-shell');
  const authFarmImage = parentElement.querySelector('#firebase-auth-farm-image');
  const authLogo = parentElement.querySelector('#firebase-auth-logo');
  const authAssets = data?.assets || {};
  if (authShell) {
    if (authAssets.farm) authShell.style.setProperty('--firebase-auth-farm', `url("${authAssets.farm}")`);
    if (authAssets.green) authShell.style.setProperty('--firebase-auth-green', `url("${authAssets.green}")`);
  }
  if (authFarmImage && authAssets.farm) { authFarmImage.src = authAssets.farm; authFarmImage.onerror = () => { authFarmImage.removeAttribute('src'); }; }
  if (authLogo && authAssets.logo) { authLogo.src = authAssets.logo; authLogo.onerror = () => { authLogo.removeAttribute('src'); }; }
  const authTitle = parentElement.querySelector('#firebase-auth-title');
  const authSubtitle = parentElement.querySelector('#firebase-auth-subtitle');
  const loading = parentElement.querySelector('#firebase-auth-loading');
  const authTabs = parentElement.querySelector('#firebase-auth-tabs');
  const email = parentElement.querySelector('#firebase-auth-email');
  const password = parentElement.querySelector('#firebase-auth-password');
  const login = parentElement.querySelector('#firebase-auth-login');
  const reset = parentElement.querySelector('#firebase-auth-reset');
  const verify = parentElement.querySelector('#firebase-auth-verify');
  const logout = parentElement.querySelector('#firebase-auth-logout');
  const loginTab = parentElement.querySelector('#firebase-auth-login-tab');
  const signupTab = parentElement.querySelector('#firebase-auth-signup-tab');
  const loginPanel = parentElement.querySelector('#firebase-auth-login-panel');
  const signupPanel = parentElement.querySelector('#firebase-auth-signup-panel');
  const signupEmail = parentElement.querySelector('#firebase-auth-signup-email');
  const signupPassword = parentElement.querySelector('#firebase-auth-signup-password');
  const signupFirstName = parentElement.querySelector('#firebase-auth-signup-first-name');
  const signupLastName = parentElement.querySelector('#firebase-auth-signup-last-name');
  const inviteCode = parentElement.querySelector('#firebase-auth-invite-code');
  const signup = parentElement.querySelector('#firebase-auth-signup');
  const status = parentElement.querySelector('#firebase-auth-status');
  if (!authTitle || !authSubtitle || !email || !password || !login || !reset || !verify || !logout || !status || !signupEmail || !signupPassword || !signupFirstName || !signupLastName || !inviteCode || !signup || !loginTab || !signupTab || !loginPanel || !signupPanel || !loading || !authTabs) return;
  let idToken = null;
  let firebaseAuth = null;
  let currentUser = null;
  let signupInFlight = false;
  const loadFirebaseSdk = () => {
    if (window.firebase && window.firebase.auth) return Promise.resolve(window.firebase);
    if (window.__bakoFirebaseSdkPromise) return window.__bakoFirebaseSdkPromise;
    const urls = [
      'https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js',
      'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth-compat.js',
    ];
    window.__bakoFirebaseSdkPromise = new Promise((resolve, reject) => {
      const load = (index) => {
        if (index >= urls.length) { resolve(window.firebase); return; }
        const script = document.createElement('script');
        script.src = urls[index];
        script.async = true;
        script.crossOrigin = 'anonymous';
        script.onload = () => load(index + 1);
        script.onerror = () => reject(new Error('Firebase browser SDK could not be loaded.'));
        document.head.appendChild(script);
      };
      load(0);
    });
    return window.__bakoFirebaseSdkPromise;
  };
  const show = (message) => { status.textContent = message; };
  const signInError = (error) => {
    const code = String(error?.code || error?.message || '').toUpperCase();
    if (code.includes('API_KEY_INVALID') || code.includes('INVALID_API_KEY')) return 'Firebase configuration mismatch. Check the Web App API key, auth domain, and App ID for project bako-sweet.';
    if (code.includes('INVALID_LOGIN_CREDENTIALS') || code.includes('INVALID_CREDENTIAL') || code.includes('EMAIL_NOT_FOUND') || code.includes('INVALID_PASSWORD')) return 'Invalid email or password.';
    if (code.includes('USER_DISABLED')) return 'This Firebase account is disabled. Contact an administrator.';
    if (code.includes('OPERATION_NOT_ALLOWED')) return 'Email/password sign-in is not enabled for this Firebase project.';
    if (code.includes('TOO_MANY_ATTEMPTS') || code.includes('TOO_MANY_REQUESTS')) return 'Firebase temporarily limited sign-in attempts. Wait a few minutes, then retry.';
    if (code.includes('FAILED_TO_FETCH') || code.includes('NETWORK') || code.includes('NETWORK-REQUEST-FAILED')) return 'The browser could not reach Firebase. Check your network connection and authorized domain settings.';
    return 'Firebase rejected sign-in. Check the account credentials and email verification status.';
  };
  const verificationError = (error) => {
    const code = String(error?.message || '').toUpperCase();
    if (code.includes('INVALID_ID_TOKEN') || code.includes('TOKEN_EXPIRED') || code.includes('USER_NOT_FOUND') || code.includes('USER_DISABLED')) return 'Your Firebase sign-in session is no longer valid. Sign in again, then retry.';
    if (code.includes('API_KEY_INVALID') || code.includes('OPERATION_NOT_ALLOWED')) return 'Firebase rejected the request. Check the Web App configuration and that Email/Password authentication is enabled.';
    if (code.includes('TOO_MANY_ATTEMPTS')) return 'Firebase temporarily limited verification emails. Wait a few minutes, then retry.';
    if (code.includes('FAILED_TO_FETCH') || code.includes('NETWORK')) return 'The browser could not reach Firebase. Check your network connection and authorized domain settings.';
    return 'Firebase rejected the verification request. Sign in again and retry.';
  };
  const showLogin = () => { loading.hidden = true; authTitle.hidden = false; authSubtitle.hidden = false; authTabs.hidden = true; loginPanel.hidden = false; signupPanel.hidden = true; verify.hidden = true; };
  const showSignup = () => { loading.hidden = true; authTitle.hidden = true; authSubtitle.hidden = true; authTabs.hidden = true; loginPanel.hidden = true; signupPanel.hidden = false; verify.hidden = true; };
  const showRestoring = () => { loading.hidden = false; loading.textContent = 'Restoring your session…'; authTitle.hidden = true; authSubtitle.hidden = true; authTabs.hidden = true; loginPanel.hidden = true; signupPanel.hidden = true; verify.hidden = true; };
  const showUnverified = () => { showLogin(); verify.hidden = false; show('Verify your Firebase email address before accessing the dashboard.'); };
  const loadingRecoveryTimer = window.setTimeout(() => { if (!loading.hidden && !data?.authenticated) { showLogin(); show('Your sign-in session could not be restored. Please sign in again.'); } }, 15000);
  const handleUser = async (user) => {
    currentUser = user || null;
    if (!currentUser) { if (!data?.authenticated && !data?.verification_only) showLogin(); return; }
    try {
      await currentUser.reload();
      if (currentUser.emailVerified !== true) { showUnverified(); return; }
      window.clearTimeout(loadingRecoveryTimer);
      if (!data?.authenticated && !data?.verification_only) {
        showRestoring();
        idToken = await currentUser.getIdToken();
        setTriggerValue('id_token', idToken);
        show('Verifying your account…');
      } else {
        loading.hidden = true;
      }
    } catch (error) {
      currentUser = null;
      showLogin();
      show(signInError(error));
    }
  };
  const normalizeName = (value, label) => {
    const normalized = String(value || '').trim().replace(/\\s+/g, ' ');
    if (!normalized || normalized.length > 80 || !/^[\\p{L}][\\p{L}\\p{M}'’ .\\-]*$/u.test(normalized)) throw new Error(`${label} must contain letters and may include spaces, apostrophes, periods, or hyphens.`);
    return normalized;
  };
  loginTab.onclick = showLogin;
  signupTab.onclick = showSignup;
  login.onclick = async () => {
    try {
      login.disabled = true;
      verify.hidden = true;
      show('Verifying your account…');
      if (!firebaseAuth) throw new Error('Firebase browser SDK is not ready.');
      const credential = await firebaseAuth.signInWithEmailAndPassword(email.value.trim(), password.value);
      await handleUser(credential.user);
    } catch (error) { show(signInError(error)); }
    finally { login.disabled = false; }
  };
  reset.onclick = async () => { try { reset.disabled = true; if (!firebaseAuth) throw new Error('Firebase browser SDK is not ready.'); await firebaseAuth.sendPasswordResetEmail(email.value.trim()); show('If that invited account exists, Firebase sent a password setup link.'); } catch (error) { show('If that invited account exists, Firebase sent a password setup link.'); } finally { reset.disabled = false; } };
  verify.onclick = async () => { try { verify.disabled = true; if (!currentUser) { show('Your Firebase sign-in session is no longer available. Sign in again, then retry.'); return; } await currentUser.sendEmailVerification(); show('Firebase accepted the verification email request. Check your inbox and spam folder.'); } catch (error) { show(verificationError(error)); } finally { verify.disabled = false; } };
  signup.onclick = () => {
    try {
      const firstName = normalizeName(signupFirstName.value, 'First name');
      const lastName = normalizeName(signupLastName.value, 'Last name');
      if (!signupEmail.value.trim() || !signupPassword.value || !inviteCode.value.trim()) { show('Enter your name, email, password, and approved invitation code.'); return; }
      setTriggerValue('signup_request', {email: signupEmail.value.trim(), invite_code: inviteCode.value.trim(), first_name: firstName, last_name: lastName});
    } catch (error) { show(error.message); return; }
    show('Checking your invitation…');
  };
  const registerAfterApproval = async () => {
    if (!data?.signup_approved || signupInFlight || !signupEmail.value.trim() || !signupPassword.value || !inviteCode.value.trim() || !signupFirstName.value.trim() || !signupLastName.value.trim()) return;
    signupInFlight = true;
    try {
      if (!firebaseAuth) throw new Error('Firebase browser SDK is not ready.');
      const credential = await firebaseAuth.createUserWithEmailAndPassword(signupEmail.value.trim(), signupPassword.value);
      currentUser = credential.user;
      await currentUser.updateProfile({displayName: signupFirstName.value.trim() + ' ' + signupLastName.value.trim()});
      let verificationSent = true;
      try { await currentUser.sendEmailVerification(); } catch (error) { verificationSent = false; }
      idToken = await currentUser.getIdToken(true);
      setTriggerValue('signup_result', {id_token: idToken, invite_code: inviteCode.value.trim(), first_name: signupFirstName.value.trim(), last_name: signupLastName.value.trim()});
      show(verificationSent ? 'Account created. Check your email, verify it, then use Sign in.' : 'Account created. Ask Firebase to resend the verification email, then use Sign in.');
      signupPassword.value = '';
    } catch (error) {
      show('Account creation failed. Check the invitation, password, and email address.');
    } finally { signupInFlight = false; }
  };
  logout.onclick = async () => { try { if (firebaseAuth) await firebaseAuth.signOut(); } finally { idToken = null; if (!data?.verification_only) { setTriggerValue('logout', true); setStateValue('signed_in', false); } show('Signed out.'); } };
  if (data?.signup_error) show(data.signup_error);
  if (data?.signup_profile) {
    signupFirstName.value = String(data.signup_profile.first_name || signupFirstName.value || '');
    signupLastName.value = String(data.signup_profile.last_name || signupLastName.value || '');
  }
  if (!config.apiKey || !config.authDomain || !config.appId) { window.clearTimeout(loadingRecoveryTimer); showLogin(); show('Firebase sign-in is unavailable until the local Web App configuration is complete.'); return; }
  if (data?.verification_only) { signupTab.hidden = true; signupPanel.hidden = true; showLogin(); }
  if (data?.authenticated) { window.clearTimeout(loadingRecoveryTimer); loading.hidden = true; authTabs.hidden = true; login.hidden = true; reset.hidden = true; verify.hidden = true; email.disabled = true; password.hidden = true; logout.hidden = false; signupTab.hidden = true; signupPanel.hidden = true; }
  else {
    logout.hidden = true;
    if (!data?.verification_only) {
      showRestoring();
    }
  }
  const initializeFirebaseAuth = async () => {
    try {
      const firebase = await loadFirebaseSdk();
      const appName = 'bako-sweet-auth-' + String(config.projectId || 'default');
      const existingApp = firebase.apps.find((app) => app.name === appName);
      const firebaseApp = existingApp || firebase.initializeApp({
        apiKey: config.apiKey,
        authDomain: config.authDomain,
        projectId: config.projectId,
        appId: config.appId,
      }, appName);
      firebaseAuth = firebaseApp.auth();
      await firebaseAuth.setPersistence(firebase.auth.Auth.Persistence.LOCAL);
      if (data?.force_signout) await firebaseAuth.signOut();
      firebaseAuth.onIdTokenChanged(handleUser);
      if (data?.signup_approved) registerAfterApproval();
    } catch (error) {
      window.clearTimeout(loadingRecoveryTimer);
      showLogin();
      show('Firebase sign-in is unavailable. Check the Web App configuration and network connection.');
    }
  };
  initializeFirebaseAuth();
  return () => {};
}
"""
