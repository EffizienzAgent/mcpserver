# Adding tools from your app

Apps register tools in their `hooks.py`. The server builds the tool list from the hooks of the apps
installed on the site, on every request.

```python
mcp_tools = [
	"myapp.mcp_tools",  # every @tool function defined in this module
	"myapp.api.get_status",  # one @tool function
	{  # a plain function with a JSON schema
		"name": "ping_device",
		"title": "Ping device",
		"description": "Checks whether a device answers.",
		"method": "myapp.devices.ping",
		"input_schema": {
			"type": "object",
			"properties": {"device": {"type": "string", "description": "Device ID."}},
			"required": ["device"],
		},
		"read_only": True,
		"available": "myapp.devices.agents_enabled",  # optional, see `available` below
	},
]
```

Later entries replace earlier tools with the same name, so an app can override a tool of another app. An
entry that cannot be loaded is logged to the Error Log and skipped; the other tools keep working.

## The `@tool` decorator

```python
import frappe
from mcpserver.tools import integer, limit_of, readable_list, string, tool, with_urls


@tool(
	"find_projects",
	"Find projects",
	"Projects whose title contains `query`. Read-only.",
	required=("query",),
	query=string("Part of the project title."),
	status=string("Only this status.", enum=("Open", "Completed")),
	limit=integer("Maximum number of results (default 20, max 100)."),
)
def find_projects(query, status=None, limit=None):
	filters = {"project_name": ["like", f"%{query}%"], **({"status": status} if status else {})}
	rows = readable_list("Project", filters=filters, fields=["name", "project_name"], limit=limit_of(limit))
	return with_urls("Project", rows)
```

Arguments:

- `name`, `title`, `description` (positional): the tool's name, a short title and what it does. The
  description is what the model reads; say what the tool returns and whether it writes.
- `read_only` (default `True`): sets the `readOnlyHint` annotation. Set it to `False` for tools that write.
- `required`: names of required arguments. Missing or empty values are refused.
- `available`: a function without arguments. When it returns false, the tool is neither listed nor
  callable, for example to offer a tool only to some roles or when a setting is on.
- Every other keyword is an argument with its JSON schema.

Schema helpers in `mcpserver.tools`: `string(description, enum=None)`, `integer(description, minimum=1,
maximum=100)`, `boolean(description)`, `string_list(description, enum=None)` and `obj(description)`.

Before a tool runs, the server checks the arguments against the schema and refuses, as JSON-RPC errors
(`-32602`):

- unknown arguments and missing or empty required ones;
- wrong JSON types (`string`, `integer`, `number`, `boolean`, `array`, `object`; booleans never count as
  numbers), checked for array items and nested object properties too;
- values outside an `enum`, numbers outside `minimum` / `maximum`, and strings longer than `maxLength`
  (10,000 characters when not set);
- for nested objects: missing `required` properties and, with `additionalProperties: false`, unknown ones.

Other schema keywords are only hints for the client.

## Writing tools safely

- Tools run as the session user. Use `frappe.get_list`, `frappe.has_permission`, `doc.check_permission`
  or `readable_list` and never `ignore_permissions` for data the user asked for.
- The return value is sent as compact JSON text. Return plain data; `with_urls` adds desk links.
- Raise `frappe.ValidationError` (for example with `frappe.throw`) or `frappe.PermissionError` for errors
  the model should see. Any other exception is logged to the Error Log, and the client gets a generic
  message.
- Each call runs in a savepoint. If the tool raises, its writes and the commit callbacks it registered are
  rolled back.

## Other hooks

```python
mcp_server_info = {"name": "myapp", "title": "My App", "version": "1.0.0"}
mcp_instructions = ["myapp.mcp_tools.INSTRUCTIONS"]
mcp_call_context = ["myapp.mcp_tools.call_context"]
mcp_allowed_roles = ["Agent User"]
```

- `mcp_server_info`: `serverInfo` in the `initialize` response. The last app that sets a key wins. The
  defaults are `mcpserver`, `MCP server` and the app version.
- `mcp_instructions`: dotted paths to strings or functions returning strings. They are joined and sent
  to clients on `initialize`.
- `mcp_allowed_roles`: roles that may authorize clients and call the endpoint. Without the hook, every
  System User (role **Desk User**) may.
- `mcp_call_context`: dotted paths to context manager factories. Each tool call runs inside all of them,
  for example to set a flag that suppresses outgoing sync while an agent writes.
