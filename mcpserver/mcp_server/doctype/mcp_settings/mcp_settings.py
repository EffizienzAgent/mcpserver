import frappe
from frappe import _
from frappe.model.document import Document

DENIED_DOCTYPES = ("OAuth Authorization Code", "OAuth Bearer Token", "OAuth Client")


class MCPSettings(Document):
	def validate(self):
		seen = set()
		for row in self.allowed_doctypes:
			if row.document_type in seen:
				frappe.throw(_("{0} is listed twice.").format(row.document_type))
			seen.add(row.document_type)
			if problem := unsupported_doctype(row.document_type):
				frappe.throw(problem)


def unsupported_doctype(doctype):
	if doctype in DENIED_DOCTYPES:
		return _("{0} holds credentials and cannot be exposed.").format(doctype)
	meta = frappe.get_meta(doctype)
	if meta.istable or meta.issingle or meta.is_virtual:
		return _("{0} is a child table, single or virtual document type.").format(doctype)
	return None


def allowed_doctypes() -> list[str]:
	settings = frappe.get_cached_doc("MCP Settings")
	if not settings.enable_document_tools:
		return []
	return [
		row.document_type
		for row in settings.allowed_doctypes
		if frappe.db.exists("DocType", row.document_type) and not unsupported_doctype(row.document_type)
	]
