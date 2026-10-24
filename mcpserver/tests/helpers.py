import json
import uuid
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import urlencode

import frappe
from frappe.app import make_form_dict
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

BASE = "https://mcp.example.test"
MCP_PATH = "/api/method/mcpserver.mcp.endpoint"
ACCEPT = "application/json, text/event-stream"
DESK_ROLE = "MCP Test User"


def unique():
	return uuid.uuid4().hex


def make_role(name):
	if not frappe.db.exists("Role", name):
		frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 1}).insert(
			ignore_permissions=True
		)
	return name


def make_user(email, roles=(), desk=True):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"user_type": "System User",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.add_roles(*(make_role(role) for role in ((DESK_ROLE,) if desk else ()) + tuple(roles)))
	return email


def set_request(
	method, path, body=None, headers=None, query=None, content_type="application/json", base_url=BASE
):
	builder = EnvironBuilder(
		method=method,
		base_url=base_url,
		path=path,
		query_string=query,
		data=encode(body, content_type),
		headers=headers or {},
		content_type=content_type if body is not None else None,
	)
	frappe.local.request = Request(builder.get_environ())
	make_form_dict(frappe.local.request)
	return frappe.local.request


def encode(body, content_type):
	if body is None:
		return None
	if isinstance(body, str):
		return body
	if content_type == "application/x-www-form-urlencoded":
		return urlencode(body)
	return json.dumps(body)


def parse(response):
	return json.loads(response.get_data(as_text=True))


@contextmanager
def hooks(**values):
	real = frappe.get_hooks

	def fake(hook=None, *args, **kwargs):
		if hook in values and not kwargs.get("app_name"):
			return values[hook]
		return real(hook, *args, **kwargs)

	with patch("frappe.get_hooks", side_effect=fake):
		yield


def restore_request_state(test):
	test.addCleanup(frappe.set_user, "Administrator")
	test.addCleanup(setattr, frappe.local, "request", getattr(frappe.local, "request", None))
	test.addCleanup(setattr, frappe.local, "form_dict", frappe.local.form_dict)
	test.addCleanup(setattr, frappe.local, "request_ip", getattr(frappe.local, "request_ip", None))
	test.addCleanup(frappe.db.after_commit.reset)
	frappe.local.request_ip = f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.1"
