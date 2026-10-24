import frappe

OAUTH_SETTINGS = {
	"show_auth_server_metadata": 1,
	"show_protected_resource_metadata": 1,
	"enable_dynamic_client_registration": 1,
	"skip_authorization": 0,
}
OAUTH_SETTINGS_MARKER = "mcpserver_oauth_settings_applied"


def apply_oauth_settings():
	if frappe.db.get_default(OAUTH_SETTINGS_MARKER) == "1":
		return
	frappe.db.set_single_value("OAuth Settings", OAUTH_SETTINGS)
	frappe.db.set_default(OAUTH_SETTINGS_MARKER, 1)
