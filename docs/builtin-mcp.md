# Frappe's built-in MCP server

Frappe v16 includes a generic MCP server at `/api/v2/mcp`. It is on by default and offers 14 tools:
schema and method discovery, list, get, count, create, update and delete for any doctype, and calls to
whitelisted methods and document methods. Every call uses the permissions of the authenticated user.

`mcpserver` is a separate endpoint at `/api/method/mcpserver.mcp.endpoint` with a different goal:

| | Built-in `/api/v2/mcp` | `mcpserver` |
| --- | --- | --- |
| Tools | Generic CRUD and method calls on everything the user may access | Only tools that installed apps register through `mcp_tools` |
| Writes | Create, update, delete and any whitelisted method | Only what a registered tool does |
| Sign-in | API key or bearer token | OAuth with dynamic client registration, PKCE and refresh checks (plus API keys) |
| Failed calls | Depends on the method | Rolled back, including commit callbacks |

Both can run on the same site. They do not share tools or settings.

## When to turn the built-in server off

Frappe permissions are written for people who click through the desk. A user who may edit a doctype can,
through the built-in server, let an agent change or delete any of those documents in bulk, or call any
whitelisted method the user may call. If agents should only use the curated tools you chose, turn the
built-in server off:

- uncheck **System Settings > AI Agents > Enable MCP**, or
- force it off for a site, regardless of the checkbox:

  ```sh
  bench --site <site> set-config disable_mcp_server 1
  ```

`mcpserver` does not change these settings. When it is installed while the built-in server is on, it
prints a hint. Apps that depend on `mcpserver` can turn the built-in server off in their own
`after_install` or `after_migrate` hook, for example with
`frappe.db.set_single_value("System Settings", "enable_mcp", 0)`.
