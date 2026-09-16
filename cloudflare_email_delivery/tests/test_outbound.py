# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The ``override_email_send`` hook end to end: ``frappe.sendmail`` through a Cloudflare
account yields one API call per recipient with the threading headers, and other
accounts fall back to what core would have used."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from cloudflare_email_delivery.cloudflare_api import CloudflareSendingSession
from cloudflare_email_delivery.cloudflare_email_delivery.custom import email_domain
from cloudflare_email_delivery.tests import fixtures
from cloudflare_email_delivery.tests.fixtures import OUTBOUND_EMAIL


class TestOutbound(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fixtures.ensure_fixtures()
		cls.addClassCleanup(fixtures.remove_fixtures)

	def setUp(self):
		super().setUp()
		self.posted = []
		self.addCleanup(self.purge_queue)

		def _post(session, body):
			import json

			payload = json.loads(body)
			self.posted.append(payload)
			addresses = [e["address"] for f in ("to", "cc", "bcc") for e in payload.get(f) or []]
			return {
				"delivered": addresses,
				"queued": [],
				"permanent_bounces": [],
				"suppressed_recipients": [],
				"message_id": "cf-1",
			}

		patcher = patch.object(CloudflareSendingSession, "_post", _post)
		patcher.start()
		self.addCleanup(patcher.stop)
		frappe.flags.testing_email = True
		self.addCleanup(setattr, frappe.flags, "testing_email", False)
		frappe.local.flags.pop("cloudflare_sending_sessions", None)

	def purge_queue(self):
		frappe.db.rollback()
		for name in frappe.get_all("Email Queue", {"sender": ("like", "%test.example%")}, pluck="name"):
			frappe.delete_doc("Email Queue", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def send(self, **kwargs):
		kwargs.setdefault("sender", OUTBOUND_EMAIL)
		kwargs.setdefault("subject", "CF outbound")
		kwargs.setdefault("message", "<p>Hello</p>")
		kwargs.setdefault("now", True)
		frappe.sendmail(**kwargs)
		# now=True defers the send to after_commit, exactly as core's own tests drive it.
		frappe.db.commit()

	def test_one_call_per_recipient_with_threading_headers(self):
		self.send(recipients=["alice@example.com", "Bob <bob@example.com>"], cc=["carol@example.com"])
		self.assertEqual(len(self.posted), 3)
		to = sorted(p["to"][0]["address"] for p in self.posted)
		self.assertEqual(to, ["alice@example.com", "bob@example.com", "carol@example.com"])
		for payload in self.posted:
			self.assertEqual(len(payload["to"]), 1)
			self.assertNotIn("cc", payload)
			self.assertNotIn("bcc", payload)
			self.assertEqual(payload["from"]["address"], OUTBOUND_EMAIL)
			self.assertIn("Hello", payload["html"])
			[queue] = frappe.get_all(
				"Email Queue",
				{"sender": ("like", f"%{OUTBOUND_EMAIL}%")},
				["message_id"],
				order_by="creation desc",
				limit=1,
			)
			self.assertTrue(payload["headers"]["References"].endswith(f"<{queue.message_id}>"))
			self.assertIn(frappe.local.site, payload["headers"]["X-Frappe-Site"])

	def test_inline_image_keeps_its_content_id(self):
		# Frappe's convention: <img embed="…"> plus the bytes in inline_images; it mints the cid.
		self.send(
			recipients=["alice@example.com"],
			message='<p><img embed="logo.png"></p>',
			inline_images=[{"filename": "logo.png", "filecontent": b"PNG"}],
		)
		[payload] = self.posted
		[attachment] = payload["attachments"]
		self.assertEqual((attachment["filename"], attachment["disposition"]), ("logo.png", "inline"))
		self.assertTrue(attachment["content_id"])
		self.assertIn(f"cid:{attachment['content_id']}", payload["html"])

	def test_other_accounts_fall_back(self):
		frappe.delete_doc(
			"Email Account", "_Test CF SMTP", force=True, ignore_permissions=True, ignore_missing=True
		)
		account = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": "_Test CF SMTP",
				"email_id": "smtp@smtp-test.example",
				"enable_outgoing": 1,
				"smtp_server": "localhost",
				"smtp_port": 2525,
				"no_smtp_authentication": 1,
				"awaiting_password": 1,
			}
		)
		account.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Email Account", account.name, force=True, ignore_permissions=True)
		sent = []
		with patch.object(email_domain, "send_via_fallback", side_effect=lambda *a, **k: sent.append(a)):
			self.send(sender="smtp@smtp-test.example", recipients=["alice@example.com"])
		self.assertEqual(len(sent), 1)
		self.assertEqual(sent[0][1].name, account.name)
		self.assertEqual(self.posted, [])

	def test_test_mode_short_circuit(self):
		frappe.flags.testing_email = False
		self.send(recipients=["alice@example.com"])
		self.assertEqual(self.posted, [])
		self.assertTrue(frappe.flags.sent_mail)
