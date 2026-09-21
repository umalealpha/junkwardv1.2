# Azure AD SSO setup — omni.alphadirect.co.bw

**Audience:** IT / Tenant Administrator (Cloud Application Administrator or Global Administrator role required in the shared Alpha Direct Entra tenant).

**Time:** ~30 minutes start to finish once you have tenant-admin access.

**Output we need back:** four values to plug into the production `.env`:

| Variable | Where it comes from |
|---|---|
| `AZURE_TENANT_ID` | Tenant ID (same as OneDesk / Reporting / Claims) |
| `AZURE_API_CLIENT_ID` | "Omni API" app registration Application (client) ID |
| `AZURE_API_AUDIENCE` | "Omni API" Application ID URI (typically `api://omni-api`) |
| `AZURE_ADMIN_GROUP_ID` | Object ID of the `omni-admins` security group |

Plus, for the SPA half (frontend, configured separately):

| Variable | Where it comes from |
|---|---|
| `NEXT_PUBLIC_AZURE_TENANT_ID` | Same as backend `AZURE_TENANT_ID` |
| `NEXT_PUBLIC_AZURE_CLIENT_ID` | "Omni Web" app registration Application (client) ID |
| `NEXT_PUBLIC_AZURE_API_SCOPE` | `api://omni-api/access_as_user` (the scope the SPA requests) |

This document mirrors the conventions established for OneDesk
(`d:\ADRisk\onedesk\infra\azure-app-registration.md`) — same tenant, same
pattern, different app registrations. If you've done OneDesk this is the
same procedure with `omni` substituted everywhere.

---

## 0. Prerequisites

- Tenant admin access (Cloud Application Administrator or Global Administrator)
- Production hostname is `https://omni.alphadirect.co.bw` — already DNS-pointed
- Existing OneDesk app registrations remain unchanged; we add two new ones

---

## 1. Register the API ("Omni API")

1. Entra admin centre → **Identity → Applications → App registrations → New registration**.
2. Fill:
   - Name: `Omni API`
   - Supported account types: **Accounts in this organisational directory only (single tenant)**
   - Redirect URI: leave empty (this is a backend resource, not a UI).
3. Click **Register**.
4. On the new app's **Overview** page, copy:
   - `Application (client) ID` → this is `AZURE_API_CLIENT_ID`
   - `Directory (tenant) ID` → this is `AZURE_TENANT_ID` (same for the SPA app, same as OneDesk)
5. **Manage → Expose an API**:
   - Click **Set** next to *Application ID URI*. Set it to `api://omni-api` and **Save**.
   - **Add a scope**:
     - Scope name: `access_as_user`
     - Who can consent: **Admins and users**
     - Admin consent display name: `Access Omni on behalf of the signed-in user`
     - Admin consent description: `Allows Omni Web to call the Omni API as the signed-in user.`
     - State: **Enabled**
     - **Add scope**.

⚠️ Do **NOT** create a client secret on this app — the SPA uses auth-code + PKCE.

---

## 2. Register the SPA ("Omni Web")

1. **App registrations → New registration**.
2. Fill:
   - Name: `Omni Web`
   - Supported account types: same as API (single tenant)
   - Redirect URI:
     - Platform: **Single-page application (SPA)**
     - URL: `https://omni.alphadirect.co.bw`
3. **Register**, then on the Overview page copy:
   - `Application (client) ID` → this is `NEXT_PUBLIC_AZURE_CLIENT_ID`
4. **Manage → API permissions → Add a permission**:
   - **Microsoft Graph → Delegated permissions** → `User.Read` (default, ensure present)
   - **My APIs → Omni API** → tick `access_as_user`
   - Click **Grant admin consent for <tenant>** so users don't see a consent prompt on first sign-in
5. **Manage → Authentication**:
   - Confirm `https://omni.alphadirect.co.bw` is listed under SPA redirect URIs
   - (Optional) Add `http://localhost:3001` under SPA for local development
   - **Front-channel logout URL**: `https://omni.alphadirect.co.bw`
   - Implicit grant: leave **off** (we use auth-code + PKCE)
   - Allow public client flows: **off**

---

## 3. Create the Admin group

This group's members get the **SUPER_ADMIN** role on first sign-in (in addition to whatever else the in-app RBAC grants them).

1. **Identity → Groups → New group**.
2. Settings:
   - Group type: **Security**
   - Name: `omni-admins`
   - Membership type: **Assigned**
3. Add the initial admin user:
   - **prathap.ganesharajah@alphadirect.co.bw** *(or pganesharajah@alphadirect.co.bw — confirm exact UPN)*
4. On the new group's overview, copy `Object Id` → this is `AZURE_ADMIN_GROUP_ID`.

---

## 4. Emit groups in tokens

1. Open the **Omni API** registration → **Manage → Token configuration → Add groups claim**.
2. Choose:
   - Group type: **Security groups**
   - Customise per token type → ID, Access, SAML → emit **Group ID**
3. **Save**.

If the tenant has more than 200 groups per user, Azure returns an overage indicator instead of the full `groups` claim. We don't expect Alpha Direct to hit this; if you do, raise it back to engineering and we'll add Graph fallback.

---

## 5. Hand the values back

Email a single message back to **erp@theriskco.com** with:

```
AZURE_TENANT_ID         = <value>
AZURE_API_CLIENT_ID     = <value>
AZURE_API_AUDIENCE      = api://omni-api
AZURE_ADMIN_GROUP_ID    = <value>
NEXT_PUBLIC_AZURE_CLIENT_ID = <value>
NEXT_PUBLIC_AZURE_API_SCOPE = api://omni-api/access_as_user
```

These are not secrets (client IDs and tenant IDs are public-by-design in
Azure AD's auth-code + PKCE flow), so plain email is fine. The actual
authentication relies on the user's Microsoft credentials and the
group-membership claim — neither of which travels through these IDs.

---

## 6. What happens on the engineering side after step 5

1. Engineering adds the values to `/etc/alpha-finance/.env` on the production EC2:
   ```
   AZURE_SSO_ENABLED=True
   AZURE_TENANT_ID=...
   AZURE_API_CLIENT_ID=...
   AZURE_API_AUDIENCE=api://omni-api
   AZURE_ADMIN_GROUP_ID=...
   ```
2. `docker compose restart backend` — the new `AzureJWTAuthentication` class becomes active in DRF's auth chain
3. Frontend MSAL.js wiring deployed in a follow-up release (engineering work, no IT action needed)
4. Smoke test:
   - Sign out of the current admin session
   - Visit `https://omni.alphadirect.co.bw` in a private window
   - Expect redirect to `login.microsoftonline.com`
   - Sign in as Prathap → should land on the dashboard
   - Sign in as a tenant user **not** in `omni-admins` → should land on the dashboard with limited menu (no Administration section)

---

## 7. Rotation & teardown

- Client IDs and tenant IDs don't rotate. `omni-admins` group ID is stable.
- To deactivate Omni for a user: remove them from `omni-admins` (drops them to default access) and/or block their sign-in to the SPA registration.
- If the API app registration must be rotated, create a new one, update `AZURE_API_AUDIENCE`, and leave the old one valid until the new is verified — then delete.
