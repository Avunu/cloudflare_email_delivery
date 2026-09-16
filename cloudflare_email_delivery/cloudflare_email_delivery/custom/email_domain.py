# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""Email Domain: the Cloudflare account behind a domain, and the ``override_email_send``
hook that routes an Email Queue entry through the Cloudflare Email Sending API."""

import email
import email.policy
from email.utils import parseaddr
from typing import TYPE_CHECKING

import frappe
from frappe import _, safe_encode
from frappe.email.doctype.email_domain.email_domain import (
	EmailDomain as BaseEmailDomain,
)
from frappe.types import DF

from cloudflare_email_delivery import cloudflare_api
from cloudflare_email_delivery.cloudflare_api import CloudflareEmailError, CloudflareSendingSession


class EmailDomain(BaseEmailDomain):
	if TYPE_CHECKING:
		cf_account_id: DF.Data
		cf_api_token: DF.Password
		send_via_cloudflare: DF.Check

	def validate_incoming_server_conn(self):
		"""No IMAP/POP server: inbound mail arrives from the Cloudflare email relay."""
		if not self.send_via_cloudflare:
			return super().validate_incoming_server_conn()

	def validate_outgoing_server_conn(self):
		"""Verify the Cloudflare API token instead of attempting an SMTP connection."""
		if not self.send_via_cloudflare:
			return super().validate_outgoing_server_conn()

		api_token = self.get_password("cf_api_token", raise_exception=False)
		if not self.cf_account_id or not api_token:
			frappe.throw(
				_("Cloudflare Account ID and API Token are required when Send via Cloudflare is enabled.")
			)
		try:
			cloudflare_api.verify_token(api_token)
		except CloudflareEmailError as error:
			frappe.throw(
				_("Cloudflare API token validation failed: {0}").format(error),
				title=_("Outgoing email account not correct"),
			)


def get_cloudflare_settings(email_account) -> dict | None:
	"""``{"account_id", "api_token"}`` when the account's domain sends via Cloudflare,
	else ``None``. Never raises for an account that simply is not ours."""
	if email_account is None or not email_account.domain:
		return None
	domain = frappe.get_cached_doc("Email Domain", email_account.domain)
	if not domain.send_via_cloudflare:
		return None
	account_id = domain.cf_account_id
	api_token = domain.get_password("cf_api_token", raise_exception=False)
	if not account_id or not api_token:
		frappe.throw(
			_("Cloudflare Account ID and API Token must be configured on Email Domain {0}.").format(
				domain.name
			)
		)
	return {"account_id": account_id, "api_token": api_token}


def get_sending_session(settings: dict) -> CloudflareSendingSession:
	"""One session per account per request/job, so a batch shares a connection pool
	and a fatal error (401/403) fails the rest of the batch fast."""
	sessions = frappe.local.flags.setdefault("cloudflare_sending_sessions", {})
	session = sessions.get(settings["account_id"])
	if session is None:
		session = CloudflareSendingSession(settings["account_id"], settings["api_token"])
		sessions[settings["account_id"]] = session
	return session


def send_via_fallback(email_queue_doc, email_account, sender: str, recipient: str, message: bytes | str):
	"""What core would have done without the hook: Frappe Mail or SMTP."""
	if email_account.service == "Frappe Mail":
		email_account.get_frappe_mail_client().send_raw(
			sender=sender,
			recipients=recipient,
			message=message,
			is_newsletter=email_queue_doc.reference_doctype == "Newsletter",
		)
		return
	smtp_server = email_account.get_smtp_server()
	if smtp_server is None:
		frappe.throw(_("No SMTP server is configured for Email Account {0}.").format(email_account.name))
	smtp_server.session.sendmail(from_addr=sender, to_addrs=recipient, msg=safe_encode(message))


def send(email_queue_doc, sender: str, recipient: str, message: bytes | str):
	"""Frappe's ``override_email_send`` hook: one call per recipient of an Email Queue
	entry, with the complete RFC 5322 message core built for that recipient.

	Registered hooks receive every outgoing mail, so accounts that do not send
	via Cloudflare fall back to the transport core would have used.
	"""
	if frappe.in_test and not frappe.flags.testing_email:
		# Exactly what core does on its own path: nothing leaves a test run.
		frappe.flags.sent_mail = message
		return
	email_account = email_queue_doc.get_email_account(raise_error=True)
	settings = get_cloudflare_settings(email_account)
	if not settings:
		return send_via_fallback(email_queue_doc, email_account, sender, recipient, message)
	msg = email.message_from_bytes(safe_encode(message), policy=email.policy.default)
	# Frappe keeps the display name on the envelope recipient ("Bob <bob@example.com>");
	# the envelope wants the bare address, the header supplies the name.
	address = parseaddr(recipient)[1] or recipient
	get_sending_session(settings).send_message(msg, sender, [address])
