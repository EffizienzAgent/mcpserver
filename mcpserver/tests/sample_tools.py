from contextlib import contextmanager

import frappe

from mcpserver.tools import boolean, integer, string, string_list, tool, url

calls = []


@tool(
	"echo",
	"Echo",
	"Returns its arguments.",
	required=("text",),
	text=string("Any text."),
	count=integer("A number."),
	loud=boolean("A flag."),
	mood=string("A mood.", enum=("calm", "loud")),
	tags=string_list("Tags.", enum=("a", "b")),
)
def echo(text, count=None, loud=None, mood=None, tags=None):
	return {"text": text, "count": count, "loud": loud, "mood": mood, "tags": tags}


@tool("whoami", "Who am I", "Returns the session user.")
def whoami():
	return {"user": frappe.session.user, "in_context": bool(frappe.flags.mcp_sample_context)}


@tool("refuse", "Refuse", "Raises a validation error.")
def refuse():
	frappe.throw("Refused for a reason")


@tool("crash", "Crash", "Raises an unexpected error.")
def crash():
	raise KeyError("secret internals")


@tool(
	"write_then_fail",
	"Write then fail",
	"Writes, registers a commit callback, then fails.",
	read_only=False,
	description=string("Text."),
)
def write_then_fail(description):
	frappe.get_doc({"doctype": "ToDo", "description": description}).insert()
	frappe.db.after_commit.add(lambda: calls.append(description))
	frappe.throw("Failed after writing")


@tool("hidden", "Hidden", "Never available.", available=lambda: False)
def hidden():
	return "hidden"


def plain(value=None):
	return {"plain": value}


@contextmanager
def sample_context():
	frappe.flags.mcp_sample_context = True
	try:
		yield
	finally:
		frappe.flags.mcp_sample_context = False


INSTRUCTIONS = "Sample instructions."
imported_url = url
