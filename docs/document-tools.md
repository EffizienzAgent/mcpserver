# Document tools

`mcpserver` ships five read-only tools that work on any doctype a System Manager allows:

| Tool | Purpose |
| --- | --- |
| `list_doctypes(query?)` | Allowed doctypes the user may read; `query` searches their names. |
| `get_doctype(doctype)` | Readable fields (name, label, type, options, required), child tables, title and search fields. |
| `list_documents(doctype, fields?, filters?, order_by?, limit?, start?)` | Documents via `frappe.get_list`, newest first by default. |
| `search_documents(doctype, query, limit?)` | Documents whose name, title field or search fields contain `query`. |
| `get_document(doctype, name)` | One document with its child tables. |

## Turning them on

Open **MCP Settings**, check **Enable Document Tools** and add the doctypes to **Allowed Document Types**.
Without at least one allowed doctype, the tools are not listed at all.

Not allowed: child tables, single and virtual doctypes, and the OAuth doctypes that hold credentials
(OAuth Client, OAuth Bearer Token, OAuth Authorization Code).

## Permissions

- A doctype outside the list is refused for everyone, including Administrator.
- The user needs read permission on the doctype. Lists use `frappe.get_list`, so user permissions,
  `if_owner` rules and `permission_query_conditions` apply. `get_document` checks the document itself.
- Fields above the user's permission levels are not returned and cannot be used in `fields`, `filters` or
  `order_by`. `get_document` applies Frappe's field-level read permissions and masking.
- Password fields are never returned, also not to Administrator.

## Filters

`filters` maps a field to a value or to `[operator, value]`, combined with AND:

```json
{"status": "Open", "modified": [">", "2026-01-01"], "priority": ["in", ["High", "Medium"]]}
```

Operators: `=`, `!=`, `>`, `<`, `>=`, `<=`, `like`, `not like`, `in`, `not in`, `is` (`set` or
`not set`) and `between`. Values must be strings, numbers, booleans or null; `in`, `not in` and `between`
take a list. `order_by` is one readable field, optionally followed by `asc` or `desc`. `limit` is at most
100.
