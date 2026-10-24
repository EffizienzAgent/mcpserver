import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

import frappe
from frappe.utils import cint, get_url_to_form

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
JSON_TYPES = {
	"string": str,
	"integer": int,
	"number": int | float,
	"boolean": bool,
	"array": list,
	"object": dict,
}


@dataclass(frozen=True)
class Tool:
	name: str
	title: str
	description: str
	properties: dict
	required: tuple
	read_only: bool
	run: Callable
	available: Callable | None = None

	def definition(self) -> dict:
		return {
			"name": self.name,
			"title": self.title,
			"description": self.description,
			"inputSchema": {
				"type": "object",
				"properties": self.properties,
				"required": list(self.required),
				"additionalProperties": False,
			},
			"annotations": {
				"title": self.title,
				"readOnlyHint": self.read_only,
				"destructiveHint": False,
				"openWorldHint": False,
			},
		}

	def is_available(self) -> bool:
		return self.available is None or bool(self.available())

	def invalid_arguments(self, arguments) -> str | None:
		unknown = set(arguments) - set(self.properties)
		if unknown:
			return f"unknown argument(s) {', '.join(sorted(unknown))}"
		missing = [key for key in self.required if arguments.get(key) in (None, "")]
		if missing:
			return f"missing required argument(s) {', '.join(missing)}"
		for key, value in arguments.items():
			if value is not None and (problem := invalid_value(key, value, self.properties[key])):
				return problem
		return None


def invalid_value(key, value, schema) -> str | None:
	expected = JSON_TYPES.get(schema.get("type"))
	if expected is None:
		return None
	if not isinstance(value, expected) or (expected is not bool and isinstance(value, bool)):
		return f"{key} must be of type {schema['type']}"
	allowed = schema.get("enum") or schema.get("items", {}).get("enum")
	if allowed and any(v not in allowed for v in (value if isinstance(value, list) else [value])):
		return f"{key} must be one of {', '.join(map(str, allowed))}"
	return None


def tool(name, title, description, /, read_only=True, required=(), available=None, **properties):
	def register(fn):
		fn.mcp_tool = Tool(name, title, description, properties, tuple(required), read_only, fn, available)
		return fn

	return register


def from_hook_entry(entry) -> list[Tool]:
	if isinstance(entry, dict):
		return [from_dict(entry)]
	target = resolve_path(entry)
	if isinstance(target, Tool):
		return [target]
	if isinstance(target, ModuleType):
		return [
			value.mcp_tool
			for value in vars(target).values()
			if inspect.isfunction(value)
			and value.__module__ == target.__name__
			and isinstance(getattr(value, "mcp_tool", None), Tool)
		]
	if isinstance(getattr(target, "mcp_tool", None), Tool):
		return [target.mcp_tool]
	raise TypeError(f"mcp_tools entry {entry} is not a tool, a module with tools or a tool definition")


def resolve_path(path):
	try:
		return importlib.import_module(path)
	except ModuleNotFoundError as e:
		if e.name != path:
			raise
	return frappe.get_attr(path)


def from_dict(entry) -> Tool:
	schema = entry.get("input_schema") or {}
	return Tool(
		name=entry["name"],
		title=entry.get("title") or entry["name"],
		description=entry["description"],
		properties=schema.get("properties") or {},
		required=tuple(schema.get("required") or ()),
		read_only=bool(entry.get("read_only", False)),
		run=frappe.get_attr(entry["method"]),
	)


def registry() -> dict[str, Tool]:
	tools = {}
	for entry in frappe.get_hooks("mcp_tools"):
		try:
			found_tools = from_hook_entry(entry)
		except Exception:
			frappe.log_error(title=f"Invalid mcp_tools entry: {entry}")
			continue
		for found in found_tools:
			tools[found.name] = found
	return {name: found for name, found in tools.items() if found.is_available()}


def string(description, enum=None):
	return {"type": "string", "description": description, **({"enum": list(enum)} if enum else {})}


def integer(description, minimum=1, maximum=MAX_LIMIT):
	return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


def boolean(description):
	return {"type": "boolean", "description": description}


def string_list(description, enum=None):
	items = {"type": "string", **({"enum": list(enum)} if enum else {})}
	return {"type": "array", "description": description, "items": items}


def obj(description):
	return {"type": "object", "description": description}


def url(doctype, name):
	return get_url_to_form(doctype, name)


def limit_of(value, default=DEFAULT_LIMIT):
	return min(max(cint(value) or default, 1), MAX_LIMIT)


def readable_list(doctype, **kwargs):
	if not frappe.has_permission(doctype, "read"):
		return []
	return frappe.get_list(doctype, **kwargs)


def with_urls(doctype, rows):
	for row in rows:
		row["url"] = url(doctype, row["name"])
	return rows
