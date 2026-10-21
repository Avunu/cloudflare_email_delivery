# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

"""Demo data for frappe-demo and the screenshots in docs/screenshots/.

One Email Domain that sends via Cloudflare, one Email Account on it that also
receives from the relay (Append To ToDo), and a short support conversation fed
through the same inbound path the webhook uses: a customer's message, the
reply sent from the desk, and the customer's answer, which threads onto the
same ToDo through ``References`` although its ``In-Reply-To`` names the id
Cloudflare gave the reply.

Nothing reaches Cloudflare: the token check is mocked, no mail is sent, and the
webhook credentials come from the demo seed so the screenshots stay the same.
"""

import random
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any, cast
from unittest.mock import patch

import frappe

from cloudflare_email_delivery import cloudflare_api
from cloudflare_email_delivery.cloudflare_email_delivery.custom.email_account import CloudflareEmailAccount
from cloudflare_email_delivery.inbound import RelayInboundMail
from cloudflare_email_delivery.relay_user import acting_as_relay_user, ensure_relay_user

DOMAIN = "example.com"
ACCOUNT = "Support"
ADDRESS = f"support@{DOMAIN}"
# A made-up Cloudflare account id and token: the token check is mocked below.
CF_ACCOUNT_ID = "3f9a2c71d8e04b6a9c15e7d2b8f40a63"
CF_API_TOKEN = "demo-token-not-a-real-cloudflare-credential"
# The site's public address, as host_name would give it in production.
HOST_NAME = "https://erp.example.com"

CUSTOMER = "Dana Reyes <dana.reyes@example.org>"
SUBJECT = "Order 1042 arrived with a damaged lid"
FIRST_ID = "<CAF3k9-order-1042@mail.example.org>"
ANSWER_ID = "<CAF3k9-order-1042-2@mail.example.org>"
# The Message-ID Cloudflare Email Sending gave our reply in place of Frappe's own.
CLOUDFLARE_ID = "<0100019a2c-7d1e-4b3f@email.cloudflare.net>"


@contextmanager
def _host_name(url: str):
	"""``get_url()`` reads ``host_name`` from the site config, and adds the dev server's port
	outside production mode; give it a production site's values for this process only."""
	overrides = {"host_name": url, "restart_supervisor_on_update": 1}
	previous = {key: frappe.local.conf.get(key) for key in overrides}
	frappe.local.conf.update(overrides)
	try:
		yield
	finally:
		frappe.local.conf.update(previous)


def _at(day: date, hour: int, minute: int) -> datetime:
	return datetime.combine(day, time(hour, minute))


def _message(day: date, hour: int, minute: int, message_id: str, body: str, **headers: str) -> bytes:
	msg = EmailMessage()
	msg["From"] = CUSTOMER
	msg["To"] = ADDRESS
	msg["Date"] = format_datetime(_at(day, hour, minute).astimezone())
	msg["Message-ID"] = message_id
	for name, value in headers.items():
		msg[name.replace("_", "-")] = value
	msg.set_content(body)
	return msg.as_bytes()


def _receive(account: Any, raw: bytes) -> Any:
	"""What ``api.inbound`` does once the signature verifies, without its commits."""
	with acting_as_relay_user():
		return RelayInboundMail(raw, account, append_to=account.append_to).process()


def _setup_domain() -> None:
	if frappe.db.exists("Email Domain", DOMAIN):
		return
	domain = frappe.get_doc(
		{
			"doctype": "Email Domain",
			"domain_name": DOMAIN,
			"send_via_cloudflare": 1,
			"cf_account_id": CF_ACCOUNT_ID,
			"cf_api_token": CF_API_TOKEN,
		}
	)
	with patch.object(cloudflare_api, "verify_token"):
		domain.insert(ignore_permissions=True)


def _setup_account(seed: int) -> CloudflareEmailAccount:
	if frappe.db.exists("Email Account", ACCOUNT):
		return cast(CloudflareEmailAccount, frappe.get_doc("Email Account", ACCOUNT))
	rng = random.Random(seed)
	account = cast(
		CloudflareEmailAccount,
		frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": ACCOUNT,
				"email_id": ADDRESS,
				"domain": DOMAIN,
				"enable_outgoing": 1,
				"enable_incoming": 1,
				"append_to": "ToDo",
				# Seeded instead of secrets.token_urlsafe, so the webhook URL is the same in every shot.
				"cf_webhook_key": rng.randbytes(32).hex()[:43],
				"cf_webhook_secret": rng.randbytes(32).hex()[:43],
			}
		),
	)
	with _host_name(HOST_NAME):
		account.insert(ignore_permissions=True)
		if not account.cf_webhook_url:
			# A frappe-nix dev bench's mail guard disarms the controller's validate(), which
			# builds the URL in production; run that one step of it here.
			account.set_webhook_url()
			account.db_set("cf_webhook_url", account.cf_webhook_url, update_modified=False)
	return account


def _setup_conversation(account: Any, today: date) -> None:
	if frappe.db.exists("Communication", {"message_id": FIRST_ID.strip("<>")}):
		return
	day = today - timedelta(days=1)
	first = _receive(
		account,
		_message(
			day,
			9,
			12,
			FIRST_ID,
			"Hello,\n\nOrder 1042 arrived this morning, but the lid of the large storage box is cracked."
			" Could you send a replacement?\n\nThanks,\nDana",
			Subject=SUBJECT,
		),
	)

	# The reply sent from the desk: Frappe's own Message-ID, which Cloudflare replaces on the wire.
	reply_id = f"{frappe.generate_hash(length=10)}@{frappe.local.site}"
	frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"sent_or_received": "Sent",
			"email_status": "Open",
			"subject": f"Re: {SUBJECT}",
			"sender": ADDRESS,
			"recipients": CUSTOMER,
			"content": "<p>Hi Dana,</p><p>Sorry about that. A new lid ships today; the tracking number"
			" follows in a separate e-mail.</p><p>Support</p>",
			"communication_date": _at(day, 10, 5),
			"email_account": account.name,
			"reference_doctype": first.reference_doctype,
			"reference_name": first.reference_name,
			"in_reply_to": first.name,
			"message_id": reply_id,
		}
	).insert(ignore_permissions=True)

	_receive(
		account,
		_message(
			today,
			8,
			47,
			ANSWER_ID,
			"The new lid arrived and fits. Thank you for the quick help!\n\nDana",
			Subject=f"Re: {SUBJECT}",
			In_Reply_To=CLOUDFLARE_ID,
			References=f"{FIRST_ID} <{reply_id}>",
		),
	)


def setup(ctx: dict[str, Any]) -> None:
	"""Create the demo once; ``ctx["today"]`` dates the conversation and ``ctx["seed"]`` the webhook key."""
	ensure_relay_user()
	_setup_domain()
	account = _setup_account(int(ctx.get("seed") or 1))
	_setup_conversation(account, ctx["today"])
