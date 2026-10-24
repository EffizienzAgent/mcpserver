# OAuth

Frappe's OAuth 2 provider does the work. `mcpserver` turns on what MCP clients need and makes it
stricter.

| Step | Endpoint | Provided by |
| --- | --- | --- |
| `401` with `WWW-Authenticate: Bearer resource_metadata="…"` | MCP endpoint | Frappe, for every 401 |
| Protected resource metadata (RFC 9728) | `/.well-known/oauth-protected-resource` | Frappe |
| Authorization server metadata (RFC 8414) | `/.well-known/oauth-authorization-server` | Frappe |
| Dynamic client registration (RFC 7591) | `frappe.integrations.oauth2.register_client` | `mcpserver.oauth.register_client` |
| Login, consent and authorization code | `frappe.integrations.oauth2.authorize` / `approve` | Frappe, wrapped to require PKCE |
| Token and refresh | `frappe.integrations.oauth2.get_token` | Frappe, wrapped with extra checks |
| Bearer token validation | every request | Frappe |

The endpoint URLs stay Frappe's: the app overrides the whitelisted methods through
`override_whitelisted_methods`. Issuer, resource and endpoint URLs follow the request host, so a site
reachable under several host names works under each of them.

## Settings

The app needs these **OAuth Settings**: authorization server metadata and protected resource metadata on,
dynamic client registration on, skip authorization off. Frappe v16 uses these values by default. On install
and migrate, the app only fills in settings that were never stored. It never changes a stored value, so
anything you switch off stays off. Set **Resource Name** yourself; clients show it.

## Registration

- Public clients only (`token_endpoint_auth_method: none`). Frappe does not check client secrets, so
  confidential registrations are refused instead of giving a false sense of security.
- Exactly one redirect URI: loopback (`http://localhost`, `127.0.0.1` or `[::1]`, any port and path; no
  user info, fragment or whitespace) or an exact match from the site config `mcp_oauth_redirect_uris`
  (default: the claude.ai and claude.com callbacks). The URI is stored as sent, so the authorize-time
  exact match works.
- Only the `code` response type and the `authorization_code` and `refresh_token` grants. Scopes must be
  plain tokens; the default is `all`.
- `client_name` must be plain text (letters, digits, spaces and `. _ ( ) - : / +`, up to 120 characters),
  because Frappe's consent page shows it. It is also HTML-escaped when stored.
- 20 registrations per IP and hour, at most 200 registered clients (then `429`). Registration can be
  switched off in OAuth Settings (`404`).
- A registered client gets nothing by itself. Tokens are only issued after the user signs in and clicks
  **Allow**, and only to the registered redirect URI.

Clients from this registration are marked with `software_id = mcpserver-dcr`. Clients created by hand in
the desk keep Frappe's behaviour.

## Authorization

Authorization requests of registered clients need `code_challenge` with `code_challenge_method=S256`.
Otherwise the browser goes back to the registered redirect URI with `error=invalid_request`, or gets a
JSON `400` if the redirect URI does not match. Frappe checks the `code_verifier` when the code is
exchanged, and a code can only be used once.

## Tokens

- An unknown `client_id` gets `401 invalid_client`.
- A refresh request for a registered client without `client_id` gets `400 invalid_request`.
- A refresh token that is unknown, revoked or belongs to another client gets `400 invalid_grant`.
- Each refresh rotates the token: the client gets a new access and refresh token, and the presented
  token (access and refresh) is revoked at once. Frappe alone issues a new refresh token but leaves the
  old one valid, so a stolen refresh token would keep working.
- The presented refresh token is locked and claimed atomically, so of two overlapping refreshes with the
  same token only one gets new tokens; the other gets `400 invalid_grant`.
- Presenting a refresh token again within 60 seconds of its rotation (a retry or a parallel session)
  gets `400 invalid_grant` and changes nothing else. Presenting it later is treated as theft: all active
  tokens of that client and user are revoked, and the user signs in again.
- Trade-off of the grace window: a stolen refresh token that is used within 60 seconds of a rotation is
  refused but not detected as reuse, so the grant is not revoked in that case.
- A sign-in lasts at most 30 days. Rotated tokens keep the issue time of the first token, so refreshing
  does not extend it; after that the refresh gets `400 invalid_grant` and the user signs in again.
- Access tokens live one hour. Revoked and expired access tokens are rejected by Frappe.

## Token audience

Access tokens of registered clients are only accepted on the MCP endpoint, its aliases and the OAuth
token endpoints (`get_token`, `revoke_token`, `introspect_token`, `openid_profile`). Anywhere else, for
example `/api/resource/...`, `/api/v2/mcp`, desk pages or private files, the request gets `403`. This also covers
the deprecated `cmd` parameter (query string, form or JSON body), which Frappe dispatches before any path
routing: with such a token, `cmd` must name an allowed method too. Requests without a bearer token, API
keys and manually created OAuth clients are not affected.

Aliases are found automatically: every key in `override_whitelisted_methods` that points to
`mcpserver.mcp.endpoint`. Allow further whitelisted methods for these tokens with a hook:

```python
mcp_bearer_methods = ["myapp.api.agent_status"]
```

## Cleanup

A daily job deletes tokens of registered clients that were revoked more than 7 days ago or issued more than
37 days ago, and registered clients older than 7 days that have no token left.
