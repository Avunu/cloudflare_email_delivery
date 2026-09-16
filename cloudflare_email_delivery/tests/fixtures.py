# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""Shared fixtures for the site-backed tests: a Cloudflare Email Domain, an incoming
and an outgoing Email Account on it, and a MIME builder."""

import time
from unittest.mock import patch

import frappe

from cloudflare_email_delivery import cloudflare_api, relay

DOMAIN = "cf-test.example"
INBOUND_EMAIL = f"support@{DOMAIN}"
OUTBOUND_EMAIL = f"notifications@{DOMAIN}"
INBOUND_ACCOUNT = "_Test CF Inbound"
DISABLED_ACCOUNT = "_Test CF Inbound Disabled"
OUTBOUND_ACCOUNT = "_Test CF Outbound"
ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
API_TOKEN = "cf-test-token"
RELAY_ID = "01JWRELAYID0000000000000000"


def ensure_domain():
	if frappe.db.exists("Email Domain", DOMAIN):
		return frappe.get_doc("Email Domain", DOMAIN)
	with patch.object(cloudflare_api, "verify_token", return_value={"status": "active"}):
		domain = frappe.get_doc(
			{
				"doctype": "Email Domain",
				"domain_name": DOMAIN,
				"email_id": INBOUND_EMAIL,
				"send_via_cloudflare": 1,
				"cf_account_id": ACCOUNT_ID,
				"cf_api_token": API_TOKEN,
			}
		).insert(ignore_permissions=True)
	return domain


def ensure_account(name, email_id, *, incoming=False, outgoing=False, **values):
	if frappe.db.exists("Email Account", name):
		return frappe.get_doc("Email Account", name)
	doc = frappe.get_doc(
		{
			"doctype": "Email Account",
			"email_account_name": name,
			"email_id": email_id,
			"domain": DOMAIN,
			"enable_incoming": int(incoming),
			"enable_outgoing": int(outgoing),
			"append_to": "ToDo" if incoming else None,
			"always_use_account_email_id_as_sender": int(outgoing),
			**values,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def ensure_fixtures():
	ensure_domain()
	inbound = ensure_account(INBOUND_ACCOUNT, INBOUND_EMAIL, incoming=True)
	ensure_account(DISABLED_ACCOUNT, f"closed@{DOMAIN}", incoming=False)
	ensure_account(OUTBOUND_ACCOUNT, OUTBOUND_EMAIL, outgoing=True, default_outgoing=1)
	frappe.db.commit()
	return frappe.get_doc("Email Account", inbound.name)


def remove_fixtures():
	for account in (INBOUND_ACCOUNT, DISABLED_ACCOUNT, OUTBOUND_ACCOUNT):
		for doctype in ("Communication", "Unhandled Email", "Email Queue"):
			for name in frappe.get_all(doctype, {"email_account": account}, pluck="name"):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.delete_doc("Email Account", account, force=True, ignore_permissions=True, ignore_missing=True)
	frappe.delete_doc("Email Domain", DOMAIN, force=True, ignore_permissions=True, ignore_missing=True)
	frappe.db.commit()


def mime(
	subject="Please call me",
	msg_id="<mime-0001@agrolait.example>",
	to=INBOUND_EMAIL,
	sender="Sylvie Lelitre <sylvie@agrolait.example>",
	extra="",
	body="Please call me as soon as possible this afternoon!",
) -> bytes:
	"""A small multipart/alternative message, CRLF-terminated like the wire."""
	lines = [
		f"From: {sender}",
		f"To: {to}",
		f"Subject: {subject}",
		"Date: Fri, 10 Aug 2012 14:16:26 +0000",
		f"Message-ID: {msg_id}" if msg_id else None,
		extra or None,
		"MIME-Version: 1.0",
		'Content-Type: multipart/alternative; boundary="----=_Part_4200734"',
		"",
		"------=_Part_4200734",
		"Content-Type: text/plain; charset=utf-8",
		"Content-Transfer-Encoding: 8bit",
		"",
		body,
		"",
		"------=_Part_4200734",
		"Content-Type: text/html; charset=utf-8",
		"Content-Transfer-Encoding: 8bit",
		"",
		f"<html><body><p>{body}</p></body></html>",
		"------=_Part_4200734--",
		"",
	]
	return "\r\n".join(line for line in lines if line is not None).encode()


def signed_headers(
	secret,
	body,
	*,
	timestamp=None,
	signature=None,
	envelope_from="sylvie@agrolait.example",
	envelope_to=INBOUND_EMAIL,
	relay_id=RELAY_ID,
	attempt=1,
	tenant="test",
):
	"""The relay's headers for one delivery; ``None`` for a value drops the header."""
	timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
	if signature is None:
		signature = relay.sign(secret, timestamp, body)
	headers = {
		relay.HEADER_ID: relay_id,
		relay.HEADER_TENANT: tenant,
		relay.HEADER_TIMESTAMP: timestamp,
		relay.HEADER_SIGNATURE: signature,
		relay.HEADER_ENVELOPE_FROM: envelope_from,
		relay.HEADER_ENVELOPE_TO: envelope_to,
		relay.HEADER_ATTEMPT: str(attempt),
	}
	return {k: v for k, v in headers.items() if v is not False}
