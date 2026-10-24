MCP server with OAuth for the Frappe Framework

# mcpserver

`mcpserver` turns a Frappe site into a remote [MCP](https://modelcontextprotocol.io) server that Claude and
other AI clients can sign in to with OAuth. It serves curated, task-level tools that your apps register,
for example "get customer profile" or "log a meeting note", instead of raw document access. Every tool call
runs as the signed-in user, with that user's permissions.

- **One endpoint.** Streamable HTTP (protocol 2025-06-18, 2025-03-26 and 2024-11-05) with plain JSON
  responses at `/api/method/mcpserver.mcp.endpoint`.
- **OAuth sign-in for MCP clients.** Uses Frappe's OAuth 2 provider with discovery metadata and dynamic
  client registration, and makes it stricter: public clients only, loopback or allowlisted redirect URIs,
  PKCE S256, plain-text client names, refresh token rotation with reuse detection, tokens bound to the MCP
  endpoint, a registration rate limit and a daily cleanup of stale clients and tokens.
- **Tool registry for apps.** Any installed app can add its own tools through the `mcp_tools` hook.
- **Safe tool calls.** Arguments are checked against the tool's schema. A failed call rolls back everything
  it wrote, including commit callbacks, and unexpected errors never leak tracebacks.

## Install

```sh
bench get-app https://github.com/EffizienzAgent/mcpserver
bench --site <site> install-app mcpserver
```

Requires Frappe v16. The app ships no tools of its own; install an app that registers tools through the
`mcp_tools` hook, or write your own (see [docs/tools.md](docs/tools.md)). OAuth discovery metadata and dynamic client registration must be on in **OAuth
Settings**, as they are by default. The app fills in these settings only where none are stored and never
changes a stored value.

## Connect a client

```sh
claude mcp add --scope user --transport http mysite https://<site>/api/method/mcpserver.mcp.endpoint
```

Claude Code opens the browser on first use. Sign in to the site and click **Allow**. For claude.ai, Claude
Desktop, other MCP clients and API keys, see [docs/clients.md](docs/clients.md).

## Documentation

- [docs/clients.md](docs/clients.md): connecting Claude and other MCP clients, revoking access.
- [docs/tools.md](docs/tools.md): adding tools from your app, server info and instructions hooks.
- [docs/builtin-mcp.md](docs/builtin-mcp.md): how this relates to Frappe's built-in MCP server.
- [docs/oauth.md](docs/oauth.md): how sign-in works and what the app enforces.

## Frappe's built-in MCP server

Frappe v16 ships a generic MCP server at `/api/v2/mcp`. Its tools create, update and delete documents of
any doctype the user may access and call any whitelisted method. `mcpserver` complements it: a separate
endpoint with OAuth sign-in for Claude and only the curated tools your apps register.

If agents should be limited to curated tools, turn the built-in server off: uncheck **System Settings >
AI Agents > Enable MCP**, or set `disable_mcp_server` in the site config. `mcpserver` never changes this
setting itself; it prints a hint on install while the built-in server is on. See
[docs/builtin-mcp.md](docs/builtin-mcp.md).

## Development

```sh
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app mcpserver
```

CI runs the tests on a Frappe v16 bench pinned in [.github/versions.env](.github/versions.env).

## Professional setup & support

Need help setting this up or integrating it into your Frappe stack? Write to oss@effizienzagent.de (English or German). German-speaking SMEs: see [mittelstandshub.de](https://mittelstandshub.de).

## License

MIT, see [license.txt](license.txt).
