MCP server with OAuth for the Frappe Framework

# mcpserver

`mcpserver` turns a Frappe site into a remote [MCP](https://modelcontextprotocol.io) server that Claude and
other AI clients can sign in to with OAuth. Every tool call runs as the signed-in user, with that user's
permissions.

- **One endpoint.** Streamable HTTP (protocol 2025-06-18, 2025-03-26 and 2024-11-05) with plain JSON
  responses at `/api/method/mcpserver.mcp.endpoint`.
- **OAuth sign-in for MCP clients.** Uses Frappe's OAuth 2 provider with discovery metadata and dynamic
  client registration, and makes it stricter: public clients only, loopback or allowlisted redirect URIs,
  PKCE S256, plain-text client names, refresh token checks, a registration rate limit and a daily cleanup
  of stale clients and tokens.
- **Tool registry for apps.** Any installed app can add its own tools through the `mcp_tools` hook.
- **Optional document tools.** Read-only tools to list, search and read documents of the doctypes a System
  Manager allows in **MCP Settings**. They use `frappe.get_list`, `has_permission` and field-level read
  permissions; password fields are never returned.
- **Safe tool calls.** Arguments are checked against the tool's schema. A failed call rolls back everything
  it wrote, including commit callbacks, and unexpected errors never leak tracebacks.

## Install

```sh
bench get-app https://github.com/EffizienzAgent/mcpserver
bench --site <site> install-app mcpserver
```

Requires Frappe v16. Installing turns on Frappe's OAuth discovery metadata and dynamic client registration
once. After that, the app leaves **OAuth Settings** alone.

## Connect a client

```sh
claude mcp add --scope user --transport http mysite https://<site>/api/method/mcpserver.mcp.endpoint
```

Claude Code opens the browser on first use. Sign in to the site and click **Allow**. For claude.ai, Claude
Desktop, other MCP clients and API keys, see [docs/clients.md](docs/clients.md).

## Documentation

- [docs/clients.md](docs/clients.md): connecting Claude and other MCP clients, revoking access.
- [docs/tools.md](docs/tools.md): adding tools from your app, server info and instructions hooks.
- [docs/document-tools.md](docs/document-tools.md): the built-in document tools and MCP Settings.
- [docs/oauth.md](docs/oauth.md): how sign-in works and what the app enforces.

## Frappe's built-in MCP endpoint

Recent Frappe v16 releases also ship an MCP endpoint at `/api/v2/mcp` with read and write tools for any
doctype. It is separate from this app and is controlled by **System Settings > AI Agents > Enable MCP**. Use this app
when you want OAuth sign-in for Claude, tools written by your apps and an admin-controlled list of readable
doctypes. Turn the built-in endpoint off if you do not need it.

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
