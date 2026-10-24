import base64
import hashlib
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import frappe
from frappe.app import process_response
from frappe.auth import validate_auth
from frappe.integrations.oauth2 import handle_wellknown
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from mcpserver import mcp, oauth
from mcpserver.setup import apply_oauth_settings
from mcpserver.tests.helpers import (
	BASE,
	MCP_PATH,
	hooks,
	make_role,
	make_user,
	parse,
	restore_request_state,
	set_request,
	unique,
)

TOKEN_PATH = "/api/method/frappe.integrations.oauth2.get_token"
REGISTER_PATH = "/api/method/frappe.integrations.oauth2.register_client"
CLAUDE_CODE = {
	"client_name": "Claude Code (app)",
	"redirect_uris": ["http://localhost:53682/callback"],
	"grant_types": ["authorization_code", "refresh_token"],
	"response_types": ["code"],
	"token_endpoint_auth_method": "none",
}
APPROVE_PATH = "/api/method/frappe.integrations.oauth2.approve"
AUTHORIZE_PATH = "/api/method/frappe.integrations.oauth2.authorize"


class OAuthTestCase(IntegrationTestCase):
	def setUp(self):
		restore_request_state(self)
		apply_oauth_settings()


class TestMetadata(OAuthTestCase):
	def test_protected_resource_metadata_points_to_this_server(self):
		for path in (
			"/.well-known/oauth-protected-resource",
			f"/.well-known/oauth-protected-resource{MCP_PATH}",
		):
			set_request("GET", path)
			metadata = parse(handle_wellknown(path))
			self.assertEqual(metadata["resource"], BASE)
			self.assertEqual(metadata["authorization_servers"], [BASE])

	def test_authorization_server_metadata(self):
		set_request("GET", "/.well-known/oauth-authorization-server")
		metadata = parse(handle_wellknown("/.well-known/oauth-authorization-server"))
		self.assertEqual(metadata["issuer"], BASE)
		self.assertEqual(metadata["code_challenge_methods_supported"], ["S256"])
		self.assertEqual(metadata["registration_endpoint"], f"{BASE}{REGISTER_PATH}")
		self.assertIn("none", metadata["token_endpoint_auth_methods_supported"])
		self.assertIn("refresh_token", metadata["grant_types_supported"])

	def test_urls_follow_the_request_host(self):
		host = "https://mcp.internal.example.test"
		frappe.set_user("Guest")
		set_request("POST", MCP_PATH, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, base_url=host)
		response = mcp.endpoint()
		process_response(response)
		prm_url = f"{host}/.well-known/oauth-protected-resource"
		self.assertEqual(response.headers["WWW-Authenticate"], f'Bearer resource_metadata="{prm_url}"')
		set_request("GET", "/.well-known/oauth-protected-resource", base_url=host)
		resource = parse(handle_wellknown("/.well-known/oauth-protected-resource"))
		self.assertEqual((resource["resource"], resource["authorization_servers"]), (host, [host]))
		set_request("GET", "/.well-known/oauth-authorization-server", base_url=host)
		server = parse(handle_wellknown("/.well-known/oauth-authorization-server"))
		self.assertEqual(server["issuer"], host)
		for key in (
			"authorization_endpoint",
			"token_endpoint",
			"registration_endpoint",
			"revocation_endpoint",
		):
			self.assertTrue(server[key].startswith(f"{host}/api/method/"), key)

	def test_guest_gets_resource_metadata_challenge(self):
		frappe.set_user("Guest")
		set_request("POST", MCP_PATH, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
		response = mcp.endpoint()
		process_response(response)
		self.assertEqual(response.status_code, 401)
		self.assertEqual(
			response.headers["WWW-Authenticate"],
			f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource"',
		)


def register(**overrides):
	frappe.set_user("Guest")
	set_request("POST", REGISTER_PATH, {**CLAUDE_CODE, **overrides})
	try:
		return oauth.register_client()
	finally:
		frappe.set_user("Administrator")


def backdate(doctype, name, **days):
	frappe.db.set_value(
		doctype, name, "creation", add_to_date(now_datetime(), days=-days["days"]), update_modified=False
	)


class TestSettings(OAuthTestCase):
	def stored(self, field):
		return frappe.db.sql(
			"select value from tabSingles where doctype = 'OAuth Settings' and field = %s", field
		)

	def test_missing_settings_are_filled_and_stored_ones_kept(self):
		self.addCleanup(
			frappe.db.set_single_value,
			"OAuth Settings",
			{
				"enable_dynamic_client_registration": 1,
				"skip_authorization": 0,
				"show_auth_server_metadata": 1,
			},
		)
		frappe.db.set_single_value(
			"OAuth Settings", {"enable_dynamic_client_registration": 0, "skip_authorization": 1}
		)
		frappe.db.sql(
			"delete from tabSingles where doctype = 'OAuth Settings' and field = 'show_auth_server_metadata'"
		)
		apply_oauth_settings()
		self.assertEqual(self.stored("show_auth_server_metadata"), (("1",),))
		self.assertEqual(self.stored("enable_dynamic_client_registration"), (("0",),))
		self.assertEqual(self.stored("skip_authorization"), (("1",),))

	def test_frappe_oauth_endpoints_are_overridden(self):
		for name in ("authorize", "approve", "get_token", "register_client"):
			method = frappe.override_whitelisted_method(f"frappe.integrations.oauth2.{name}")
			self.assertEqual(method, f"mcpserver.oauth.{name}")


class TestRegistration(OAuthTestCase):
	def register(self, **overrides):
		return register(**overrides)

	def test_registers_public_loopback_client_without_access(self):
		response = self.register()
		self.assertEqual(response.status_code, 201)
		body = parse(response)
		self.assertNotIn("client_secret", body)
		self.assertEqual(body["redirect_uris"], CLAUDE_CODE["redirect_uris"])
		self.assertEqual(body["token_endpoint_auth_method"], "none")
		client = frappe.get_doc("OAuth Client", body["client_id"])
		self.assertTrue(client.is_public_client())
		self.assertTrue(oauth.is_dynamic_client(client.name))
		self.assertFalse(client.skip_authorization)
		self.assertEqual(client.response_type, "Code")
		self.assertFalse(frappe.db.exists("OAuth Bearer Token", {"client": client.name}))
		self.assertFalse(frappe.db.exists("OAuth Authorization Code", {"client": client.name}))

	def test_client_name_must_be_plain_text(self):
		for name in (
			"<img src=x onerror=alert(1)>",
			'Claude "Code"',
			"Claude's",
			"A & B",
			"back`tick",
			"line\nbreak",
			"tab\there",
			"nul\x00",
			" ",
			"x" * 121,
		):
			response = self.register(client_name=name)
			self.assertEqual(response.status_code, 400, name)
			self.assertEqual(parse(response)["error"], "invalid_client_metadata")
			self.assertFalse(frappe.db.exists("OAuth Client", {"app_name": name}))
		for name in ("Claude Code (app_2) v1.0-beta", "Agent: Jürgen/Übersicht+1", "x" * 120):
			self.assertEqual(self.register(client_name=name).status_code, 201, name)

	def test_scope_must_be_plain_tokens(self):
		for scope in ("<img src=x onerror=alert(1)>", "all\nopenid", "a&b"):
			self.assertEqual(self.register(scope=scope).status_code, 400, scope)
		body = parse(self.register(scope="all openid"))
		self.assertEqual(body["scope"], "all openid")

	def test_consent_page_shows_the_name_as_text(self):
		client_id = parse(self.register(client_name="Claude Code (app)"))["client_id"]
		app_name = frappe.db.get_value("OAuth Client", client_id, "app_name")
		page = frappe.render_template(
			"templates/includes/oauth_confirmation.html",
			{"client_id": app_name, "details": ["all"], "success_url": "/", "failure_url": "/"},
		)
		self.assertIn("(unverified) Claude Code (app) wants to access", page)
		self.assertNotIn("<img", page)

	def test_accepts_loopback_and_allowlisted_callbacks(self):
		for uri in (
			"http://127.0.0.1:1234/callback",
			"http://[::1]:1234/callback",
			"http://localhost:1234",
			"http://localhost:1234/deep/path?x=1",
			"https://claude.ai/api/mcp/auth_callback",
			"https://claude.com/api/mcp/auth_callback",
		):
			response = self.register(redirect_uris=[uri])
			self.assertEqual(response.status_code, 201, uri)
			body = parse(response)
			self.assertEqual(body["redirect_uris"], [uri])
			stored = frappe.db.get_value(
				"OAuth Client", body["client_id"], ["redirect_uris", "default_redirect_uri"]
			)
			self.assertEqual(stored, (uri, uri))

	def test_rejects_unsafe_redirect_uris(self):
		for uri in (
			"https://evil.example/callback",
			"https://claude.ai/other",
			"https://claude.ai/api/mcp/auth_callback/",
			"https://claude.ai/api/mcp/auth_callback?x=1",
			"https://CLAUDE.ai/api/mcp/auth_callback",
			"http://claude.ai/api/mcp/auth_callback",
			"http://app.example.test/callback",
			"http://localhost.evil.example/callback",
			"http://user@localhost:1234/callback",
			"http://localhost:1234/callback#frag",
			"http://localhost:1234/call back",
			"javascript:alert(1)",
			"custom-app://callback",
		):
			response = self.register(redirect_uris=[uri])
			self.assertEqual(response.status_code, 400, uri)
		self.assertEqual(self.register(redirect_uris=[]).status_code, 400)
		self.assertEqual(self.register(redirect_uris=[123]).status_code, 400)

	def test_only_one_redirect_uri(self):
		uris = ["http://localhost:1/callback", "http://localhost:2/callback"]
		self.assertEqual(self.register(redirect_uris=uris).status_code, 400)

	def test_https_callbacks_are_configurable(self):
		uri = "https://agent.example.test/callback"
		self.assertEqual(self.register(redirect_uris=[uri]).status_code, 400)
		with patch.dict(frappe.conf, {"mcp_oauth_redirect_uris": [uri]}):
			self.assertEqual(self.register(redirect_uris=[uri]).status_code, 201)
			claude = "https://claude.ai/api/mcp/auth_callback"
			self.assertEqual(self.register(redirect_uris=[claude]).status_code, 400)
		for invalid in (uri, {uri: 1}, [uri, 1]):
			with patch.dict(frappe.conf, {"mcp_oauth_redirect_uris": invalid}):
				self.assertEqual(self.register(redirect_uris=[uri]).status_code, 400, invalid)
				self.assertEqual(oauth.https_redirect_uris(), ())

	def test_only_public_clients(self):
		for method in ("client_secret_basic", "client_secret_post", "private_key_jwt", None):
			response = self.register(token_endpoint_auth_method=method)
			self.assertEqual(response.status_code, 400, method)
			self.assertEqual(parse(response)["error"], "invalid_client_metadata")
		body = CLAUDE_CODE.copy()
		del body["token_endpoint_auth_method"]
		frappe.set_user("Guest")
		set_request("POST", REGISTER_PATH, body)
		self.assertEqual(oauth.register_client().status_code, 400)

	def test_rejects_unsupported_grants_and_bodies(self):
		self.assertEqual(self.register(grant_types=["client_credentials"]).status_code, 400)
		self.assertEqual(self.register(response_types=["token"]).status_code, 400)
		set_request("POST", REGISTER_PATH, ["not", "an", "object"])
		self.assertEqual(oauth.register_client().status_code, 400)

	def test_registration_is_rate_limited(self):
		for _ in range(oauth.REGISTRATION_LIMIT):
			self.assertEqual(self.register().status_code, 201)
		with self.assertRaises(frappe.RateLimitExceededError):
			self.register()

	def used(self, client_id):
		frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": client_id,
				"user": "Administrator",
				"scopes": "all",
				"access_token": unique(),
				"refresh_token": unique(),
				"expires_in": 3600,
			}
		).insert(ignore_permissions=True)

	def test_full_cap_evicts_the_oldest_unused_client(self):
		for name in frappe.get_all("OAuth Client", {"software_id": oauth.DYNAMIC_CLIENT_ID}, pluck="name"):
			self.used(name)
		count = frappe.db.count("OAuth Client", {"software_id": oauth.DYNAMIC_CLIENT_ID})
		with patch.object(oauth, "MAX_DYNAMIC_CLIENTS", count + 2):
			first = parse(self.register())["client_id"]
			frappe.db.set_value("OAuth Client", first, "creation", add_to_date(now_datetime(), minutes=-5))
			second = parse(self.register())["client_id"]
			third = self.register()
		self.assertEqual(third.status_code, 201)
		self.assertFalse(frappe.db.exists("OAuth Client", first))
		self.assertTrue(frappe.db.exists("OAuth Client", second))

	def test_full_cap_of_used_clients_refuses_registration(self):
		for name in frappe.get_all("OAuth Client", {"software_id": oauth.DYNAMIC_CLIENT_ID}, pluck="name"):
			self.used(name)
		count = frappe.db.count("OAuth Client", {"software_id": oauth.DYNAMIC_CLIENT_ID})
		with patch.object(oauth, "MAX_DYNAMIC_CLIENTS", count):
			response = self.register()
		self.assertEqual(response.status_code, 429)
		self.assertEqual(parse(response)["error"], "temporarily_unavailable")

	def test_registered_clients_are_marked_unverified_and_role_gated(self):
		body = parse(self.register(client_name="Claude Code (app)"))
		self.assertEqual(body["client_name"], "(unverified) Claude Code (app)")
		client = frappe.get_doc("OAuth Client", body["client_id"])
		self.assertEqual([row.role for row in client.allowed_roles], ["Desk User"])
		make_role("MCP Agent")
		with hooks(mcp_allowed_roles=["MCP Agent"]):
			client = frappe.get_doc("OAuth Client", parse(self.register())["client_id"])
		self.assertEqual([row.role for row in client.allowed_roles], ["MCP Agent"])

	def test_disabled_registration(self):
		self.addCleanup(frappe.db.set_single_value, "OAuth Settings", "enable_dynamic_client_registration", 1)
		frappe.db.set_single_value("OAuth Settings", "enable_dynamic_client_registration", 0)
		self.assertEqual(self.register().status_code, 404)


class TestAuthorize(OAuthTestCase):
	def setUp(self):
		super().setUp()
		self.user = make_user("mcp-oauth@example.test")
		self.client_id = parse(register())["client_id"]
		self.redirect_uri = CLAUDE_CODE["redirect_uris"][0]

	def params(self, **extra):
		return {
			"response_type": "code",
			"client_id": self.client_id,
			"redirect_uri": self.redirect_uri,
			"state": "st4te",
			**extra,
		}

	def authorize(self, handler=oauth.authorize, **extra):
		frappe.set_user(self.user)
		set_request("GET", AUTHORIZE_PATH, query=self.params(**extra))
		frappe.local.response = frappe._dict()
		return handler(**frappe.form_dict)

	def test_registered_client_reaches_the_consent_page(self):
		self.authorize(code_challenge="abc", code_challenge_method="S256")
		self.assertNotIn("error", frappe.local.response)
		self.assertEqual(frappe.local.response.get("type"), "page")

	def test_missing_or_plain_pkce_redirects_with_error(self):
		for handler in (oauth.authorize, oauth.approve):
			for extra in (
				{},
				{"code_challenge": "abc", "code_challenge_method": "plain"},
				{"code_challenge": "abc"},
			):
				with (
					patch("frappe.integrations.oauth2.authorize") as frappe_authorize,
					patch("frappe.integrations.oauth2.approve") as frappe_approve,
				):
					response = self.authorize(handler, **extra)
				self.assertEqual(response.status_code, 302, extra)
				location = urlparse(response.headers["Location"])
				self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", self.redirect_uri)
				query = parse_qs(location.query)
				self.assertEqual(query["error"], ["invalid_request"])
				self.assertEqual(query["state"], ["st4te"])
				frappe_authorize.assert_not_called()
				frappe_approve.assert_not_called()

	def test_stored_uri_without_path_matches_exactly(self):
		self.redirect_uri = "http://localhost:1234"
		self.client_id = parse(register(redirect_uris=[self.redirect_uri]))["client_id"]
		self.authorize(code_challenge="abc", code_challenge_method="S256")
		self.assertNotIn("error", frappe.local.response)
		self.assertEqual(frappe.local.response.get("type"), "page")
		self.redirect_uri = "http://localhost:1234/"
		self.authorize(code_challenge="abc", code_challenge_method="S256")
		self.assertEqual(frappe.local.response.get("error"), "invalid_request")

	def approve_code(self, verifier):
		challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
		frappe.set_user(self.user)
		query = self.params(code_challenge=challenge, code_challenge_method="S256", scope="all")
		set_request("POST", APPROVE_PATH, query=query)
		frappe.local.response = frappe._dict()
		self.assertIsNone(oauth.approve(**frappe.form_dict))
		location = urlparse(frappe.local.response["location"])
		query = parse_qs(location.query)
		self.assertEqual(query["state"], ["st4te"])
		return query["code"][0]

	def exchange(self, code, verifier):
		frappe.flags.oauth_credentials = None
		frappe.set_user("Guest")
		form = {
			"grant_type": "authorization_code",
			"code": code,
			"redirect_uri": self.redirect_uri,
			"client_id": self.client_id,
			"code_verifier": verifier,
		}
		set_request("POST", TOKEN_PATH, form, content_type="application/x-www-form-urlencoded")
		frappe.local.response = frappe._dict()
		with patch("frappe.db.commit"):
			response = oauth.get_token()
		return response, frappe.local.response

	def test_pkce_code_flow_issues_a_token_for_the_user(self):
		verifier = unique() + unique()
		code = self.approve_code(verifier)
		response, body = self.exchange(code, verifier)
		self.assertIsNone(response)
		self.assertTrue(body.get("access_token"), body)
		token = frappe.get_doc("OAuth Bearer Token", body["access_token"])
		self.assertEqual((token.user, token.client, token.scopes), (self.user, self.client_id, "all"))
		_response, replayed = self.exchange(code, verifier)
		self.assertEqual(replayed.get("error"), "invalid_grant")

	def test_pkce_code_needs_the_matching_verifier(self):
		code = self.approve_code(unique() + unique())
		_response, body = self.exchange(code, unique() + unique())
		self.assertEqual(body.get("error"), "invalid_grant")
		self.assertFalse(body.get("access_token"))

	def test_untrusted_redirect_uri_gets_json_error(self):
		self.redirect_uri = "http://localhost:9/evil"
		response = self.authorize()
		self.assertEqual(response.status_code, 400)
		self.assertEqual(parse(response)["error"], "invalid_request")

	def test_other_clients_are_left_to_frappe(self):
		client = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "Manual client",
				"scopes": "all",
				"redirect_uris": self.redirect_uri,
				"default_redirect_uri": self.redirect_uri,
			}
		).insert(ignore_permissions=True)
		self.client_id = client.name
		with patch("frappe.integrations.oauth2.authorize", return_value="frappe") as frappe_authorize:
			self.assertEqual(self.authorize(), "frappe")
		frappe_authorize.assert_called_once()


class TestBearerTokens(OAuthTestCase):
	def setUp(self):
		super().setUp()
		self.user = make_user("mcp-oauth@example.test")
		self.other = make_user("mcp-oauth-other@example.test")
		self.client = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "Claude Code test",
				"scopes": "all",
				"redirect_uris": "http://localhost:1/callback",
				"default_redirect_uri": "http://localhost:1/callback",
				"token_endpoint_auth_method": "None",
			}
		).insert(ignore_permissions=True)

	def make_token(self, user=None, **fields):
		return frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": self.client.name,
				"user": user or self.user,
				"scopes": "all",
				"access_token": unique(),
				"refresh_token": unique(),
				"expires_in": 3600,
				**fields,
			}
		).insert(ignore_permissions=True)

	def rpc(self, access_token, method, params=None):
		body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
		headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
		frappe.set_user("Guest")
		set_request("POST", MCP_PATH, body, headers)
		validate_auth()
		return mcp.endpoint()

	def whoami(self, access_token):
		params = {"name": "whoami", "arguments": {}}
		with hooks(mcp_tools=["mcpserver.tests.sample_tools"]):
			response = self.rpc(access_token, "tools/call", params)
		self.assertEqual(response.status_code, 200)
		result = parse(response)["result"]
		self.assertFalse(result["isError"], result)
		return json.loads(result["content"][0]["text"])["user"]

	def test_tools_run_as_the_token_user(self):
		self.assertEqual(self.whoami(self.make_token().access_token), self.user)
		self.assertEqual(self.whoami(self.make_token(user=self.other).access_token), self.other)

	def test_revoked_and_expired_tokens_are_rejected(self):
		revoked = self.make_token()
		revoked.db_set("status", "Revoked")
		expired = self.make_token(expiration_time=add_to_date(now_datetime(), seconds=-1))
		for token in (revoked, expired):
			with self.assertRaises(frappe.AuthenticationError):
				self.rpc(token.access_token, "ping")
		with self.assertRaises(frappe.AuthenticationError):
			self.rpc(unique(), "ping")

	def token_request(self, **form):
		frappe.set_user("Guest")
		set_request("POST", TOKEN_PATH, form, content_type="application/x-www-form-urlencoded")
		frappe.local.response = frappe._dict()
		with patch("frappe.db.commit"):
			return oauth.get_token()

	def refresh(self, refresh_token, client_id=None):
		return self.token_request(
			grant_type="refresh_token", refresh_token=refresh_token, client_id=client_id or self.client.name
		)

	def assert_oauth_error(self, response, status, error):
		self.assertEqual(response.status_code, status)
		self.assertEqual(parse(response)["error"], error)

	def test_unknown_or_revoked_refresh_token_is_invalid_grant(self):
		revoked = self.make_token()
		revoked.db_set("status", "Revoked")
		for refresh_token in (unique(), revoked.refresh_token):
			self.assert_oauth_error(self.refresh(refresh_token), 400, "invalid_grant")

	def test_manual_clients_keep_frappes_refresh_behaviour(self):
		old = self.make_token()
		self.assertIsNone(self.refresh(old.refresh_token))
		self.assertTrue(frappe.local.response.get("access_token"))
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", old.name, "status"), "Active")

	def test_refresh_rotates_and_revokes_the_presented_token(self):
		self.client.db_set("software_id", oauth.DYNAMIC_CLIENT_ID)
		old = self.make_token()
		backdate("OAuth Bearer Token", old.name, days=10)
		old_creation = frappe.db.get_value("OAuth Bearer Token", old.name, "creation")
		self.assertIsNone(self.refresh(old.refresh_token))
		issued = frappe._dict(frappe.local.response)
		self.assertNotEqual(issued.refresh_token, old.refresh_token)
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", old.name, "status"), "Revoked")
		new = frappe.get_doc("OAuth Bearer Token", issued.access_token)
		self.assertEqual((new.status, new.user), ("Active", self.user))
		self.assertEqual(new.creation, old_creation)
		with self.assertRaises(frappe.AuthenticationError):
			self.rpc(old.access_token, "ping")
		self.assertIsNone(self.refresh(issued.refresh_token))

	def test_grant_lifetime_does_not_slide_with_refreshes(self):
		self.client.db_set("software_id", oauth.DYNAMIC_CLIENT_ID)
		token = self.make_token()
		backdate("OAuth Bearer Token", token.name, days=29)
		self.assertIsNone(self.refresh(token.refresh_token))
		issued = frappe.local.response["access_token"]
		backdate("OAuth Bearer Token", issued, days=31)
		refresh_token = frappe.db.get_value("OAuth Bearer Token", issued, "refresh_token")
		self.assert_oauth_error(self.refresh(refresh_token), 400, "invalid_grant")

	def test_reuse_within_the_grace_window_keeps_the_grant(self):
		self.client.db_set("software_id", oauth.DYNAMIC_CLIENT_ID)
		token = self.make_token()
		self.assertIsNone(self.refresh(token.refresh_token))
		rotated = frappe.local.response["access_token"]
		self.assert_oauth_error(self.refresh(token.refresh_token), 400, "invalid_grant")
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", rotated, "status"), "Active")

	def test_reused_refresh_token_revokes_the_grant(self):
		self.client.db_set("software_id", oauth.DYNAMIC_CLIENT_ID)
		stolen = self.make_token()
		other_user = self.make_token(user=self.other)
		self.assertIsNone(self.refresh(stolen.refresh_token))
		rotated = frappe.local.response["access_token"]
		revoked_at = add_to_date(now_datetime(), seconds=-(oauth.ROTATION_GRACE_SECONDS + 5))
		frappe.db.set_value("OAuth Bearer Token", stolen.name, "modified", revoked_at, update_modified=False)
		self.assert_oauth_error(self.refresh(stolen.refresh_token), 400, "invalid_grant")
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", rotated, "status"), "Revoked")
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", other_user.name, "status"), "Active")

	def test_overlapping_refreshes_issue_only_one_token(self):
		self.client.db_set("software_id", oauth.DYNAMIC_CLIENT_ID)
		token = self.make_token()
		real = oauth.oauth2.get_token

		def concurrent_winner(*args, **kwargs):
			result = real(*args, **kwargs)
			frappe.db.set_value("OAuth Bearer Token", token.name, "status", "Revoked")
			return result

		with patch.object(oauth.oauth2, "get_token", side_effect=concurrent_winner):
			response = self.refresh(token.refresh_token)
		self.assert_oauth_error(response, 400, "invalid_grant")
		self.assertFalse(frappe.local.response.get("access_token"))
		self.assertFalse(
			frappe.db.exists("OAuth Bearer Token", {"client": self.client.name, "status": "Active"})
		)

	def test_refresh_locks_the_presented_token(self):
		token = self.make_token()
		with patch.object(frappe.db, "get_value", wraps=frappe.db.get_value) as get_value:
			oauth.refresh_token_row(token.refresh_token)
		self.assertTrue(get_value.call_args.kwargs["for_update"])

	def test_failed_refresh_revokes_nothing(self):
		self.client.db_set("software_id", oauth.DYNAMIC_CLIENT_ID)
		token = self.make_token()
		with patch(
			"frappe.integrations.oauth2.get_token",
			side_effect=lambda *a, **k: frappe.local.response.update(error="invalid_grant"),
		):
			self.refresh(token.refresh_token)
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", token.name, "status"), "Active")

	def test_dynamic_clients_must_send_client_id_on_refresh(self):
		self.client.db_set("software_id", oauth.DYNAMIC_CLIENT_ID)
		token = self.make_token()
		response = self.token_request(grant_type="refresh_token", refresh_token=token.refresh_token)
		self.assert_oauth_error(response, 400, "invalid_request")
		self.client.db_set("software_id", None)
		self.assertIsNone(self.token_request(grant_type="refresh_token", refresh_token=token.refresh_token))

	def test_refresh_token_must_belong_to_the_client(self):
		other = frappe.copy_doc(self.client).insert(ignore_permissions=True)
		token = self.make_token()
		self.assert_oauth_error(self.refresh(token.refresh_token, other.name), 400, "invalid_grant")
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", token.name, "status"), "Active")

	def test_refresh_tokens_expire_after_30_days(self):
		token = self.make_token()
		backdate("OAuth Bearer Token", token.name, days=31)
		self.assert_oauth_error(self.refresh(token.refresh_token), 400, "invalid_grant")
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", token.name, "status"), "Revoked")
		fresh = self.make_token()
		backdate("OAuth Bearer Token", fresh.name, days=29)
		self.assertIsNone(self.refresh(fresh.refresh_token))

	def test_unknown_client_is_invalid_client(self):
		token = self.make_token()
		self.assert_oauth_error(self.refresh(token.refresh_token, unique()), 401, "invalid_client")
		response = self.token_request(
			grant_type="authorization_code",
			code=unique(),
			client_id=unique(),
			redirect_uri="http://localhost:1/callback",
		)
		self.assert_oauth_error(response, 401, "invalid_client")


class TestCleanup(OAuthTestCase):
	def setUp(self):
		super().setUp()
		self.user = make_user("mcp-oauth@example.test")

	def client(self, days=0, hours=0, dynamic=True):
		name = parse(register())["client_id"]
		if not dynamic:
			frappe.db.set_value("OAuth Client", name, "software_id", None)
		created = add_to_date(now_datetime(), days=-days, hours=-hours)
		frappe.db.set_value("OAuth Client", name, "creation", created, update_modified=False)
		return name

	def token(self, client, days, status="Active", modified_days=0):
		doc = frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": client,
				"user": self.user,
				"scopes": "all",
				"access_token": unique(),
				"refresh_token": unique(),
				"expires_in": 3600,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"OAuth Bearer Token",
			doc.name,
			{
				"status": status,
				"creation": add_to_date(now_datetime(), days=-days),
				"modified": add_to_date(now_datetime(), days=-modified_days),
			},
			update_modified=False,
		)
		return doc.name

	def test_cleanup_removes_stale_tokens_and_unused_clients(self):
		active = self.client(days=60)
		live = self.token(active, days=1)
		recent_revoked = self.token(active, days=20, status="Revoked", modified_days=8)
		old_revoked = self.token(active, days=29, status="Revoked", modified_days=31)
		expired = self.token(active, days=31)
		unused_old = self.client(hours=2)
		unused_new = self.client(hours=0)
		manual = self.client(days=60, dynamic=False)
		manual_revoked = self.token(manual, days=40, status="Revoked", modified_days=40)

		oauth.cleanup_dynamic_clients()

		exists = lambda doctype, name: bool(frappe.db.exists(doctype, name))  # noqa: E731
		self.assertTrue(exists("OAuth Bearer Token", live))
		self.assertTrue(exists("OAuth Bearer Token", recent_revoked))
		self.assertFalse(exists("OAuth Bearer Token", old_revoked))
		self.assertFalse(exists("OAuth Bearer Token", expired))
		self.assertTrue(exists("OAuth Bearer Token", manual_revoked))
		self.assertTrue(exists("OAuth Client", active))
		self.assertFalse(exists("OAuth Client", unused_old))
		self.assertTrue(exists("OAuth Client", unused_new))
		self.assertTrue(exists("OAuth Client", manual))

	def test_cleanup_runs_hourly(self):
		self.assertIn(
			"mcpserver.oauth.cleanup_dynamic_clients", frappe.get_hooks("scheduler_events")["hourly"]
		)
