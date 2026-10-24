import frappe

OAUTH_SETTINGS = {
	"show_auth_server_metadata": 1,
	"show_protected_resource_metadata": 1,
	"enable_dynamic_client_registration": 1,
	"skip_authorization": 0,
}


def apply_oauth_settings():
	stored = set(frappe.db.sql_list("select field from tabSingles where doctype = 'OAuth Settings'"))
	missing = {field: value for field, value in OAUTH_SETTINGS.items() if field not in stored}
	if missing:
		frappe.db.set_single_value("OAuth Settings", missing)
