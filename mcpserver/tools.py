import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

import frappe
from frappe.utils import cint, get_url_to_form

DEFAULT_LIMIT = 20
MAX_STRING = 10_000
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
		schema = {"type": "object", "properties": self.properties, "required": self.required}
		return invalid_value("arguments", arguments, {**schema, "additionalProperties": False})


def invalid_value(path, value, schema) -> str | None:
	if value is None:
		return None
	type_name = schema.get("type")
	expected = JSON_TYPES.get(type_name)
	if expected is None:
		return None
	if not isinstance(value, expected) or (expected is not bool and isinstance(value, bool)):
		return f"{path} must be of type {type_name}"
	if "enum" in schema and value not in schema["enum"]:
		return f"{path} must be one of {', '.join(map(str, schema['enum']))}"
	if type_name == "string" and len(value) > schema.get("maxLength", MAX_STRING):
		return f"{path} must be at most {schema.get('maxLength', MAX_STRING)} characters"
	if type_name in ("integer", "number"):
		if "minimum" in schema and value < schema["minimum"]:
			return f"{path} must be at least {schema['minimum']}"
		if "maximum" in schema and value > schema["maximum"]:
			return f"{path} must be at most {schema['maximum']}"
	if type_name == "array":
		for index, item in enumerate(value):
			if problem := invalid_value(f"{path}[{index}]", item, schema.get("items", {})):
				return problem
	if type_name == "object":
		return invalid_object(path, value, schema)
	return None


def invalid_object(path, value, schema) -> str | None:
	properties = schema.get("properties") or {}
	prefix = "" if path == "arguments" else f"{path}."
	if schema.get("additionalProperties") is False and (unknown := set(value) - set(properties)):
		return f"unknown argument(s) {', '.join(prefix + key for key in sorted(unknown))}"
	missing = [key for key in schema.get("required") or () if value.get(key) in (None, "")]
	if missing:
		return f"missing required argument(s) {', '.join(prefix + key for key in missing)}"
	for key, item in value.items():
		if key in properties and (problem := invalid_value(prefix + key, item, properties[key])):
			return problem
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
		available=frappe.get_attr(entry["available"]) if entry.get("available") else None,
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
