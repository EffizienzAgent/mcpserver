import frappe

OAUTH_SETTINGS = {
	"show_auth_server_metadata": 1,
	"show_protected_resource_metadata": 1,
	"enable_dynamic_client_registration": 1,
	"skip_authorization": 0,
}
BUILTIN_MCP_HINT = (
	"Frappe's built-in MCP server at /api/v2/mcp is enabled. It lets agents create, update and delete "
	"any permitted document and call any whitelisted method. To limit agents to the curated tools of "
	"mcpserver, uncheck System Settings > AI Agents > Enable MCP or set disable_mcp_server in the site config."
)


def apply_oauth_settings():
	stored = set(frappe.db.sql_list("select field from tabSingles where doctype = 'OAuth Settings'"))
	missing = {field: value for field, value in OAUTH_SETTINGS.items() if field not in stored}
	if missing:
		frappe.db.set_single_value("OAuth Settings", missing)


def after_install():
	apply_oauth_settings()
	if builtin_mcp_enabled():
		print(BUILTIN_MCP_HINT)


def builtin_mcp_enabled() -> bool:
	return not frappe.conf.get("disable_mcp_server") and bool(frappe.get_system_settings("enable_mcp"))
