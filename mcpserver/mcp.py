import json
from contextlib import ExitStack, contextmanager
from urllib.parse import urlparse

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import cstr, strip_html
from werkzeug.wrappers import Response

import mcpserver
from mcpserver.tools import registry

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_NAME = "mcpserver"
SAVEPOINT = "mcp_tool_call"
COMMIT_CALLBACKS = ("before_commit", "after_commit")
RATE_LIMIT = 300

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


class RpcError(Exception):
	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@frappe.whitelist(allow_guest=True, methods=["GET", "POST", "DELETE"])
@rate_limit(limit=RATE_LIMIT, seconds=60)
def endpoint(**kwargs):
	request = frappe.request
	if request.method != "POST":
		return Response(status=405, headers={"Allow": "POST"})
	if foreign_origin(request):
		return error_response(INVALID_REQUEST, "Origin not allowed", 403)
	if frappe.session.user == "Guest":
		return error_response(
			INVALID_REQUEST,
			"Authentication required: sign in via OAuth or send Authorization: token key:secret",
			401,
		)
	if not accepts_json(request):
		return error_response(INVALID_REQUEST, "Accept must include application/json", 406)
	version = request.headers.get("MCP-Protocol-Version")
	if version and version not in PROTOCOL_VERSIONS:
		return error_response(INVALID_REQUEST, f"Unsupported MCP-Protocol-Version: {version}", 400)
	try:
		message = json.loads(request.get_data(as_text=True))
	except ValueError:
		return error_response(PARSE_ERROR, "Parse error", 400)
	if isinstance(message, list):
		return error_response(INVALID_REQUEST, "Batch requests are not supported", 400)
	if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
		return error_response(INVALID_REQUEST, "Invalid JSON-RPC 2.0 message", 400)
	if "method" not in message or "id" not in message:
		return Response(status=202)
	return json_response(handle(message))


def foreign_origin(request) -> bool:
	origin = request.headers.get("Origin")
	return bool(origin) and urlparse(origin).netloc != request.host


def accepts_json(request) -> bool:
	return not request.headers.get("Accept") or request.accept_mimetypes.accept_json


def handle(message) -> dict:
	request_id = message["id"]
	try:
		params = message.get("params") or {}
		if not isinstance(params, dict):
			raise RpcError(INVALID_PARAMS, "params must be an object")
		result = dispatch(cstr(message["method"]), params)
	except RpcError as e:
		return {"jsonrpc": "2.0", "id": request_id, "error": {"code": e.code, "message": e.message}}
	return {"jsonrpc": "2.0", "id": request_id, "result": result}


def dispatch(method, params) -> dict:
	if method == "initialize":
		return initialize(params)
	if method == "ping":
		return {}
	if method == "tools/list":
		return {"tools": [found.definition() for found in registry().values()]}
	if method == "tools/call":
		return call_tool(params)
	raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def initialize(params) -> dict:
	requested = params.get("protocolVersion")
	return {
		"protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
		"capabilities": {"tools": {"listChanged": False}},
		"serverInfo": server_info(),
		"instructions": instructions(),
	}


def server_info() -> dict:
	info = {key: values[-1] for key, values in frappe.get_hooks("mcp_server_info", {}).items() if values}
	return {
		"name": info.get("name") or DEFAULT_NAME,
		"title": info.get("title") or "MCP server",
		"version": info.get("version") or mcpserver.__version__,
	}


def instructions() -> str:
	parts = [hook_text(path) for path in frappe.get_hooks("mcp_instructions")]
	return "\n\n".join(cstr(part).strip() for part in parts if cstr(part).strip())


def hook_text(path):
	value = frappe.get_attr(path)
	return value() if callable(value) else value


def call_tool(params) -> dict:
	name, arguments = params.get("name"), params.get("arguments") or {}
	if not isinstance(name, str):
		raise RpcError(INVALID_PARAMS, "name must be a string")
	if not isinstance(arguments, dict):
		raise RpcError(INVALID_PARAMS, "arguments must be an object")
	found = registry().get(name)
	if not found:
		raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
	problem = found.invalid_arguments(arguments)
	if problem:
		raise RpcError(INVALID_PARAMS, f"Invalid arguments for {name}: {problem}")
	callbacks = commit_callbacks()
	frappe.db.savepoint(SAVEPOINT)
	try:
		with call_context():
			result = found.run(**arguments)
	except Exception as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		restore_commit_callbacks(callbacks)
		return tool_result(error_text(e), is_error=True)
	finally:
		frappe.clear_messages()
	frappe.db.release_savepoint(SAVEPOINT)
	return tool_result(as_json(result))


@contextmanager
def call_context():
	with ExitStack() as stack:
		for path in frappe.get_hooks("mcp_call_context"):
			stack.enter_context(frappe.get_attr(path)())
		yield


def commit_callbacks() -> dict:
	return {name: list(getattr(frappe.db, name)._functions) for name in COMMIT_CALLBACKS}


def restore_commit_callbacks(snapshot) -> None:
	for name, functions in snapshot.items():
		manager = getattr(frappe.db, name)
		manager.reset()
		for function in functions:
			manager.add(function)


def error_text(e) -> str:
	if isinstance(e, frappe.ValidationError | frappe.PermissionError):
		return strip_html(cstr(e)).strip() or type(e).__name__
	frappe.log_error(title="MCP tool call failed")
	return "Unexpected server error; details are in the Error Log."


def tool_result(text, is_error=False) -> dict:
	return {"content": [{"type": "text", "text": text}], "isError": is_error}


def as_json(value) -> str:
	return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def json_response(body, status=200) -> Response:
	return Response(as_json(body), status=status, content_type="application/json")


def error_response(code, message, status) -> Response:
	return json_response({"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}, status)
