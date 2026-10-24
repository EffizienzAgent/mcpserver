# Connecting MCP clients

The server URL is

```
https://<site>/api/method/mcpserver.mcp.endpoint
```

The endpoint accepts POST only and answers with plain JSON (no SSE stream, no session ID). Clients that
support Streamable HTTP and OAuth 2.1 with dynamic client registration sign in by themselves: they get a
`401` with a `WWW-Authenticate` header pointing to `/.well-known/oauth-protected-resource`, register, open
the browser for login and consent, and refresh their tokens.

The client acts as the user who clicked **Allow**, with that user's roles and permissions. Users need desk
access (the **Desk User** role); website users cannot authorize clients.

## Claude Code

```sh
claude mcp add --scope user --transport http mysite https://<site>/api/method/mcpserver.mcp.endpoint
```

Run `/mcp` in a session (or `claude mcp login mysite`) to sign in. Claude Code uses a
`http://localhost:<port>/callback` redirect URI, which the server always accepts. Access tokens live one
hour and refresh automatically.

## claude.ai and Claude Desktop

Add a custom connector (**Settings > Connectors > Add custom connector**) with the server URL. Claude
connects from Anthropic's servers, so the site must be reachable from the internet. The callbacks
`https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback` are allowed by
default.

## Other clients

Any client that supports remote MCP servers over Streamable HTTP works the same way, for example the
[MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```sh
npx @modelcontextprotocol/inspector --cli https://<site>/api/method/mcpserver.mcp.endpoint \
  --transport http --header "Authorization: token <api_key>:<api_secret>" --method tools/list
```

Loopback redirect URIs (`http://localhost`, `127.0.0.1` or `[::1]`, any port and path) are always
accepted. A client that uses an `https://` callback needs it listed in the site config. The list replaces
the Claude defaults, so keep them if you still need them:

```sh
bench --site <site> set-config -p mcp_oauth_redirect_uris \
  '["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback", "https://client.example.com/oauth/callback"]'
```

Entries must match exactly and start with `https://`. An invalid value allows no `https://` callback at
all.

## API keys

For scripts or clients without OAuth, generate an API key for a user (**User > Settings > API Access >
Generate Keys**) and send it as a header:

```sh
claude mcp add --scope user --transport http mysite \
  https://<site>/api/method/mcpserver.mcp.endpoint \
  --header "Authorization: token <api_key>:<api_secret>"
```

Keep keys out of version control. In a project `.mcp.json`, reference an environment variable instead,
for example `"Authorization": "token ${MYSITE_TOKEN}"`.

## Revoking access

- Users remove a client under `/third_party_apps`; this revokes all its tokens.
- System Managers set tokens to **Revoked** in the **OAuth Bearer Token** list.
- Refresh tokens expire 30 days after they were issued.

## Troubleshooting

| Response | Meaning |
| --- | --- |
| `401` | No or invalid credentials. OAuth clients start the sign-in from here. |
| `403` | The request came with an `Origin` header for another host. |
| `405` | GET or DELETE; MCP requests must be POSTed. |
| `406` | The `Accept` header does not allow `application/json`. |
| `429` | Too many requests (300 per minute per IP, 20 client registrations per hour per IP). |

With `Content-Type: application/json`, Frappe parses the body before the endpoint runs, so invalid JSON
gets Frappe's own error response instead of a JSON-RPC parse error.
