# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""A reply to mail sent through Cloudflare threads by ``References``, because the
``In-Reply-To`` it carries names the Message-ID Cloudflare substituted."""

import frappe
from frappe.email.receive import InboundMail
from frappe.tests import IntegrationTestCase

from cloudflare_email_delivery.inbound import RelayInboundMail
from cloudflare_email_delivery.tests import fixtures
from cloudflare_email_delivery.tests.fixtures import OUTBOUND_EMAIL, mime


class TestThreading(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.account = fixtures.ensure_fixtures()
		cls.addClassCleanup(fixtures.remove_fixtures)

	def setUp(self):
		super().setUp()
		self.event = frappe.get_doc(
			{"doctype": "Event", "subject": "CF threading event", "starts_on": "2026-01-01 10:00:00"}
		).insert()
		# An outgoing mail about the Event, as Frappe queues it (Communication + Email Queue).
		comm = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"subject": "About the event",
				"content": "<p>Hello</p>",
				"sender": OUTBOUND_EMAIL,
				"recipients": "alice@example.com",
				"reference_doctype": "Event",
				"reference_name": self.event.name,
			}
		).insert(ignore_permissions=True)
		frappe.flags.testing_email = False
		comm.send_email()
		self.comm = comm.reload()
		[queue_name] = frappe.get_all("Email Queue", {"communication": comm.name}, pluck="name")
		self.queue = frappe.get_doc("Email Queue", queue_name)
		self.assertTrue(self.queue.message_id)
		self.assertIn(frappe.local.site, self.queue.message_id)

	def reply(self, in_reply_to, references):
		extra = f"In-Reply-To: {in_reply_to}\r\nReferences: {references}"
		return mime(
			subject="Re: About the event",
			msg_id="<reply-1@example.com>",
			sender="Alice <alice@example.com>",
			extra=extra,
		)

	def test_reply_threads_through_references(self):
		# What a client sends back: In-Reply-To is Cloudflare's id, References carries ours.
		raw = self.reply(
			"<cloudflare-substituted@cf-bounce.example>",
			f"<cloudflare-substituted@cf-bounce.example> <{self.queue.message_id}>",
		)
		mail = RelayInboundMail(raw, self.account, append_to=self.account.append_to)
		self.assertEqual(mail.in_reply_to, self.queue.message_id)
		self.assertTrue(mail.is_reply_to_system_sent_mail())
		self.assertEqual(mail.parent_email_queue().name, self.queue.name)
		self.assertEqual(mail.parent_communication().name, self.comm.name)
		self.assertEqual(
			(mail.reference_document().doctype, mail.reference_document().name), ("Event", self.event.name)
		)

	def test_stock_inbound_mail_does_not(self):
		raw = self.reply("<cloudflare-substituted@cf-bounce.example>", f"<{self.queue.message_id}>")
		mail = InboundMail(raw, self.account, append_to=self.account.append_to)
		self.assertEqual(mail.in_reply_to, "cloudflare-substituted@cf-bounce.example")
		self.assertFalse(mail.parent_email_queue())

	def test_in_reply_to_still_wins_when_known(self):
		raw = self.reply(f"<{self.queue.message_id}>", "<unrelated@example.com>")
		mail = RelayInboundMail(raw, self.account)
		self.assertEqual(mail.in_reply_to, self.queue.message_id)

	def test_unknown_ids_fall_back_to_the_first(self):
		raw = self.reply("<foreign@example.com>", "<older@example.com> <foreign@example.com>")
		mail = RelayInboundMail(raw, self.account)
		self.assertEqual(mail.in_reply_to, "foreign@example.com")
		self.assertTrue(mail.is_reply())
		raw = mime(subject="Fresh", msg_id="<fresh@example.com>")
		self.assertEqual(RelayInboundMail(raw, self.account).in_reply_to, "")

	def test_known_communication_id_in_references(self):
		# An id this site never issued but stored (mail received earlier) is known too.
		earlier = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Received",
				"subject": "Earlier",
				"content": "<p>Earlier</p>",
				"sender": "alice@example.com",
				"email_account": self.account.name,
				"message_id": "known@elsewhere.example",
			}
		).insert(ignore_permissions=True)
		raw = self.reply("<x@y>", "<known@elsewhere.example>")
		mail = RelayInboundMail(raw, self.account)
		self.assertEqual(mail.in_reply_to, "known@elsewhere.example")
		self.assertEqual(mail.parent_communication().name, earlier.name)
