import re

import frappe
from frappe import _
from frappe.model import no_value_fields, table_fields
from frappe.utils import cint, cstr

from mcpserver.mcp_server.doctype.mcp_settings.mcp_settings import allowed_doctypes
from mcpserver.tools import integer, limit_of, obj, string, string_list, tool, url

STANDARD_FIELDS = ("name", "owner", "creation", "modified", "modified_by", "docstatus")
SEARCHABLE_TYPES = ("Data", "Link", "Dynamic Link", "Select", "Small Text", "Text", "Autocomplete", "Phone")
OPERATORS = ("=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "is", "between")
LIST_OPERATORS = ("in", "not in", "between")
ORDER_BY = re.compile(r"(\w+)(?: (asc|desc))?", re.IGNORECASE)
SCALARS = (str, int, float, bool, type(None))
MAX_QUERY = 140

DOCTYPE = string("Document type, as returned by list_doctypes.")


def enabled():
	return bool(allowed_doctypes())


def readable_doctype(doctype):
	if doctype not in allowed_doctypes() or not frappe.has_permission(doctype, "read"):
		raise frappe.PermissionError(_("Document type {0} is not available.").format(doctype))
	return frappe.get_meta(doctype)


def readable_fields(meta) -> list[str]:
	levels = meta.get_permlevel_access("read")
	return [*STANDARD_FIELDS, *(df.fieldname for df in visible_fields(meta) if (df.permlevel or 0) in levels)]


def visible_fields(meta):
	return [
		df
		for df in meta.fields
		if df.fieldtype not in no_value_fields
		and df.fieldtype not in table_fields
		and df.fieldtype != "Password"
	]


def checked_fields(meta, fields) -> list[str]:
	allowed = readable_fields(meta)
	unknown = [field for field in fields if field not in allowed]
	if unknown:
		frappe.throw(_("Unknown or unreadable field(s): {0}").format(", ".join(map(cstr, unknown))))
	return list(dict.fromkeys(fields))


def checked_filters(meta, filters) -> list:
	allowed = readable_fields(meta)
	result = []
	for field, condition in (filters or {}).items():
		operator, value = (
			condition if isinstance(condition, list) and len(condition) == 2 else ("=", condition)
		)
		operator = cstr(operator).lower()
		if field not in allowed:
			frappe.throw(_("Unknown or unreadable field: {0}").format(field))
		if operator not in OPERATORS:
			frappe.throw(_("Unsupported operator: {0}").format(operator))
		if not valid_value(operator, value):
			frappe.throw(_("Invalid value for {0}").format(field))
		result.append([meta.name, field, operator, value])
	return result


def valid_value(operator, value) -> bool:
	if operator in LIST_OPERATORS:
		return isinstance(value, list) and all(isinstance(item, SCALARS) for item in value)
	if operator == "is":
		return value in ("set", "not set")
	return isinstance(value, SCALARS)


def checked_order(meta, order_by) -> str:
	match = ORDER_BY.fullmatch(cstr(order_by).strip())
	if not match:
		frappe.throw(_("order_by must be a field, optionally followed by asc or desc."))
	field = match.group(1)
	if field not in readable_fields(meta):
		frappe.throw(_("Unknown or unreadable field: {0}").format(field))
	return f"{field} {(match.group(2) or 'asc').lower()}"


@tool(
	"list_doctypes",
	"List document types",
	"Document types this server exposes that you may read, with module and description. Pass `query` "
	"to search their names. Read-only.",
	available=enabled,
	query=string("Case-insensitive part of the document type name."),
)
def list_doctypes(query=None):
	needle = cstr(query).strip().lower()
	rows = []
	for doctype in allowed_doctypes():
		if needle in doctype.lower() and frappe.has_permission(doctype, "read"):
			meta = frappe.get_meta(doctype)
			rows.append({"name": doctype, "module": meta.module, "description": meta.description or None})
	return rows


@tool(
	"get_doctype",
	"Get document type",
	"Fields you may read on one document type (fieldname, label, type, options, required), its child "
	"tables, title field and search fields. Read-only.",
	available=enabled,
	required=("doctype",),
	doctype=DOCTYPE,
)
def get_doctype(doctype):
	meta = readable_doctype(doctype)
	readable = set(readable_fields(meta))
	levels = meta.get_permlevel_access("read")
	return {
		"name": meta.name,
		"description": meta.description or None,
		"title_field": meta.get_title_field(),
		"search_fields": [field for field in meta.get_search_fields() if field in readable],
		"is_submittable": bool(meta.is_submittable),
		"fields": [
			{
				"fieldname": df.fieldname,
				"label": df.label,
				"fieldtype": df.fieldtype,
				"options": df.options or None,
				"required": bool(df.reqd),
			}
			for df in visible_fields(meta)
			if df.fieldname in readable
		],
		"tables": [
			{"fieldname": df.fieldname, "label": df.label, "doctype": df.options}
			for df in meta.get_table_fields()
			if (df.permlevel or 0) in levels
		],
	}


@tool(
	"list_documents",
	"List documents",
	"Documents of one type that you may read, newest first unless `order_by` is given. `filters` maps a "
	'field to a value or to [operator, value], e.g. {"status": "Open", "modified": [">", "2026-01-01"]}; '
	"operators: " + ", ".join(OPERATORS) + ". Read-only.",
	available=enabled,
	required=("doctype",),
	doctype=DOCTYPE,
	fields=string_list("Fields to return (default: name and the title field)."),
	filters=obj("Field conditions, combined with AND."),
	order_by=string("A readable field, optionally followed by asc or desc."),
	limit=integer("Maximum number of results (default 20, max 100)."),
	start=integer("Number of results to skip.", minimum=0, maximum=10**6),
)
def list_documents(doctype, fields=None, filters=None, order_by=None, limit=None, start=None):
	meta = readable_doctype(doctype)
	fields = checked_fields(meta, fields or default_fields(meta))
	rows = frappe.get_list(
		doctype,
		fields=fields,
		filters=checked_filters(meta, filters),
		order_by=checked_order(meta, order_by) if order_by else "modified desc",
		limit_start=max(cint(start), 0),
		limit_page_length=limit_of(limit),
	)
	return [with_url(doctype, row) for row in rows]


@tool(
	"search_documents",
	"Search documents",
	"Documents of one type whose name, title or search fields contain `query`. Read-only.",
	available=enabled,
	required=("doctype", "query"),
	doctype=DOCTYPE,
	query=string("Search text."),
	limit=integer("Maximum number of results (default 20, max 100)."),
)
def search_documents(doctype, query, limit=None):
	meta = readable_doctype(doctype)
	query = cstr(query).strip()[:MAX_QUERY]
	if not query:
		frappe.throw(_("query must not be empty."))
	fields = search_fields(meta)
	pattern = f"%{escape_like(query)}%"
	rows = frappe.get_list(
		doctype,
		fields=fields,
		or_filters=[[doctype, field, "like", pattern] for field in fields],
		order_by="modified desc",
		limit_page_length=limit_of(limit),
	)
	return [with_url(doctype, row) for row in rows]


@tool(
	"get_document",
	"Get document",
	"One document with its child tables, limited to the fields you may read. Read-only.",
	available=enabled,
	required=("doctype", "name"),
	doctype=DOCTYPE,
	name=string("Document name (ID)."),
)
def get_document(doctype, name):
	readable_doctype(doctype)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	doc.apply_fieldlevel_read_permissions()
	data = without_passwords(doc, doc.as_dict(convert_dates_to_str=True, no_nulls=True))
	return {**data, "url": url(doctype, doc.name)}


def default_fields(meta):
	title = meta.get_title_field()
	return ["name", title] if title != "name" and title in readable_fields(meta) else ["name"]


def search_fields(meta):
	readable = set(readable_fields(meta))
	candidates = ["name", meta.get_title_field(), *meta.get_search_fields()]
	return [
		field
		for field in dict.fromkeys(candidates)
		if field in readable and (field == "name" or meta.get_field(field).fieldtype in SEARCHABLE_TYPES)
	]


def without_passwords(doc, data):
	for df in doc.meta.fields:
		if df.fieldtype == "Password":
			data.pop(df.fieldname, None)
	for table in doc.meta.get_table_fields():
		child_meta = frappe.get_meta(table.options)
		hidden = [df.fieldname for df in child_meta.fields if df.fieldtype == "Password"]
		for row in data.get(table.fieldname) or []:
			for fieldname in hidden:
				row.pop(fieldname, None)
	return data


def with_url(doctype, row):
	row["url"] = url(doctype, row["name"])
	return row


def escape_like(text):
	return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
