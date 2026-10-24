import frappe
from frappe.core.doctype.doctype.test_doctype import new_doctype

from mcpserver.tests.helpers import make_role, make_user, unique
from mcpserver.tests.test_mcp import McpTestCase

RECORD = "MCP Test Record"
READER = "MCP Test Reader"
AUDITOR = "MCP Test Auditor"
TOOLS = ["list_doctypes", "get_doctype", "list_documents", "search_documents", "get_document"]


def ensure_record_doctype():
	if frappe.db.exists("DocType", RECORD):
		return
	make_role(READER)
	make_role(AUDITOR)
	new_doctype(
		RECORD,
		fields=[
			{"fieldname": "title", "fieldtype": "Data", "label": "Title"},
			{"fieldname": "status", "fieldtype": "Select", "label": "Status", "options": "Open\nClosed"},
			{"fieldname": "audit_note", "fieldtype": "Data", "label": "Audit Note", "permlevel": 1},
			{"fieldname": "api_password", "fieldtype": "Password", "label": "API Password"},
		],
		permissions=[
			{"role": READER, "read": 1},
			{"role": AUDITOR, "read": 1},
			{"role": AUDITOR, "read": 1, "permlevel": 1},
		],
		title_field="title",
		search_fields="status,audit_note",
		autoname="hash",
	).insert(ignore_permissions=True)
	frappe.db.commit()


class TestDocumentTools(McpTestCase):
	tool_hooks = ("mcpserver.document_tools",)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_record_doctype()

	def setUp(self):
		super().setUp()
		self.reader = make_user("mcp-reader@example.test", [READER])
		self.auditor = make_user("mcp-auditor@example.test", [AUDITOR])
		self.allow(RECORD, "ToDo")
		self.tag = unique()[:10]
		self.record = frappe.get_doc(
			{
				"doctype": RECORD,
				"title": f"Alpha {self.tag}",
				"status": "Open",
				"audit_note": "level one",
				"api_password": "hunter2",
			}
		).insert(ignore_permissions=True)
		self.other_record = frappe.get_doc(
			{"doctype": RECORD, "title": f"Beta_{self.tag}", "status": "Closed"}
		).insert(ignore_permissions=True)

	def allow(self, *doctypes, enabled=1):
		settings = frappe.get_single("MCP Settings")
		settings.enable_document_tools = enabled
		settings.set("allowed_doctypes", [{"document_type": doctype} for doctype in doctypes])
		settings.save(ignore_permissions=True)

	def names(self, user=None):
		return [tool["name"] for tool in self.rpc("tools/list", user=user)["result"]["tools"]]

	def test_tools_are_hidden_until_enabled_with_doctypes(self):
		self.assertEqual(self.names(), TOOLS)
		self.allow(RECORD, enabled=0)
		self.assertEqual(self.names(), [])
		self.allow()
		self.assertEqual(self.names(), [])
		self.assertEqual(self.rpc("tools/call", {"name": "list_doctypes"})["error"]["code"], -32602)

	def test_settings_reject_unsafe_doctypes(self):
		for doctype in (
			"OAuth Bearer Token",
			"OAuth Client",
			"OAuth Authorization Code",
			"Has Role",
			"System Settings",
		):
			with self.assertRaises(frappe.ValidationError, msg=doctype):
				self.allow(doctype)
		with self.assertRaises(frappe.ValidationError):
			self.allow("ToDo", "ToDo")

	def test_list_doctypes_only_shows_readable_allowed_types(self):
		names = [row["name"] for row in self.ok("list_doctypes", as_user=self.reader)]
		self.assertEqual(names, [RECORD, "ToDo"])
		self.assertEqual(
			[row["name"] for row in self.ok("list_doctypes", as_user=self.reader, query="todo")], ["ToDo"]
		)
		self.assertEqual([row["name"] for row in self.ok("list_doctypes")], ["ToDo"])

	def test_doctypes_outside_the_allowlist_are_refused_even_for_administrator(self):
		for name, arguments in (
			("get_doctype", {"doctype": "User"}),
			("list_documents", {"doctype": "User"}),
			("search_documents", {"doctype": "User", "query": "admin"}),
			("get_document", {"doctype": "User", "name": "Administrator"}),
			("list_documents", {"doctype": "No Such Type"}),
		):
			self.assertIn("not available", self.error(name, as_user="Administrator", **arguments), name)

	def test_users_without_read_permission_are_refused(self):
		self.assertIn("not available", self.error("list_documents", doctype=RECORD))
		self.assertIn("not available", self.error("get_document", doctype=RECORD, name=self.record.name))

	def test_list_documents_applies_permission_queries(self):
		mine = frappe.get_doc(
			{"doctype": "ToDo", "description": f"mine {self.tag}", "allocated_to": self.user}
		)
		mine.insert(ignore_permissions=True)
		foreign = frappe.get_doc({"doctype": "ToDo", "description": f"foreign {self.tag}"})
		foreign.insert(ignore_permissions=True)
		rows = self.ok("list_documents", doctype="ToDo", filters={"description": ["like", f"%{self.tag}%"]})
		self.assertEqual([row["name"] for row in rows], [mine.name])
		self.assertTrue(rows[0]["url"].endswith(mine.name))

	def test_field_level_permissions_limit_fields_filters_and_order(self):
		self.assertNotIn("audit_note", self.fieldnames(self.reader))
		self.assertIn("audit_note", self.fieldnames(self.auditor))
		for arguments in (
			{"fields": ["name", "audit_note"]},
			{"filters": {"audit_note": "level one"}},
			{"order_by": "audit_note desc"},
		):
			self.assertIn(
				"audit_note", self.error("list_documents", as_user=self.reader, doctype=RECORD, **arguments)
			)
		rows = self.ok(
			"list_documents",
			as_user=self.auditor,
			doctype=RECORD,
			fields=["name", "audit_note"],
			filters={"audit_note": "level one"},
		)
		self.assertEqual(rows[0]["audit_note"], "level one")

	def fieldnames(self, user):
		return [
			field["fieldname"] for field in self.ok("get_doctype", as_user=user, doctype=RECORD)["fields"]
		]

	def test_get_document_hides_unreadable_and_password_fields(self):
		as_reader = self.ok("get_document", as_user=self.reader, doctype=RECORD, name=self.record.name)
		self.assertEqual(as_reader["title"], self.record.title)
		self.assertNotIn("audit_note", as_reader)
		as_auditor = self.ok("get_document", as_user=self.auditor, doctype=RECORD, name=self.record.name)
		self.assertEqual(as_auditor["audit_note"], "level one")
		as_admin = self.ok("get_document", as_user="Administrator", doctype=RECORD, name=self.record.name)
		for data in (as_reader, as_auditor, as_admin):
			self.assertNotIn("api_password", data)
			self.assertNotIn("hunter2", str(data))

	def test_passwords_are_never_listed_or_searched(self):
		self.assertNotIn("api_password", self.fieldnames(self.auditor))
		self.assertIn(
			"api_password",
			self.error("list_documents", as_user="Administrator", doctype=RECORD, fields=["api_password"]),
		)

	def test_filters_are_validated(self):
		for filters in (
			{"title": ["drop", "x"]},
			{"title": {"nested": 1}},
			{"title": ["in", "not a list"]},
			{"title": ["is", "maybe"]},
			{"name; select": "x"},
		):
			self.error("list_documents", as_user=self.reader, doctype=RECORD, filters=filters)
		rows = self.ok(
			"list_documents",
			as_user=self.reader,
			doctype=RECORD,
			fields=["name", "status"],
			filters={
				"status": ["in", ["Open"]],
				"title": ["like", f"%{self.tag}%"],
				"creation": ["is", "set"],
			},
			order_by="title asc",
		)
		self.assertEqual([row["name"] for row in rows], [self.record.name])

	def test_order_by_must_be_a_plain_field(self):
		for order_by in ("title; drop table x", "title asc, name", "(select 1)", "title sideways"):
			self.error("list_documents", as_user=self.reader, doctype=RECORD, order_by=order_by)

	def test_limit_and_start(self):
		filters = {"title": ["like", f"%{self.tag}%"]}
		args = {"as_user": self.reader, "doctype": RECORD, "filters": filters, "order_by": "title asc"}
		self.assertEqual(len(self.ok("list_documents", **args, limit=1)), 1)
		second = self.ok("list_documents", **args, limit=1, start=1)
		self.assertEqual([row["name"] for row in second], [self.other_record.name])

	def test_search_documents(self):
		rows = self.ok("search_documents", as_user=self.reader, doctype=RECORD, query=f"alpha {self.tag}")
		self.assertEqual([row["name"] for row in rows], [self.record.name])
		self.assertEqual(rows[0]["title"], self.record.title)
		underscore = self.ok("search_documents", as_user=self.reader, doctype=RECORD, query=f"_{self.tag}")
		self.assertEqual([row["name"] for row in underscore], [self.other_record.name])
		self.assertEqual(self.ok("search_documents", as_user=self.reader, doctype=RECORD, query="%"), [])
		self.assertIn("empty", self.error("search_documents", as_user=self.reader, doctype=RECORD, query=" "))

	def test_search_never_matches_unreadable_fields(self):
		frappe.db.set_value(RECORD, self.record.name, "status", "Open")
		rows = self.ok("search_documents", as_user=self.reader, doctype=RECORD, query="level one")
		self.assertEqual(rows, [])
