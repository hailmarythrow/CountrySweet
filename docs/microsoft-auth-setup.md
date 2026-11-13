# Microsoft Entra ID preparation

Microsoft authentication is prepared but disabled by default. The existing
dashboard continues to use its current local workflow until a Country Sweet
administrator approves and configures the identity provider.

## Streamlit OIDC configuration

Streamlit 1.66 supports `st.login`, `st.user`, and `st.logout` with OpenID
Connect. Configure the following in the untracked local file
`.streamlit/secrets.toml` or in the deployment secret manager. Do not commit
this file:

```toml
[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "GENERATE-A-LONG-RANDOM-VALUE"

[auth.microsoft]
client_id = "ENTRA-APPLICATION-CLIENT-ID"
client_secret = "ENTRA-CLIENT-SECRET"
server_metadata_url = "https://login.microsoftonline.com/ENTRA-TENANT-ID/v2.0/.well-known/openid-configuration"
```

Set the feature flag only after the administrator has configured the Entra
application and redirect URI:

```powershell
$env:BAKO_MICROSOFT_AUTH_ENABLED = "true"
$env:BAKO_MICROSOFT_TENANT_ID = "ENTRA-TENANT-ID"
$env:BAKO_ALLOWED_EMAIL_DOMAIN = "countrysweet.com"
# Optional: match the exact app-role values created by the administrator.
$env:BAKO_MICROSOFT_ADMIN_ROLES = "BakoSweet.Admin"
$env:BAKO_MICROSOFT_PUBLISHER_ROLES = "BakoSweet.Publisher"
```

The module does not call `st.login()` yet. Once activated in a future app
entrypoint, employees will be redirected to Microsoft, and Streamlit will
expose the validated OIDC claims through `st.user`.

## Identity and roles

`utils/entra_auth.py` validates:

- the exact Entra tenant ID (`tid` claim),
- the OIDC issuer for that tenant,
- a stable employee ID (`oid`, falling back to `sub`), and
- the company email domain as an additional policy check.

The email suffix is never treated as proof of authorization. Elevated access
comes only from Microsoft Entra app-role claims:

| Role | Intended permissions |
| --- | --- |
| `admin` | View dashboard, publish workbooks, manage access |
| `publisher` | View dashboard and publish validated workbooks |
| `viewer` | View dashboard only |

When a successful workbook refresh is eventually published, the Firestore
metadata field `publishing_user_id` should receive the validated Entra `oid`,
not a manually typed employee name or email.

## Administrator inputs still required

Country Sweet's Microsoft administrator must provide the tenant ID, register
the Streamlit redirect URI, create the client secret, assign the approved
company users/groups, and define the app roles. Authentication should remain
disabled until those values and the production callback URL are approved.
