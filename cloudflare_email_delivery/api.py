# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The inbound webhook the Cloudflare email relay delivers to, and the desk helpers
for the Email Account's webhook credentials.

``POST /api/method/cloudflare_email_delivery.api.inbound?key=<key>`` with the raw
RFC 5322 message as the body (``Content-Type: message/rfc822``), signed as the
relay's README describes. The status code is the contract with the relay's
retry queue: 2xx delivered, 401/413/422 permanent (parked as ``rejected``, no
retry), anything else retried with back-off. Nothing here ever raises out of
the request: an uncaught exception would become Frappe's 417/500 with a
traceback body, and 417 would be read as a permanent rejection.
"""

import email
import email.policy
from typing import cast

import frappe
from frappe import _
from frappe.email.receive import SentEmailInInboxError
from frappe.utils.password import set_encrypted_password

from cloudflare_email_delivery import relay
from cloudflare_email_delivery.cloudflare_email_delivery.custom.email_account import (
	CloudflareEmailAccount,
	new_token,
)
from cloudflare_email_delivery.inbound import RelayInboundMail
from cloudflare_email_delivery.relay_user import RELAY_USER, RelayUserMissing, acting_as_relay_user

INBOUND_METHOD = "cloudflare_email_delivery.api.inbound"
# Cloudflare Email Routing accepts messages up to 25 MiB; the relay prepends the envelope headers.
# The site's own request cap (max_file_size) applies first when it is lower.
MAX_MESSAGE_BYTES = 26 * 1024 * 1024
# The one answer for an unknown key and a bad signature: a probe can't tell which it got.
UNAUTHORIZED = "unauthorized"


def _respond(status: int, **values):
	"""The JSON body (Frappe wraps it as ``{"message": …}``) with ``status`` on the wire."""
	frappe.local.response.http_status_code = status
	return {"ok": status == 200, **values}


def _find_account(key: str | None) -> CloudflareEmailAccount | None:
	"""The enabled, Cloudflare-domain Email Account behind ``key``, or ``None``."""
	if not key:
		return None
	name = frappe.db.get_value("Email Account", {"cf_webhook_key": key, "enable_incoming": 1}, "name")
	if not name:
		return None
	# extend_doctype_class composes the controller at runtime; the cast names our layer.
	account = cast(CloudflareEmailAccount, frappe.get_doc("Email Account", name))
	if not account._is_cloudflare_domain():
		return None
	return account


def _owned_domains(account) -> set[str]:
	domains = set()
	if account.domain:
		domains.add(account.domain.lower())
	own = relay.recipient_domain(account.email_id)
	if own:
		domains.add(own)
	return domains


def store_unhandled_email(account, uid: str | None, raw: bytes, reason: str) -> None:
	"""``EmailAccount.handle_bad_emails`` without its ``use_imap`` gate: the relay's
	retries need the failed message on record whatever the account's transport."""
	try:
		raw_str = raw.decode("ASCII", "replace")
		message_id = email.message_from_string(raw_str).get("Message-ID")
	except Exception:
		raw_str = "can't be parsed"
		message_id = "can't be parsed"
	frappe.get_doc(
		{
			"doctype": "Unhandled Email",
			"raw": raw_str,
			"uid": uid or "",
			"reason": reason,
			"message_id": message_id,
			"email_account": account.name,
		}
	).insert(ignore_permissions=True)


def _deliver(account: CloudflareEmailAccount, body: bytes, relay_id: str | None):
	"""Create the Communication for a verified message; run as the relay user."""
	if relay_id:
		existing = frappe.db.get_value(
			"Communication", {"cf_relay_id": relay_id, "email_account": account.name}, "name"
		)
		if existing:
			return _respond(200, remote_ref=existing, id=relay_id)

	# Core deduplicates on Message-ID: a retry of a message it already has returns that Communication.
	mail = RelayInboundMail(body, account, uid=None, seen_status=None, append_to=account.append_to)
	try:
		communication = mail.process()
		if communication and mail.flags.is_new_communication and relay_id:
			communication.db_set("cf_relay_id", relay_id, update_modified=False)
		frappe.db.commit()
		if communication and mail.flags.is_new_communication:
			if account.enable_auto_reply:
				account.send_auto_reply(communication, mail)
			communication.send_email(is_inbound_mail_communcation=True)
		frappe.db.commit()
		return _respond(200, remote_ref=communication.name if communication else None, id=relay_id)
	except SentEmailInInboxError:
		# Our own outgoing mail looped back: deliberately ignored, never retried.
		frappe.db.rollback()
		return _respond(200, remote_ref=None, id=relay_id)
	except frappe.ValidationError as error:
		# The reference document refused the message: permanent.
		frappe.db.rollback()
		return _respond(422, error=str(error))
	except Exception:
		frappe.db.rollback()
		try:
			account.log_error(title="Cloudflare relay inbound")
			store_unhandled_email(account, relay_id, mail.raw_message, frappe.get_traceback())
		except Exception:
			frappe.db.rollback()
		else:
			frappe.db.commit()
		return _respond(500, error="internal error")


# Reviewed: a machine-to-machine webhook, so it has to accept guests. The HMAC over timestamp and body
# is the credential: POST only, size-capped before the body is read, and nothing is processed until
# the signature verifies (constant-time, ± SIGNATURE_TOLERANCE s replay window) against the account's
# secret. Processing then runs as the least-privilege relay user, never as Administrator.
@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def inbound(key: str | None = None):
	relay_id = frappe.get_request_header(relay.HEADER_ID) or None
	tenant = frappe.get_request_header(relay.HEADER_TENANT) or None
	logger = frappe.logger("cloudflare_email_delivery")

	length = frappe.request.content_length
	if length is not None and length > MAX_MESSAGE_BYTES:
		logger.warning(f"relay inbound {relay_id} (tenant {tenant}): {length} bytes is over the limit")
		return _respond(413, error="message too large")

	account = _find_account(key)
	if account is None:
		logger.warning(
			f"relay inbound {relay_id} (tenant {tenant}): no enabled account for key {str(key)[:8]}..."
		)
		return _respond(401, error=UNAUTHORIZED)

	body = frappe.request.get_data()
	if len(body) > MAX_MESSAGE_BYTES:
		return _respond(413, error="message too large")
	secret = account.get_password("cf_webhook_secret", raise_exception=False)
	if not relay.verify_signature(
		secret,
		frappe.get_request_header(relay.HEADER_TIMESTAMP),
		frappe.get_request_header(relay.HEADER_SIGNATURE),
		body,
	):
		logger.warning(f"relay inbound {relay_id}: signature verification failed on {account.name}")
		return _respond(401, error=UNAUTHORIZED)

	try:
		envelope_from = relay.check_envelope(
			"envelope-from", frappe.get_request_header(relay.HEADER_ENVELOPE_FROM)
		)
		envelope_to = relay.check_envelope("envelope-to", frappe.get_request_header(relay.HEADER_ENVELOPE_TO))
	except ValueError as error:
		return _respond(422, error=str(error))
	body = relay.prepend_envelope_headers(body, envelope_from, envelope_to)

	# Misrouting defence for a shared relay: the routed recipient must be ours.
	delivered_to = relay.first_header(body, "Delivered-To") or envelope_to
	domain = relay.recipient_domain(delivered_to)
	if domain and domain not in _owned_domains(account):
		return _respond(422, error="recipient domain does not belong to this account")

	try:
		with acting_as_relay_user():
			return _deliver(account, body, relay_id)
	except RelayUserMissing:
		# Not migrated yet: a 5xx, so the relay keeps the message and retries.
		frappe.log_error(title="Cloudflare relay inbound", message=f"{RELAY_USER} is missing: run migrate")
		frappe.db.commit()
		return _respond(503, error="not ready")


def _account_for_desk(email_account: str) -> CloudflareEmailAccount:
	frappe.only_for("System Manager")
	account = cast(CloudflareEmailAccount, frappe.get_doc("Email Account", email_account))
	account.check_permission("write")
	return account


@frappe.whitelist(methods=["POST"])
def get_webhook_secret(email_account: str) -> str:
	"""The account's webhook secret, for pasting into the relay fleet's onboarding."""
	account = _account_for_desk(email_account)
	return account.get_password("cf_webhook_secret", raise_exception=False) or ""


@frappe.whitelist(methods=["POST"])
def regenerate_webhook_secret(email_account: str) -> None:
	"""A new signing secret; the key (hence the URL) is left alone so only the tenant's
	relay secret needs updating."""
	account = _account_for_desk(email_account)
	if not account._is_cloudflare_domain():
		frappe.throw(_("{0} does not receive mail via Cloudflare.").format(account.name))
	token = new_token()
	set_encrypted_password("Email Account", account.name, token, "cf_webhook_secret")
	# The same placeholder core writes for a stored password.
	frappe.db.set_value(
		"Email Account", account.name, "cf_webhook_secret", "*" * len(token), update_modified=False
	)
	frappe.msgprint(
		_(
			"Webhook secret regenerated. Update this tenant's secret in the Cloudflare email relay: requests signed with the previous secret are rejected from now on."
		),
		indicator="orange",
	)
