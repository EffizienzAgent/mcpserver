import frappe

from mcpserver.oauth import is_dynamic_client

ENDPOINT = "mcpserver.mcp.endpoint"
METHOD_PREFIXES = ("/api/method/", "/api/v1/method/", "/api/v2/method/")
OAUTH_METHODS = (
	"frappe.integrations.oauth2.get_token",
	"frappe.integrations.oauth2.revoke_token",
	"frappe.integrations.oauth2.introspect_token",
	"frappe.integrations.oauth2.openid_profile",
)


def check_bearer_audience():
	header = frappe.get_request_header("Authorization") or ""
	if header[:7].lower() != "bearer ":
		return
	client = frappe.db.get_value("OAuth Bearer Token", header[7:].strip(), "client")
	if not client or not is_dynamic_client(client):
		return
	if requested_method() not in allowed_methods():
		raise frappe.PermissionError("This access token is only valid for the MCP endpoint")


def requested_method():
	path = frappe.request.path.rstrip("/")
	for prefix in METHOD_PREFIXES:
		if path.startswith(prefix):
			return path[len(prefix) :]
	return None


def allowed_methods() -> set[str]:
	aliases = [
		method
		for method, targets in frappe.get_hooks("override_whitelisted_methods", {}).items()
		if targets and targets[-1] == ENDPOINT
	]
	return {ENDPOINT, *aliases, *OAUTH_METHODS, *frappe.get_hooks("mcp_bearer_methods")}
