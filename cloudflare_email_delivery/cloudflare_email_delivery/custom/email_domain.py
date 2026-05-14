# Copyright (c) 2026, Avunu LLC

import json
from email import policy
from email.parser import Parser
from typing import TYPE_CHECKING, cast

import frappe
import requests
from frappe import _
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.email.doctype.email_domain.email_domain import (
	EmailDomain as BaseEmailDomain,
)
from frappe.types import DF


class EmailDomain(BaseEmailDomain):
	if TYPE_CHECKING:
		cf_account_id: DF.Data
		cf_api_token: DF.Password
		send_via_cloudflare: DF.Check

	def validate_incoming_server_conn(self):
		"""Skip incoming server validation when send_via_cloudflare is enabled,
		since the domain may be used for outgoing only."""
		if not self.send_via_cloudflare:
			return super().validate_incoming_server_conn()

	def validate_outgoing_server_conn(self):
		"""If send_via_cloudflare is enabled, verify the Cloudflare API token
		instead of attempting an SMTP connection."""
		if not self.send_via_cloudflare:
			return super().validate_outgoing_server_conn()

		account_id = self.cf_account_id
		api_token = self.get_password("cf_api_token")

		if not account_id or not api_token:
			frappe.throw(
				_("Cloudflare Account ID and API Token are required when Send via Cloudflare is enabled.")
			)

		response = requests.get(
			"https://api.cloudflare.com/client/v4/user/tokens/verify",
			headers={"Authorization": f"Bearer {api_token}"},
			timeout=15,
		)

		data = response.json()
		if response.status_code != 200 or not data.get("success"):
			errors = data.get("errors", [])
			error_msg = "; ".join(e.get("message", "") for e in errors) if errors else response.text
			frappe.throw(
				_("Cloudflare API token validation failed: {0}").format(error_msg),
				title=_("Outgoing email account not correct"),
			)


def get_cloudflare_settings(sender: str) -> dict | None:
	"""Get Cloudflare settings from the Email Domain linked to the sender's email account.

	Returns None if Cloudflare sending is not enabled for this domain.
	"""
	email_account = cast(
		EmailAccount,
		frappe.get_doc("Email Account", {"email_id": sender, "enable_outgoing": 1}),  # type: ignore
	)
	domain_name = email_account.domain

	if not domain_name:
		return None

	domain = cast(EmailDomain, frappe.get_doc("Email Domain", domain_name))

	if not domain.send_via_cloudflare:
		return None

	account_id = domain.cf_account_id
	api_token = domain.get_password("cf_api_token")

	if not account_id or not api_token:
		frappe.throw("Cloudflare Account ID and API Token must be configured on the Email Domain.")

	return {"account_id": account_id, "api_token": api_token}


def send_via_smtp(email_queue_doc, sender: str, recipient: str, message: str):
	"""Fall back to Frappe's default SMTP sending."""
	email_account_doc = email_queue_doc.get_email_account(raise_error=True)
	smtp_server = email_account_doc.get_smtp_server()
	msg_bytes = message.encode("utf-8") if isinstance(message, str) else message
	smtp_server.session.sendmail(
		from_addr=sender,
		to_addrs=recipient,
		msg=msg_bytes,
	)


def send(email_queue_doc, sender: str, recipient: str, message: str):
	"""Send email via Cloudflare Email Sending API, falling back to SMTP if not enabled.

	This function is called by Frappe's override_email_send hook.
	The message parameter is a complete RFC 2822 email message.
	"""
	settings = get_cloudflare_settings(sender)

	if not settings:
		return send_via_smtp(email_queue_doc, sender, recipient, message)

	parsed = Parser(policy=policy.default).parsestr(message)

	payload = {
		"from": sender,
		"to": [recipient],
		"subject": parsed["Subject"] or "No Subject",
	}

	# Extract body content
	if parsed.is_multipart():
		for part in parsed.walk():
			content_type = part.get_content_type()
			if content_type == "text/plain" and "text" not in payload:
				payload["text"] = part.get_content()
			elif content_type == "text/html" and "html" not in payload:
				payload["html"] = part.get_content()
	else:
		content_type = parsed.get_content_type()
		content = parsed.get_content()
		if content_type == "text/html":
			payload["html"] = content
		else:
			payload["text"] = content

	# Extract Reply-To
	if reply_to := parsed["Reply-To"]:
		payload["reply_to"] = reply_to

	# Extract CC
	if cc := parsed["Cc"]:
		payload["cc"] = [addr.strip() for addr in cc.split(",")]

	# Extract attachments
	if parsed.is_multipart():
		attachments = []
		for part in parsed.walk():
			disposition = part.get_content_disposition()
			if disposition in ("attachment", "inline"):
				content = part.get_payload(decode=True)
				if content:
					import base64

					if isinstance(content, str):
						content = content.encode("utf-8")
					elif not isinstance(content, (bytes, bytearray, memoryview)):
						content = bytes(content)
					attachments.append(
						{
							"filename": part.get_filename() or "attachment",
							"content": base64.b64encode(content).decode(),
							"type": part.get_content_type(),
							"disposition": disposition,
						}
					)
		if attachments:
			payload["attachments"] = attachments

	response = requests.post(
		f"https://api.cloudflare.com/client/v4/accounts/{settings['account_id']}/email/sending/send",
		headers={
			"Authorization": f"Bearer {settings['api_token']}",
			"Content-Type": "application/json",
		},
		json=payload,
		timeout=30,
	)

	if response.status_code != 200:
		try:
			error_data = response.json()
			errors = error_data.get("errors", [])
			error_msg = "; ".join(e.get("message", "") for e in errors) if errors else response.text
		except (json.JSONDecodeError, KeyError):
			error_msg = response.text
		frappe.throw(f"Cloudflare API error {response.status_code}: {error_msg}", requests.HTTPError)
