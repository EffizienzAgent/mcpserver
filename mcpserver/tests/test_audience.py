import frappe
from frappe.tests import IntegrationTestCase

from mcpserver import audience, oauth
from mcpserver.tests.helpers import hooks, make_user, restore_request_state, set_request, unique


class TestBearerAudience(IntegrationTestCase):
	def setUp(self):
		restore_request_state(self)
		self.user = make_user("mcp-audience@example.test")
		self.dynamic = self.token(oauth.DYNAMIC_CLIENT_ID)
		self.manual = self.token(None)

	def token(self, software_id):
		client = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "Audience test",
				"scopes": "all",
				"redirect_uris": "http://localhost:1/callback",
				"default_redirect_uri": "http://localhost:1/callback",
				"token_endpoint_auth_method": "None",
				"software_id": software_id,
			}
		).insert(ignore_permissions=True)
		return frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": client.name,
				"user": self.user,
				"scopes": "all",
				"access_token": unique(),
				"refresh_token": unique(),
				"expires_in": 3600,
			}
		).insert(ignore_permissions=True)

	def allowed(self, path, token=None, scheme="Bearer"):
		headers = {"Authorization": f"{scheme} {(token or self.dynamic).access_token}"}
		set_request("POST", path, {}, headers)
		try:
			audience.check_bearer_audience()
		except frappe.PermissionError:
			return False
		return True

	def test_mcp_tokens_only_reach_the_endpoint_and_oauth_methods(self):
		for path in (
			"/api/method/mcpserver.mcp.endpoint",
			"/api/v2/method/mcpserver.mcp.endpoint",
			"/api/method/frappe.integrations.oauth2.get_token",
			"/api/method/frappe.integrations.oauth2.revoke_token",
		):
			self.assertTrue(self.allowed(path), path)
		for path in (
			"/api/method/frappe.client.get_list",
			"/api/resource/ToDo",
			"/api/v2/document/ToDo",
			"/api/v2/mcp",
			"/api/method/mcpserver.mcp.endpoint.extra",
			"/private/files/report.pdf",
			"/app/todo",
			"/",
		):
			self.assertFalse(self.allowed(path), path)
		self.assertFalse(self.allowed("/api/method/frappe.client.get_list", scheme="bearer"))

	def test_cmd_rpc_cannot_bypass_the_path_check(self):
		token = self.dynamic.access_token
		headers = {"Authorization": f"Bearer {token}"}
		endpoint = "/api/method/mcpserver.mcp.endpoint"
		requests = (
			{"query": {"cmd": "frappe.client.get_list"}},
			{"body": {"cmd": "frappe.client.get_list"}, "content_type": "application/x-www-form-urlencoded"},
			{"body": {"cmd": "frappe.client.get_list"}},
			{"body": {"cmd": ["frappe.client.get_list"]}},
		)
		for request in requests:
			set_request(
				"POST",
				endpoint,
				request.get("body"),
				headers,
				query=request.get("query"),
				content_type=request.get("content_type", "application/json"),
			)
			self.assertTrue(frappe.form_dict.get("cmd"), request)
			with self.assertRaises(frappe.PermissionError, msg=request):
				audience.check_bearer_audience()
		set_request("POST", "/", {"cmd": "mcpserver.mcp.endpoint"}, headers)
		audience.check_bearer_audience()
		set_request(
			"POST",
			endpoint,
			{"cmd": "frappe.client.get_list"},
			{"Authorization": f"Bearer {self.manual.access_token}"},
		)
		audience.check_bearer_audience()

	def test_endpoint_aliases_and_hook_methods_are_allowed(self):
		alias = "/api/method/myapp.mcp.endpoint"
		self.assertFalse(self.allowed(alias))
		with hooks(override_whitelisted_methods={"myapp.mcp.endpoint": ["mcpserver.mcp.endpoint"]}):
			self.assertTrue(self.allowed(alias))
		with hooks(mcp_bearer_methods=["myapp.api.status"]):
			self.assertTrue(self.allowed("/api/method/myapp.api.status"))

	def test_other_requests_are_left_alone(self):
		self.assertTrue(self.allowed("/api/method/frappe.client.get_list", token=self.manual))
		self.assertTrue(self.allowed("/api/method/frappe.client.get_list", scheme="token"))
		set_request("GET", "/app/todo")
		audience.check_bearer_audience()
		set_request("GET", "/app/todo", headers={"Authorization": f"Bearer {unique()}"})
		audience.check_bearer_audience()

	def test_runs_before_every_request(self):
		self.assertIn("mcpserver.audience.check_bearer_audience", frappe.get_hooks("before_request"))
