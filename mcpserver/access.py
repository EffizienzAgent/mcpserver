import frappe
from frappe.permissions import SYSTEM_USER_ROLE


def allowed_roles() -> list[str]:
	return list(dict.fromkeys(frappe.get_hooks("mcp_allowed_roles"))) or [SYSTEM_USER_ROLE]


def may_use_mcp(user=None) -> bool:
	return bool(set(allowed_roles()) & set(frappe.get_roles(user)))
