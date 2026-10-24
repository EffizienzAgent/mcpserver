import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

import mcpserver
from mcpserver import mcp, setup, tools
from mcpserver.tests import sample_tools
from mcpserver.tests.helpers import ACCEPT, MCP_PATH, hooks, make_user, restore_request_state, unique

SAMPLE = "mcpserver.tests.sample_tools"


class McpTestCase(IntegrationTestCase):
	tool_hooks = (SAMPLE,)

	def setUp(self):
		restore_request_state(self)
		self.user = make_user("mcp-user@example.test")
		self.next_id = 0
		patcher = hooks(mcp_tools=list(self.tool_hooks))
		patcher.__enter__()
		self.addCleanup(patcher.__exit__, None, None, None)

	def post(self, body, user=None, headers=None, content_type="application/json"):
		builder = EnvironBuilder(
			method="POST",
			path=MCP_PATH,
			data=body if isinstance(body, str) else json.dumps(body),
			headers={"Accept": ACCEPT, "Content-Type": content_type, **(headers or {})},
		)
		return self.send(builder, user)

	def send(self, builder, user=None):
		from frappe.app import make_form_dict

		frappe.local.request = Request(builder.get_environ())
		make_form_dict(frappe.local.request)
		frappe.set_user(user or self.user)
		try:
			return mcp.endpoint()
		finally:
			frappe.set_user("Administrator")

	def rpc(self, method, params=None, user=None):
		self.next_id += 1
		response = self.post({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}, user)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.mimetype, "application/json")
		body = json.loads(response.get_data(as_text=True))
		self.assertEqual(body["id"], self.next_id)
		return body

	def call(self, name, /, as_user=None, **arguments):
		result = self.rpc("tools/call", {"name": name, "arguments": arguments}, as_user)["result"]
		self.assertEqual(result["content"][0]["type"], "text")
		text = result["content"][0]["text"]
		return result["isError"], (text if result["isError"] else json.loads(text))

	def ok(self, name, /, as_user=None, **arguments):
		is_error, value = self.call(name, as_user, **arguments)
		self.assertFalse(is_error, value)
		return value

	def error(self, name, /, as_user=None, **arguments):
		is_error, value = self.call(name, as_user, **arguments)
		self.assertTrue(is_error, value)
		return value


class TestProtocol(McpTestCase):
	def test_initialize_negotiates_version(self):
		result = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
		self.assertEqual(result["protocolVersion"], "2025-06-18")
		self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
		self.assertEqual(
			result["serverInfo"],
			{"name": "mcpserver", "title": "MCP server", "version": mcpserver.__version__},
		)
		older = self.rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
		self.assertEqual(older["protocolVersion"], "2025-03-26")
		unknown = self.rpc("initialize", {"protocolVersion": "2099-01-01"})["result"]
		self.assertEqual(unknown["protocolVersion"], "2025-06-18")

	def test_server_info_and_instructions_come_from_hooks(self):
		info = {"name": ["first", "app-server"], "title": ["App Server"]}
		instructions = [f"{SAMPLE}.INSTRUCTIONS", f"{SAMPLE}.more_instructions"]
		with hooks(mcp_server_info=info, mcp_instructions=instructions):
			result = self.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
		self.assertEqual(result["serverInfo"]["name"], "app-server")
		self.assertEqual(result["serverInfo"]["title"], "App Server")
		self.assertEqual(result["instructions"], "Sample instructions.\n\nMore instructions.")

	def test_ping(self):
		self.assertEqual(self.rpc("ping")["result"], {})

	def test_notifications_and_responses_are_accepted(self):
		for body in (
			{"jsonrpc": "2.0", "method": "notifications/initialized"},
			{"jsonrpc": "2.0", "id": 1, "result": {}},
		):
			response = self.post(body)
			self.assertEqual(response.status_code, 202)
			self.assertEqual(response.get_data(), b"")

	def test_get_and_delete_are_not_allowed(self):
		for method in ("GET", "DELETE"):
			builder = EnvironBuilder(method=method, path="/", headers={"Accept": "text/event-stream"})
			response = self.send(builder)
			self.assertEqual(response.status_code, 405)
			self.assertEqual(response.headers["Allow"], "POST")

	def test_guest_gets_401(self):
		response = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, user="Guest")
		self.assertEqual(response.status_code, 401)
		response = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, user="Guest")
		self.assertEqual(response.status_code, 401)

	def test_rejects_invalid_transport_input(self):
		ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
		self.assertEqual(self.post([ping]).status_code, 400)
		self.assertEqual(self.post({"id": 1, "method": "ping"}).status_code, 400)
		self.assertEqual(self.post(ping, headers={"MCP-Protocol-Version": "1999-01-01"}).status_code, 400)
		self.assertEqual(self.post(ping, headers={"MCP-Protocol-Version": "2025-06-18"}).status_code, 200)
		self.assertEqual(self.post(ping, headers={"Origin": "https://evil.example"}).status_code, 403)
		self.assertEqual(self.post(ping, headers={"Origin": "http://localhost"}).status_code, 200)
		self.assertEqual(self.post(ping, headers={"Accept": "text/html"}).status_code, 406)

	def test_parse_errors(self):
		for body, code in (("{not json", -32700), ("5", -32600), ('"ping"', -32600)):
			response = self.post(body, content_type="text/plain")
			self.assertEqual(response.status_code, 400, body)
			self.assertEqual(json.loads(response.get_data())["error"]["code"], code, body)

	def test_tool_name_and_arguments_types(self):
		for params in ({"name": ["echo"]}, {"name": {"x": 1}}, {"name": "echo", "arguments": ["x"]}):
			self.assertEqual(self.rpc("tools/call", params)["error"]["code"], -32602, params)
		self.assertEqual(self.rpc("tools/call", ["echo"])["error"]["code"], -32602)

	def test_unknown_method_and_tool(self):
		self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)
		body = self.rpc("tools/call", {"name": "drop_database", "arguments": {}})
		self.assertEqual(body["error"]["code"], -32602)

	def test_invalid_arguments_are_protocol_errors(self):
		for arguments in (
			{},
			{"text": ""},
			{"text": 5},
			{"text": "x", "bogus": 1},
			{"text": "x", "count": "1"},
			{"text": "x", "count": True},
			{"text": "x", "loud": 1},
			{"text": "x", "mood": "angry"},
			{"text": "x", "tags": "a"},
			{"text": "x", "tags": ["a", "c"]},
		):
			body = self.rpc("tools/call", {"name": "echo", "arguments": arguments})
			self.assertEqual(body["error"]["code"], -32602, arguments)

	def test_valid_arguments_reach_the_tool(self):
		value = self.ok("echo", text="hi", count=3, loud=False, mood="calm", tags=["a", "b"])
		self.assertEqual(value, {"text": "hi", "count": 3, "loud": False, "mood": "calm", "tags": ["a", "b"]})

	def test_tools_run_as_the_session_user(self):
		self.assertEqual(self.ok("whoami")["user"], self.user)

	def test_validation_errors_are_tool_errors(self):
		self.assertEqual(self.error("refuse"), "Refused for a reason")

	def test_unexpected_error_hides_traceback(self):
		text = self.error("crash")
		self.assertNotIn("secret internals", text)
		self.assertNotIn("Traceback", text)

	def test_failed_tool_rolls_back_writes_and_commit_callbacks(self):
		description = f"mcp-rollback-{unique()}"
		before = list(frappe.db.after_commit._functions)
		self.assertIn("Failed after writing", self.error("write_then_fail", description=description))
		self.assertEqual(list(frappe.db.after_commit._functions), before)
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))
		self.assertNotIn(description, sample_tools.calls)

	def test_call_context_wraps_every_call(self):
		with hooks(mcp_tools=[SAMPLE], mcp_call_context=[f"{SAMPLE}.sample_context"]):
			self.assertTrue(self.ok("whoami")["in_context"])
			self.assertIn("Refused", self.error("refuse"))
		self.assertFalse(frappe.flags.mcp_sample_context)
		self.assertFalse(self.ok("whoami")["in_context"])


class TestRegistry(McpTestCase):
	def names(self):
		return [tool["name"] for tool in self.rpc("tools/list")["result"]["tools"]]

	def test_module_entry_lists_its_own_available_tools(self):
		self.assertEqual(self.names(), ["echo", "whoami", "refuse", "crash", "write_then_fail"])
		self.assertNotIn("imported_url", self.names())

	def test_definitions(self):
		definitions = {tool["name"]: tool for tool in self.rpc("tools/list")["result"]["tools"]}
		echo = definitions["echo"]
		self.assertEqual(echo["inputSchema"]["required"], ["text"])
		self.assertFalse(echo["inputSchema"]["additionalProperties"])
		self.assertTrue(echo["annotations"]["readOnlyHint"])
		self.assertFalse(definitions["write_then_fail"]["annotations"]["readOnlyHint"])

	def test_unavailable_tools_cannot_be_called(self):
		self.assertEqual(self.rpc("tools/call", {"name": "hidden"})["error"]["code"], -32602)

	def test_function_and_dict_entries(self):
		entry = {
			"name": "plain",
			"description": "A plain function.",
			"method": f"{SAMPLE}.plain",
			"input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
		}
		with hooks(mcp_tools=[f"{SAMPLE}.whoami", entry]):
			self.assertEqual(self.names(), ["whoami", "plain"])
			self.assertEqual(self.ok("plain", value="x"), {"plain": "x"})
			definition = self.rpc("tools/list")["result"]["tools"][1]
			self.assertFalse(definition["annotations"]["readOnlyHint"])
			self.assertEqual(
				self.rpc("tools/call", {"name": "plain", "arguments": {"v": 1}})["error"]["code"], -32602
			)

	def test_later_entries_replace_earlier_ones(self):
		entry = {"name": "echo", "description": "Replaced.", "method": f"{SAMPLE}.plain"}
		with hooks(mcp_tools=[SAMPLE, entry]):
			self.assertEqual(self.ok("echo"), {"plain": None})

	def test_broken_entries_are_logged_and_skipped(self):
		broken = [f"{SAMPLE}.plain", "mcpserver.tests.no_such_module", {"name": "incomplete"}]
		with hooks(mcp_tools=[*broken, f"{SAMPLE}.whoami"]), patch.object(frappe, "log_error") as log:
			self.assertEqual(list(tools.registry()), ["whoami"])
			self.assertEqual(self.ok("whoami")["user"], self.user)
		self.assertEqual(log.call_count, 2 * len(broken))

	def test_endpoint_is_whitelisted_for_guests_to_challenge(self):
		self.assertIn(mcp.endpoint, frappe.whitelisted)
		self.assertIn(mcp.endpoint, frappe.guest_methods)


class TestSetup(IntegrationTestCase):
	def test_hint_about_the_builtin_server(self):
		with patch.object(frappe, "get_system_settings", return_value=1), patch.dict(frappe.conf, {}):
			frappe.conf.pop("disable_mcp_server", None)
			self.assertTrue(setup.builtin_mcp_enabled())
			frappe.conf["disable_mcp_server"] = 1
			self.assertFalse(setup.builtin_mcp_enabled())
		with patch.object(frappe, "get_system_settings", return_value=0):
			self.assertFalse(setup.builtin_mcp_enabled())
