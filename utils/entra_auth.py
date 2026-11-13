"""Disabled-by-default Microsoft Entra ID/OIDC authorization helpers.

Streamlit performs the OIDC protocol and exposes validated identity claims via
``st.user``. This module adds the application-specific tenant and role checks
that must happen before a future dashboard session is allowed to publish a
shared workbook. It never stores client secrets, access tokens, or raw claims.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Any, Mapping


MICROSOFT_PROVIDER_NAME = "microsoft"
COMPANY_EMAIL_DOMAIN = "countrysweet.com"


class EmployeeRole(str, Enum):
    ADMIN = "admin"
    PUBLISHER = "publisher"
    VIEWER = "viewer"


ROLE_PERMISSIONS: dict[EmployeeRole, frozenset[str]] = {
    EmployeeRole.ADMIN: frozenset({"view_dashboard", "publish_workbook", "manage_access"}),
    EmployeeRole.PUBLISHER: frozenset({"view_dashboard", "publish_workbook"}),
    EmployeeRole.VIEWER: frozenset({"view_dashboard"}),
}


@dataclass(frozen=True)
class EntraAuthConfig:
    """Runtime policy; secrets remain in Streamlit secrets/deployment config."""

    enabled: bool = False
    tenant_id: str | None = None
    allowed_email_domain: str = COMPANY_EMAIL_DOMAIN
    provider_name: str = MICROSOFT_PROVIDER_NAME
    admin_role_names: frozenset[str] = frozenset({"admin", "bako-sweet.admin", "bako sweet admin"})
    publisher_role_names: frozenset[str] = frozenset({"publisher", "bako-sweet.publisher", "bako sweet publisher"})

    @classmethod
    def from_environment(cls) -> "EntraAuthConfig":
        enabled = os.getenv("BAKO_MICROSOFT_AUTH_ENABLED", "false").strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        tenant_id = os.getenv("BAKO_MICROSOFT_TENANT_ID", "").strip() or None
        domain = os.getenv("BAKO_ALLOWED_EMAIL_DOMAIN", COMPANY_EMAIL_DOMAIN).strip().casefold()
        admin_roles = _configured_role_names(
            os.getenv("BAKO_MICROSOFT_ADMIN_ROLES"),
            {"admin", "bako-sweet.admin", "bako sweet admin"},
        )
        publisher_roles = _configured_role_names(
            os.getenv("BAKO_MICROSOFT_PUBLISHER_ROLES"),
            {"publisher", "bako-sweet.publisher", "bako sweet publisher"},
        )
        return cls(
            enabled=enabled,
            tenant_id=tenant_id,
            allowed_email_domain=domain,
            admin_role_names=admin_roles,
            publisher_role_names=publisher_roles,
        )


@dataclass(frozen=True)
class AuthenticatedEmployee:
    """Safe identity fields suitable for audit metadata."""

    employee_id: str
    tenant_id: str
    email: str | None
    display_name: str | None
    role: EmployeeRole

    @property
    def permissions(self) -> frozenset[str]:
        return ROLE_PERMISSIONS[self.role]

    def can(self, permission: str) -> bool:
        return permission in self.permissions


class AuthConfigurationError(RuntimeError):
    """Raised when authentication is requested without administrator config."""


class UnauthorizedEmployeeError(RuntimeError):
    """Raised when a logged-in identity fails tenant or identity validation."""


def _claim_text(claims: Mapping[str, Any], name: str) -> str | None:
    value = claims.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _configured_role_names(value: str | None, defaults: set[str]) -> frozenset[str]:
    if not value:
        return frozenset(defaults)
    return frozenset(item.strip().casefold() for item in value.split(",") if item.strip())


def _claim_roles(claims: Mapping[str, Any]) -> set[str]:
    value = claims.get("roles", [])
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return {str(item).strip().casefold() for item in values if str(item).strip()}


def _role_from_claims(claims: Mapping[str, Any], config: EntraAuthConfig) -> EmployeeRole:
    roles = _claim_roles(claims)
    if roles & config.admin_role_names:
        return EmployeeRole.ADMIN
    if roles & config.publisher_role_names:
        return EmployeeRole.PUBLISHER
    # A validated employee may view the dashboard by default. Elevated access
    # must come from an Entra app-role claim, never from a typed name/email.
    return EmployeeRole.VIEWER


def employee_from_claims(
    claims: Mapping[str, Any],
    *,
    config: EntraAuthConfig | None = None,
) -> AuthenticatedEmployee:
    """Validate a Streamlit OIDC claim mapping and return a safe identity."""
    config = config or EntraAuthConfig.from_environment()
    if not config.enabled:
        raise AuthConfigurationError("Microsoft authentication is disabled by configuration.")
    if not config.tenant_id:
        raise AuthConfigurationError("BAKO_MICROSOFT_TENANT_ID is required when authentication is enabled.")

    tenant_id = _claim_text(claims, "tid")
    if tenant_id != config.tenant_id:
        raise UnauthorizedEmployeeError("Authenticated tenant does not match the configured company tenant.")

    issuer = _claim_text(claims, "iss")
    expected_issuer = f"https://login.microsoftonline.com/{config.tenant_id}/v2.0"
    if issuer and issuer.rstrip("/") != expected_issuer:
        raise UnauthorizedEmployeeError("OIDC issuer does not match the configured Microsoft tenant.")

    employee_id = _claim_text(claims, "oid") or _claim_text(claims, "sub")
    if not employee_id:
        raise UnauthorizedEmployeeError("Authenticated identity has no stable employee identifier.")

    email = _claim_text(claims, "preferred_username") or _claim_text(claims, "email")
    if not email or "@" not in email:
        raise UnauthorizedEmployeeError("Authenticated identity has no usable company email claim.")
    if config.allowed_email_domain and email.rsplit("@", 1)[1].casefold() != config.allowed_email_domain:
        raise UnauthorizedEmployeeError("Authenticated email domain is not allowed for this dashboard.")

    return AuthenticatedEmployee(
        employee_id=employee_id,
        tenant_id=tenant_id,
        email=email,
        display_name=_claim_text(claims, "name"),
        role=_role_from_claims(claims, config),
    )


def current_employee(*, config: EntraAuthConfig | None = None) -> AuthenticatedEmployee | None:
    """Read the current Streamlit identity without initiating a login flow.

    This is intentionally non-blocking while the feature flag is false. A
    future app entrypoint can call ``st.login(config.provider_name)`` only after
    the administrator supplies the matching Streamlit ``[auth.microsoft]``
    secrets configuration.
    """
    config = config or EntraAuthConfig.from_environment()
    if not config.enabled:
        return None
    try:
        import streamlit as st

        user = st.user
        if not getattr(user, "is_logged_in", False):
            return None
        claims = {key: user[key] for key in user.keys()}
    except Exception as exc:  # pragma: no cover - depends on Streamlit runtime
        raise UnauthorizedEmployeeError(f"Unable to read the Streamlit OIDC identity: {exc}") from exc
    return employee_from_claims(claims, config=config)
