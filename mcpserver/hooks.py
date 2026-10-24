app_name = "mcpserver"
app_title = "MCP Server"
app_publisher = "EffizienzAgent"
app_description = "MCP server with OAuth for the Frappe Framework"
app_email = "oss@effizienzagent.de"
app_license = "mit"

after_install = "mcpserver.setup.after_install"
after_migrate = "mcpserver.setup.apply_oauth_settings"

before_request = ["mcpserver.audience.check_bearer_audience"]

override_whitelisted_methods = {
	"frappe.integrations.oauth2.authorize": "mcpserver.oauth.authorize",
	"frappe.integrations.oauth2.approve": "mcpserver.oauth.approve",
	"frappe.integrations.oauth2.get_token": "mcpserver.oauth.get_token",
	"frappe.integrations.oauth2.register_client": "mcpserver.oauth.register_client",
}

scheduler_events = {"daily": ["mcpserver.oauth.cleanup_dynamic_clients"]}
