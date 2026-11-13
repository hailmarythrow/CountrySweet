# Firebase Authentication development flow

Firebase Authentication remains disabled unless `BAKO_FIREBASE_AUTH_ENABLED=true`.
The existing Entra integration is unchanged and remains disabled.

## One-time project-admin bootstrap

1. In Firebase Console, enable **Authentication → Sign-in method → Email/Password**.
2. Create your own verified Firebase user account in the Console. Do not put a
   password in this repository.
3. Look up that user's Firebase Auth UID.
4. Configure `BAKO_FIREBASE_BOOTSTRAP_TOKEN` only in the local shell, run
   `scripts/firebase_auth_admin.py bootstrap-admin <UID>`, and enter the same
   token when prompted.
5. Remove or rotate the bootstrap token after the command succeeds. The
   `bako_sweet_auth_bootstrap/current` Firestore marker prevents a second run.

The command requires ADC and only assigns the `admin` custom claim to the
already-created, email-verified UID. It does not create a password.
The bootstrap command refuses an unverified UID; complete Firebase's email
verification flow (or mark the account verified through the authorized Console
workflow) before running it.
Sign out and sign in again after bootstrap so the browser receives the new
admin custom claim.

## Invite-only teammates

An admin can run:

```powershell
.venv312\Scripts\python.exe scripts\firebase_auth_admin.py invite teammate@school.edu --role viewer --admin-uid ADMIN_UID
```

The command creates a pending, one-time invitation in the development
Firestore namespace and prints a private invitation code. It does not create
an Auth account, accept a password, or assign a role before the teammate
completes registration. Deliver the code out-of-band; it is not stored in
Firestore in plaintext.

The teammate selects **Create an Account** in the dashboard, enters the
invited email, a password, and the code. The server validates and atomically
consumes the invitation, assigns its requested `admin`, `publisher`, or
`viewer` custom claim, and Firebase sends a verification email. The account
cannot enter the dashboard until that email is verified. There is no open
public registration path.

An invitation is valid for seven days and can be used once. If it is lost or
expires, an administrator should create a new invitation rather than reusing
the code.

An administrator can change an existing user's role or revoke access from a
trusted terminal (the supplied UID is a Firebase Auth UID, never a typed
display name):

```powershell
.venv312\Scripts\python.exe scripts/firebase_auth_admin.py set-role USER_UID publisher --admin-uid ADMIN_UID
.venv312\Scripts\python.exe scripts/firebase_auth_admin.py revoke USER_UID --admin-uid ADMIN_UID
```

The invitation is the approval step: only a pending invitation can be
consumed, and the server assigns the role after Firebase verifies the newly
created account. A revoked user is disabled and has refresh tokens revoked.

## Local activation configuration

Keep these values out of GitHub (use an untracked `.streamlit/secrets.toml`, a
local environment, or deployment secret manager):

For repeatable local launches, create the ignored file
`.csub-local\firebase-auth.env.cmd` beside `Start-Dashboard.cmd` with the
following contents, replacing only the three Firebase Web App values from the
Firebase Console:

```bat
@echo off
set "BAKO_FIREBASE_AUTH_ENABLED=true"
set "BAKO_FIREBASE_WEB_API_KEY=your Web App apiKey"
set "BAKO_FIREBASE_AUTH_DOMAIN=bako-sweet.firebaseapp.com"
set "BAKO_FIREBASE_WEB_APP_ID=your Web App appId"
set "BAKO_FIREBASE_REQUIRE_EMAIL_VERIFICATION=true"
```

`Start-Dashboard.cmd` loads this file automatically. It is under `.csub-local\`,
which is ignored by Git. Do not commit that file if it contains local
configuration. To temporarily disable login, set
`BAKO_FIREBASE_AUTH_ENABLED=false` in that local file; server-side permission
checks remain in place whenever authentication is enabled. If the file is
absent, the launcher keeps the application's existing safe default of
authentication disabled.

```powershell
$env:BAKO_FIREBASE_AUTH_ENABLED = "true"
$env:BAKO_FIREBASE_WEB_API_KEY = "Firebase web-app API key"
$env:BAKO_FIREBASE_AUTH_DOMAIN = "bako-sweet.firebaseapp.com"
$env:BAKO_FIREBASE_WEB_APP_ID = "Firebase web-app ID"
$env:BAKO_FIREBASE_REQUIRE_EMAIL_VERIFICATION = "true"
```

## Verification-only panel for an existing account

To request Firebase's official verification email without enabling mandatory
dashboard login, set only the development panel flag and the Firebase Web App
configuration in the same PowerShell window used to launch the app:

```powershell
$env:BAKO_FIREBASE_AUTH_ENABLED = "false"
$env:BAKO_FIREBASE_AUTH_DEV_PANEL = "true"
$env:BAKO_FIREBASE_WEB_API_KEY = "your Web App apiKey"
$env:BAKO_FIREBASE_AUTH_DOMAIN = "bako-sweet.firebaseapp.com"
$env:BAKO_FIREBASE_WEB_APP_ID = "your Web App appId"
.\Start-Dashboard.cmd
```

The panel appears in the sidebar. Sign in with the existing Firebase account,
then select **Send verification email**. The browser calls Firebase's official
verification-email endpoint; the dashboard does not store the password or raw
ID token. Open the email and complete verification, then confirm the user's
**Email verified** value in Firebase Console. Stop the dashboard and remove
the panel flag afterward:

```powershell
Remove-Item Env:BAKO_FIREBASE_AUTH_DEV_PANEL
```

This panel does not assign a role, run the admin bootstrap, or grant dashboard
access. Keep `BAKO_FIREBASE_AUTH_ENABLED` set to `false` until verification and
administrator recovery testing are complete.

The web API key is client configuration, not an Admin credential. ADC or a
secret-managed service credential remains server-only. The Streamlit server
verifies every ID token with `check_revoked=True`, stores only verified identity
fields in session state, and never logs raw tokens or passwords.

## Publication identity

Development snapshot metadata now supports `publisher_uid` and
`publisher_display_name`, plus a `publication_outcome` marker. These fields are
written only on successful publication and are restored with the active snapshot. The dashboard displays
the saved publisher and server timestamp in `America/Los_Angeles`; opening the
dashboard or changing filters does not update them.

Viewers can read the active snapshot but cannot publish. Publisher/admin checks
are enforced in the server-side workbook activation path, not only by hiding a
button. The existing expected-version Firestore transaction still protects
against stale concurrent publication attempts.

## Activation and recovery checklist

Keep `BAKO_FIREBASE_AUTH_ENABLED` unset or `false` while setting up the first
account. Before enabling it, verify that the administrator can sign in,
receive a password-reset email, and recover the account through Firebase
Console. Then configure the web-app values and set the flag to `true` in the
local launch environment. The existing `Start-Dashboard.cmd` does not enable
authentication by default, so the current local workflow remains available.

The browser keeps Firebase tokens in memory only; Streamlit stores verified
UID/email/display name/role, never a raw token or password.
