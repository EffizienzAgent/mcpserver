import re
from urllib.parse import urlencode, urlsplit

import frappe
from frappe.integrations import oauth2
from frappe.integrations.utils import OAuth2DynamicClientMetadata, _is_secure_redirect_uri
from frappe.oauth import OAuthWebRequestValidator
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, escape_html, now_datetime
from pydantic import ValidationError
from werkzeug.utils import redirect

from mcpserver.mcp import json_response

DYNAMIC_CLIENT_ID = "mcpserver-dcr"
DEFAULT_HTTPS_REDIRECT_URIS = (
	"https://claude.ai/api/mcp/auth_callback",
	"https://claude.com/api/mcp/auth_callback",
)
GRANT_TYPES = {"authorization_code", "refresh_token"}
CLIENT_NAME_PUNCTUATION = set(" ._()-:/+")
MAX_CLIENT_NAME = 120
SCOPE = re.compile(r"[A-Za-z0-9_.:-]+( [A-Za-z0-9_.:-]+)*")
REGISTRATION_LIMIT = 20
MAX_DYNAMIC_CLIENTS = 200
REFRESH_TOKEN_DAYS = 30
STALE_DAYS = 7
ROTATION_GRACE_SECONDS = 60
PKCE_REQUIRED = "PKCE with code_challenge_method=S256 is required"


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def authorize(**kwargs):
	return pkce_rejection() or oauth2.authorize(**kwargs)


@frappe.whitelist(methods=["POST"])
def approve(*args, **kwargs):
	return pkce_rejection() or oauth2.approve(*args, **kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def get_token(*args, **kwargs):
	form = frappe.form_dict
	client_id = form.get("client_id")
	if client_id and not frappe.db.exists("OAuth Client", client_id):
		return oauth_error("invalid_client", "Unknown client", 401)
	if form.get("grant_type") != "refresh_token":
		return oauth2.get_token(*args, **kwargs)
	token = refresh_token_row(form.get("refresh_token"))
	if problem := refresh_problem(token, client_id):
		return oauth_error(*problem)
	response = oauth2.get_token(*args, **kwargs)
	if is_dynamic_client(token.client):
		return rotate(token) or response
	return response


def refresh_token_row(refresh_token):
	if not refresh_token:
		return None
	return frappe.db.get_value(
		"OAuth Bearer Token",
		{"refresh_token": refresh_token},
		["name", "client", "user", "status", "creation", "modified"],
		as_dict=True,
		for_update=True,
	)


def refresh_problem(token, client_id):
	if token and not client_id and is_dynamic_client(token.client):
		return "invalid_request", "client_id is required"
	if not token or (client_id and token.client != client_id):
		return "invalid_grant", "Unknown or revoked refresh token"
	if token.status != "Active":
		if is_dynamic_client(token.client) and not recently_revoked(token):
			revoke_grant(token)
		return "invalid_grant", "Unknown or revoked refresh token"
	if token.creation < add_to_date(now_datetime(), days=-REFRESH_TOKEN_DAYS):
		frappe.db.set_value("OAuth Bearer Token", token.name, "status", "Revoked")
		return "invalid_grant", "Refresh token expired"
	return None


def recently_revoked(token) -> bool:
	return token.modified > add_to_date(now_datetime(), seconds=-ROTATION_GRACE_SECONDS)


def rotate(old):
	issued = frappe.local.response.get("access_token")
	if not issued or frappe.local.response.get("error"):
		return None
	if not claim(old.name):
		frappe.db.set_value("OAuth Bearer Token", issued, "status", "Revoked")
		frappe.local.response = frappe._dict()
		return oauth_error("invalid_grant", "Unknown or revoked refresh token")
	frappe.db.set_value("OAuth Bearer Token", issued, "creation", old.creation, update_modified=False)
	return None


def claim(name) -> bool:
	frappe.db.sql(
		"update `tabOAuth Bearer Token` set status = 'Revoked', modified = %s where name = %s and status = 'Active'",
		(now_datetime(), name),
	)
	return frappe.db._cursor.rowcount == 1


def revoke_grant(token):
	frappe.db.set_value(
		"OAuth Bearer Token",
		{"client": token.client, "user": token.user, "status": "Active"},
		"status",
		"Revoked",
	)


def pkce_rejection():
	form = frappe.form_dict
	client_id = form.get("client_id")
	if not is_dynamic_client(client_id):
		return None
	if form.get("code_challenge") and form.get("code_challenge_method") == "S256":
		return None
	redirect_uri = form.get("redirect_uri")
	if not redirect_uri or not OAuthWebRequestValidator().validate_redirect_uri(
		client_id, redirect_uri, None
	):
		return oauth_error("invalid_request", PKCE_REQUIRED)
	params = {"error": "invalid_request", "error_description": PKCE_REQUIRED}
	if form.get("state"):
		params["state"] = form.get("state")
	separator = "&" if "?" in redirect_uri else "?"
	return redirect(f"{redirect_uri}{separator}{urlencode(params)}", 302)


def is_dynamic_client(client_id) -> bool:
	return (
		bool(client_id) and frappe.db.get_value("OAuth Client", client_id, "software_id") == DYNAMIC_CLIENT_ID
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=REGISTRATION_LIMIT, seconds=60 * 60)
def register_client():
	if not frappe.get_cached_value("OAuth Settings", "OAuth Settings", "enable_dynamic_client_registration"):
		return oauth_error("invalid_request", "Dynamic client registration is disabled", 404)
	data = frappe.request.get_json(silent=True)
	if not isinstance(data, dict):
		return oauth_error("invalid_client_metadata", "Body must be a JSON object")
	try:
		metadata = OAuth2DynamicClientMetadata.model_validate(data)
	except ValidationError as e:
		return oauth_error("invalid_client_metadata", str(e))
	if problem := metadata_problem(metadata, data["redirect_uris"]):
		return oauth_error(*problem)
	if frappe.db.count("OAuth Client", {"software_id": DYNAMIC_CLIENT_ID}) >= MAX_DYNAMIC_CLIENTS:
		return oauth_error("temporarily_unavailable", "Too many registered clients", 429)
	return registration_response(create_client(metadata, data["redirect_uris"][0]), metadata)


def metadata_problem(metadata, raw_redirect_uris):
	if not valid_client_name(metadata.client_name):
		return "invalid_client_metadata", (
			f"client_name: up to {MAX_CLIENT_NAME} letters, digits, spaces and . _ ( ) - : / +"
		)
	if metadata.scope is not None and not SCOPE.fullmatch(metadata.scope):
		return "invalid_client_metadata", "scope must be space-separated plain tokens"
	if metadata.token_endpoint_auth_method != "none":
		return "invalid_client_metadata", "Only public clients (token_endpoint_auth_method none)"
	if metadata.grant_types and not set(metadata.grant_types) <= GRANT_TYPES:
		return "invalid_client_metadata", "Only authorization_code and refresh_token grants are supported"
	if metadata.response_types and set(metadata.response_types) != {"code"}:
		return "invalid_client_metadata", "Only the code response type is supported"
	if len(raw_redirect_uris) != 1:
		return "invalid_redirect_uri", "Register exactly one redirect URI"
	if not allowed_redirect_uri(raw_redirect_uris[0], metadata.redirect_uris[0]):
		return "invalid_redirect_uri", f"Redirect URI not allowed: {raw_redirect_uris[0]}"
	return None


def valid_client_name(name) -> bool:
	return (
		0 < len(name) <= MAX_CLIENT_NAME
		and bool(name.strip())
		and all(char.isalnum() or char in CLIENT_NAME_PUNCTUATION for char in name)
	)


def allowed_redirect_uri(raw, uri) -> bool:
	if raw in https_redirect_uris():
		return True
	parts = urlsplit(raw)
	if parts.scheme != "http" or parts.username or parts.password or "#" in raw:
		return False
	if any(char.isspace() or not char.isprintable() for char in raw):
		return False
	return parts.hostname == "localhost" or _is_secure_redirect_uri(uri)


def https_redirect_uris() -> tuple:
	uris = frappe.conf.get("mcp_oauth_redirect_uris")
	if uris is None:
		return DEFAULT_HTTPS_REDIRECT_URIS
	if not isinstance(uris, list | tuple) or not all(isinstance(uri, str) for uri in uris):
		return ()
	return tuple(uri for uri in uris if uri.startswith("https://"))


def create_client(metadata, redirect_uri):
	return frappe.get_doc(
		{
			"doctype": "OAuth Client",
			"app_name": escape_html(metadata.client_name),
			"scopes": metadata.scope or "all",
			"redirect_uris": redirect_uri,
			"default_redirect_uri": redirect_uri,
			"response_type": "Code",
			"grant_type": "Authorization Code",
			"skip_authorization": 0,
			"token_endpoint_auth_method": "None",
			"software_id": DYNAMIC_CLIENT_ID,
		}
	).insert(ignore_permissions=True)


def registration_response(client, metadata):
	body = {
		"client_id": client.client_id,
		"client_id_issued_at": client.client_id_issued_at(),
		"client_name": client.app_name,
		"redirect_uris": [client.redirect_uris],
		"grant_types": metadata.grant_types or ["authorization_code"],
		"response_types": ["code"],
		"token_endpoint_auth_method": "none",
		"scope": client.scopes,
	}
	return json_response(body, 201)


def oauth_error(error, description, status=400):
	return json_response({"error": error, "error_description": description}, status)


def cleanup_dynamic_clients():
	clients = frappe.get_all("OAuth Client", filters={"software_id": DYNAMIC_CLIENT_ID}, pluck="name")
	if not clients:
		return
	stale = add_to_date(now_datetime(), days=-STALE_DAYS)
	frappe.db.delete(
		"OAuth Bearer Token", {"client": ["in", clients], "status": "Revoked", "modified": ["<", stale]}
	)
	frappe.db.delete(
		"OAuth Bearer Token",
		{
			"client": ["in", clients],
			"creation": ["<", add_to_date(stale, days=-REFRESH_TOKEN_DAYS)],
		},
	)
	used = frappe.get_all(
		"OAuth Bearer Token", filters={"client": ["in", clients]}, pluck="client", distinct=True
	)
	unused = frappe.get_all(
		"OAuth Client",
		filters={
			"software_id": DYNAMIC_CLIENT_ID,
			"creation": ["<", stale],
			"name": ["not in", used or [""]],
		},
		pluck="name",
	)
	for name in unused:
		frappe.db.delete("OAuth Authorization Code", {"client": name})
		frappe.delete_doc("OAuth Client", name, ignore_permissions=True, force=True)
