"""Privileged development helper for Firebase Auth role management.

This script never accepts or stores passwords. Run it only from a trusted
developer machine with Firebase Admin ADC configured. It is intentionally not
called by the Streamlit app.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.firebase_auth import FirebaseEmployee, assign_role, bootstrap_admin, invite_teammate, revoke_user
from utils.firebase_admin_client import get_firebase_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage invited Firebase dashboard users")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap-admin", help="Grant admin to one existing verified Firebase UID")
    bootstrap.add_argument("uid", help="Firebase Auth UID created through Firebase Console")

    invite = subparsers.add_parser("invite", help="Create a one-time invitation code for a teammate")
    invite.add_argument("email")
    invite.add_argument("--role", choices=("admin", "publisher", "viewer"), default="viewer")
    invite.add_argument("--display-name", default=None)
    invite.add_argument("--admin-uid", required=True, help="Existing Firebase Auth UID with the admin role")
    set_role = subparsers.add_parser("set-role", help="Change an existing Firebase user's dashboard role")
    set_role.add_argument("uid")
    set_role.add_argument("role", choices=("admin", "publisher", "viewer"))
    set_role.add_argument("--admin-uid", required=True, help="Existing Firebase Auth UID with the admin role")
    revoke = subparsers.add_parser("revoke", help="Disable a Firebase user's dashboard access")
    revoke.add_argument("uid")
    revoke.add_argument("--admin-uid", required=True, help="Existing Firebase Auth UID with the admin role")

    args = parser.parse_args()
    get_firebase_app()
    if args.command == "bootstrap-admin":
        # Prefer the already-generated environment value so it never needs to
        # be echoed, copied, logged, or retyped. Fall back to a hidden prompt
        # for manual use when no environment token was supplied.
        token = os.environ.get("BAKO_FIREBASE_BOOTSTRAP_TOKEN")
        if not token:
            token = getpass.getpass("One-time bootstrap token (BAKO_FIREBASE_BOOTSTRAP_TOKEN): ")
        uid = bootstrap_admin(args.uid, bootstrap_token=token)
        print(f"Admin role assigned to verified Firebase UID {uid}.")
        return 0

    from firebase_admin import auth

    admin_user = auth.get_user(args.admin_uid)
    claims = dict(admin_user.custom_claims or {})
    if claims.get("role") != "admin":
        raise SystemExit("The supplied admin UID does not currently have the admin role.")
    admin = FirebaseEmployee(
        uid=admin_user.uid,
        email=admin_user.email or "",
        display_name=admin_user.display_name or admin_user.email or admin_user.uid,
        role="admin",
        expires_at=datetime.now(timezone.utc).replace(year=2099),
    )
    if args.command == "set-role":
        assign_role(args.uid, args.role, admin=admin)
        print(f"Dashboard role for Firebase UID {args.uid} set to {args.role}.")
        return 0
    if args.command == "revoke":
        revoke_user(args.uid, admin=admin)
        print(f"Dashboard access revoked for Firebase UID {args.uid}.")
        return 0
    invitation_code = invite_teammate(
        args.email,
        display_name=args.display_name,
        role=args.role,
        admin=admin,
    )
    print("Invitation created. Deliver this one-time code through an approved private channel:")
    print(invitation_code)
    print("The teammate must choose Create an Account, enter the invited email and this code, and choose a password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
