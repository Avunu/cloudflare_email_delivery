# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The inbound webhook, called in-process with a fake request (the fast path) and
twice through the real WSGI app (the plumbing: guest POST, raw bytes, CSRF)."""

import inspect
import json
import re
from threading import Thread
from typing import cast
from unittest.mock import PropertyMock, patch

import frappe
from frappe.core.doctype.communication.communication import Communication
from frappe.tests import IntegrationTestCase
from frappe.utils import get_test_client, set_request
from werkzeug.sansio.request import Request
from werkzeug.test import TestResponse

from cloudflare_email_delivery import api, relay, relay_user
from cloudflare_email_delivery.cloudflare_email_delivery.custom.email_account import CloudflareEmailAccount
from cloudflare_email_delivery.inbound import RelayInboundMail
from cloudflare_email_delivery.relay_user import RELAY_USER
from cloudflare_email_delivery.tests import fixtures
from cloudflare_email_delivery.tests.fixtures import DISABLED_ACCOUNT, INBOUND_EMAIL, RELAY_ID, mime

PATH = "/api/method/cloudflare_email_delivery.api.inbound"
UNAUTHORIZED = {"ok": False, "error": "unauthorized"}


def mime_with_attachment(subject, msg_id) -> bytes:
	"""A multipart/mixed message with one text attachment."""
	lines = [
		"From: Sylvie Lelitre <sylvie@agrolait.example>",
		f"To: {INBOUND_EMAIL}",
		f"Subject: {subject}",
		"Date: Fri, 10 Aug 2012 14:16:26 +0000",
		f"Message-ID: {msg_id}",
		"MIME-Version: 1.0",
		'Content-Type: multipart/mixed; boundary="mixed"',
		"",
		"--mixed",
		"Content-Type: text/plain; charset=utf-8",
		"",
		"See the attached notes.",
		"--mixed",
		'Content-Type: text/plain; name="notes.txt"',
		'Content-Disposition: attachment; filename="notes.txt"',
		"Content-Transfer-Encoding: base64",
		"",
		"Q2FsbCBTeWx2aWUgYXQgMyBwbS4=",
		"--mixed--",
		"",
	]
	return "\r\n".join(lines).encode()


class TestInbound(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.account = fixtures.ensure_fixtures()
		cls.key = cls.account.cf_webhook_key
		cls.secret = cls.account.get_password("cf_webhook_secret")
		cls.addClassCleanup(fixtures.remove_fixtures)

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._seq = 0

	def tearDown(self):
		frappe.db.rollback()
		for doctype in ("Communication", "Unhandled Email", "ToDo"):
			filters = (
				{"email_account": self.account.name}
				if doctype != "ToDo"
				else {"description": ("like", "%CF inbound%")}
			)
			for name in frappe.get_all(doctype, filters, pluck="name"):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		# api.inbound commits what it receives, so removing it has to be committed too.
		frappe.db.commit()  # nosemgrep: frappe-nix-test-commit
		super().tearDown()

	# -- helpers ---------------------------------------------------------------

	def msg_id(self):
		self._seq += 1
		return f"<cf-inbound-{self.id()}-{self._seq}@agrolait.example>"

	def post(self, body=None, *, key=None, headers=None, **signed):
		"""Call the endpoint in-process; return ``(status, payload)``."""
		body = body if body is not None else mime(subject="CF inbound", msg_id=self.msg_id())
		key = self.key if key is None else key
		request_headers = fixtures.signed_headers(self.secret, body, **signed)
		if headers:
			for name, value in headers.items():
				if value is None:
					request_headers.pop(name, None)
				else:
					request_headers[name] = value
		set_request(
			method="POST",
			path=PATH,
			query_string={"key": key} if key is not False else {},
			data=body,
			content_type="message/rfc822",
			headers=list(request_headers.items()),
		)
		frappe.local.response = frappe._dict()
		# As in production: the relay's request is a guest session, and it must stay one.
		frappe.set_user("Guest")
		try:
			payload = api.inbound(key=key if key is not False else None)
			self.assertEqual(frappe.session.user, "Guest")
			self.assertEqual(frappe.session.sid, "Guest")
		finally:
			frappe.set_user("Administrator")
		status = frappe.local.response.get("http_status_code") or 200
		self.assertEqual(payload["ok"], status == 200, payload)
		return status, payload

	def communication(self, name):
		return frappe.get_doc("Communication", name)

	# -- delivered -------------------------------------------------------------

	def test_creates_record(self):
		body = mime(subject="CF inbound new", msg_id="<new@agrolait.example>")
		status, payload = self.post(body)
		self.assertEqual(status, 200)
		self.assertEqual(payload["id"], RELAY_ID)
		comm = self.communication(payload["remote_ref"])
		self.assertEqual(comm.subject, "CF inbound new")
		self.assertEqual(comm.sent_or_received, "Received")
		self.assertEqual(comm.email_account, self.account.name)
		self.assertEqual(comm.message_id, "new@agrolait.example")
		self.assertEqual(comm.cf_relay_id, RELAY_ID)
		self.assertEqual(comm.reference_doctype, "ToDo")
		self.assertIn("Please call me", comm.content)
		self.assertEqual(frappe.get_value("ToDo", comm.reference_name, "description"), "CF inbound new")
		self.assertFalse(frappe.get_all("Unhandled Email", {"email_account": self.account.name}))
		# Processed as the least-privilege relay user, never as Administrator.
		self.assertEqual(comm.owner, RELAY_USER)
		self.assertEqual(comm.modified_by, RELAY_USER)
		self.assertEqual(frappe.get_value("ToDo", comm.reference_name, "owner"), RELAY_USER)

	def test_attachments_are_saved_as_the_relay_user(self):
		body = mime_with_attachment(subject="CF inbound attached", msg_id=self.msg_id())
		status, payload = self.post(body)
		self.assertEqual(status, 200)
		comm = self.communication(payload["remote_ref"])
		[attachment] = frappe.get_all(
			"File",
			{"attached_to_doctype": "Communication", "attached_to_name": comm.name},
			["file_name", "owner", "is_private"],
		)
		self.assertEqual(attachment.file_name, "notes.txt")
		self.assertEqual(attachment.owner, RELAY_USER)
		self.assertTrue(attachment.is_private)
		self.assertEqual(comm.owner, RELAY_USER)

	def test_reply_threads(self):
		first_id = "<thread-root@agrolait.example>"
		_status, first = self.post(mime(subject="CF inbound thread", msg_id=first_id))
		root = self.communication(first["remote_ref"])
		_status, second = self.post(
			mime(
				subject="Re: CF inbound thread",
				msg_id="<thread-reply@agrolait.example>",
				extra=f"In-Reply-To: {first_id}",
			),
			relay_id="01JWRELAYID0000000000000001",
		)
		reply = self.communication(second["remote_ref"])
		self.assertEqual(reply.in_reply_to, root.name)
		self.assertEqual(
			(reply.reference_doctype, reply.reference_name), (root.reference_doctype, root.reference_name)
		)

	def test_routes_by_delivered_to(self):
		# The header names someone else; the envelope (Delivered-To) is ours.
		body = mime(subject="CF inbound list", msg_id=self.msg_id(), to="list@lists.example")
		status, _payload = self.post(body, envelope_to=INBOUND_EMAIL)
		self.assertEqual(status, 200)

	def test_rejects_a_recipient_domain_that_is_not_ours(self):
		body = mime(subject="CF inbound stray", msg_id=self.msg_id(), to="x@other.example")
		status, payload = self.post(body, envelope_to="x@other.example")
		self.assertEqual(status, 422)
		self.assertIn("domain", payload["error"])
		self.assertFalse(frappe.get_all("Communication", {"email_account": self.account.name}))

	def test_duplicate_message_id_is_delivered_once(self):
		body = mime(subject="CF inbound twice", msg_id="<twice@agrolait.example>")
		_status, first = self.post(body)
		status, second = self.post(body, relay_id="01JWRELAYID0000000000000002", attempt=2)
		self.assertEqual(status, 200)
		self.assertEqual(second["remote_ref"], first["remote_ref"])
		self.assertEqual(len(frappe.get_all("Communication", {"email_account": self.account.name})), 1)

	def test_duplicate_relay_id_without_message_id(self):
		body = mime(subject="CF inbound no id", msg_id=None)
		_status, first = self.post(body)
		status, second = self.post(body, attempt=2)
		self.assertEqual(status, 200)
		self.assertEqual(second["remote_ref"], first["remote_ref"])
		self.assertEqual(len(frappe.get_all("Communication", {"email_account": self.account.name})), 1)

	def test_self_sent_mail_is_ignored(self):
		body = mime(subject="CF inbound self", msg_id=self.msg_id(), sender=INBOUND_EMAIL)
		status, payload = self.post(body, envelope_from=INBOUND_EMAIL)
		self.assertEqual(status, 200)
		self.assertIsNone(payload["remote_ref"])
		self.assertFalse(frappe.get_all("Communication", {"email_account": self.account.name}))

	# -- 401 -------------------------------------------------------------------

	def test_bad_signature(self):
		body = mime(subject="CF inbound sig", msg_id=self.msg_id())
		good = relay.sign(self.secret, "1700000000", body)
		for signature in (
			"v1=" + "0" * 64,
			good.replace("v1=", "v2="),
			good.partition("=")[2],
			relay.sign("other-secret-of-sufficient-length", "1700000000", body),
			relay.sign(self.secret, "1700000000", body + b"x"),
			relay.sign(self.secret, "1700000001", body),
		):
			status, payload = self.post(body, timestamp="1700000000", signature=signature)
			self.assertEqual(status, 401, signature)
			self.assertEqual(payload, UNAUTHORIZED)
		with patch("cloudflare_email_delivery.relay.time.time", return_value=1700000000):
			status, _payload = self.post(body, timestamp="1700000000", signature=good)
		self.assertEqual(status, 200)

	def test_stale_timestamp(self):
		body = mime(subject="CF inbound stale", msg_id=self.msg_id())
		with patch("cloudflare_email_delivery.relay.time.time", return_value=1700000000):
			for timestamp in ("1699999699", "1700000301", "1699900000", "0"):
				status, _payload = self.post(body, timestamp=timestamp)
				self.assertEqual(status, 401, timestamp)
			status, _payload = self.post(body, timestamp="1699999705")
		self.assertEqual(status, 200)

	def test_missing_headers(self):
		body = mime(subject="CF inbound missing", msg_id=self.msg_id())
		self.assertEqual(self.post(body, headers={relay.HEADER_SIGNATURE: None})[0], 401)
		self.assertEqual(self.post(body, headers={relay.HEADER_TIMESTAMP: None})[0], 401)
		self.assertEqual(self.post(body, headers={relay.HEADER_SIGNATURE: ""})[0], 401)

	def test_replayed_request_outside_the_window(self):
		# A captured, validly signed request is refused once its timestamp is stale.
		body = mime(subject="CF inbound replay", msg_id=self.msg_id())
		signed_at = 1700000000
		signature = relay.sign(self.secret, signed_at, body)
		with patch("cloudflare_email_delivery.relay.time.time", return_value=signed_at):
			self.assertEqual(self.post(body, timestamp=signed_at, signature=signature)[0], 200)
		late = signed_at + relay.SIGNATURE_TOLERANCE + 1
		with patch("cloudflare_email_delivery.relay.time.time", return_value=late):
			status, payload = self.post(body, timestamp=signed_at, signature=signature)
		self.assertEqual((status, payload), (401, UNAUTHORIZED))

	def test_unknown_key(self):
		# The same answer as a bad signature: nothing tells a probe which check failed.
		body = mime(subject="CF inbound key", msg_id=self.msg_id())
		for key in ("not-a-key", self.key[:-1], "", False):
			status, payload = self.post(body, key=key)
			self.assertEqual(status, 401, key)
			self.assertEqual(payload, UNAUTHORIZED)

	def test_account_not_receiving(self):
		disabled = cast(CloudflareEmailAccount, frappe.get_doc("Email Account", DISABLED_ACCOUNT))
		body = mime(subject="CF inbound closed", msg_id=self.msg_id())
		# A key exists but incoming is off.
		self.assertTrue(disabled.cf_webhook_key)
		status, _payload = self.post(body, key=disabled.cf_webhook_key)
		self.assertEqual(status, 401)

	def test_form_help_lists_the_codes_the_endpoint_answers(self):
		# The help under the webhook fields on the Email Account form once said an unknown
		# key or a disabled account got a 404; both get the 401 above.
		help_html = frappe.db.get_value(
			"Custom Field", {"dt": "Email Account", "fieldname": "cf_webhook_help"}, "options"
		)
		answers = help_html.split("Answers:", 1)[1]
		answered = {int(code) for code in re.findall(r"_respond\((\d{3})", inspect.getsource(api))}
		self.assertEqual({int(code) for code in re.findall(r"\b\d{3}\b", answers)}, answered)
		self.assertIn("401 unknown key, incoming disabled or bad signature", answers)

	# -- 413 / 503 -------------------------------------------------------------

	def test_oversized_message(self):
		body = mime(subject="CF inbound big", msg_id=self.msg_id())
		with patch.object(api, "MAX_MESSAGE_BYTES", len(body) - 1):
			status, payload = self.post(body)
			self.assertEqual((status, payload["error"]), (413, "message too large"))
			# Refused before the key is even looked up.
			status, _payload = self.post(body, key="not-a-key")
			self.assertEqual(status, 413)
		self.assertFalse(frappe.get_all("Communication", {"email_account": self.account.name}))

	def test_oversized_body_without_content_length(self):
		body = mime(subject="CF inbound chunked", msg_id=self.msg_id())
		with (
			patch.object(api, "MAX_MESSAGE_BYTES", len(body) - 1),
			patch.object(Request, "content_length", new_callable=PropertyMock, return_value=None),
		):
			status, _payload = self.post(body)
		self.assertEqual(status, 413)

	def test_relay_user_missing_is_retried(self):
		body = mime(subject="CF inbound unmigrated", msg_id=self.msg_id())
		with patch.object(relay_user, "RELAY_USER", "missing-relay@relay.invalid"):
			status, payload = self.post(body)
		self.assertEqual((status, payload["error"]), (503, "not ready"))
		self.assertFalse(frappe.get_all("Communication", {"email_account": self.account.name}))

	# -- 422 / 500 -------------------------------------------------------------

	def test_unroutable(self):
		body = mime(subject="CF inbound refused", msg_id=self.msg_id())
		with patch.object(
			RelayInboundMail,
			"_build_communication_doc",
			side_effect=frappe.ValidationError("Append To refused it"),
		):
			status, payload = self.post(body)
		self.assertEqual(status, 422)
		self.assertIn("refused", payload["error"])
		self.assertFalse(frappe.get_all("Communication", {"email_account": self.account.name}))

	def test_internal_error_then_retry(self):
		body = mime(subject="CF inbound boom", msg_id="<boom@agrolait.example>")
		with patch.object(
			RelayInboundMail, "_build_communication_doc", side_effect=RuntimeError("secret detail")
		):
			status, payload = self.post(body)
		self.assertEqual(status, 500)
		self.assertEqual(payload, {"ok": False, "error": "internal error"})
		[unhandled] = frappe.get_all(
			"Unhandled Email", {"email_account": self.account.name}, ["uid", "message_id"]
		)
		self.assertEqual(unhandled.uid, RELAY_ID)
		self.assertEqual(unhandled.message_id, "<boom@agrolait.example>")
		status, payload = self.post(body, attempt=2)
		self.assertEqual(status, 200)
		self.assertEqual(self.communication(payload["remote_ref"]).message_id, "boom@agrolait.example")

	def test_control_characters_in_envelope(self):
		body = mime(subject="CF inbound ctl", msg_id=self.msg_id())
		status, payload = self.post(body, envelope_to="x@cf-test.example%0d%0aBcc: y@z")
		self.assertEqual(status, 422)
		self.assertIn("control characters", payload["error"])


class TestInboundWsgi(IntegrationTestCase):
	"""Through the real WSGI app: a guest POST with a raw body passes CSRF and the
	response is Frappe's ``{"message": …}`` envelope with the status on the wire."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.account = fixtures.ensure_fixtures()
		cls.key = cls.account.cf_webhook_key
		cls.secret = cls.account.get_password("cf_webhook_secret")
		cls.addClassCleanup(fixtures.remove_fixtures)

	def tearDown(self):
		frappe.db.rollback()
		for name in frappe.get_all("Communication", {"email_account": self.account.name}, pluck="name"):
			frappe.delete_doc("Communication", name, force=True, ignore_permissions=True)
		# The request committed on its own connection, so removing its rows has to be committed too.
		frappe.db.commit()  # nosemgrep: frappe-nix-test-commit
		super().tearDown()

	def request(self, method, **kwargs) -> TestResponse:
		"""One request through the WSGI app, on a thread of its own as frappe's API tests
		do: the app's init/destroy would otherwise tear down this thread's site."""
		site = frappe.local.site
		responses: list[TestResponse] = []

		def run():
			with patch("frappe.app.get_site_name", return_value=site):
				client = get_test_client(use_cookies=False)
				responses.append(client.open(method=method, path=PATH, **kwargs))

		thread = Thread(target=run)
		thread.start()
		thread.join()
		return responses[0]

	def test_guest_post_with_raw_body(self):
		body = mime(subject="CF inbound wsgi", msg_id="<wsgi@agrolait.example>", body="8-bit body: café")
		headers = fixtures.signed_headers(self.secret, body)
		response = self.request(
			"POST", query_string={"key": self.key}, data=body, content_type="message/rfc822", headers=headers
		)
		self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
		payload = json.loads(response.get_data(as_text=True))["message"]
		self.assertTrue(payload["ok"])
		self.assertEqual(payload["id"], RELAY_ID)
		frappe.db.rollback()
		comm = cast(Communication, frappe.get_doc("Communication", payload["remote_ref"]))
		self.assertIn("café", comm.text_content or "")

	def test_bad_signature_status_on_the_wire(self):
		body = mime(subject="CF inbound wsgi 401", msg_id="<wsgi-401@agrolait.example>")
		headers = fixtures.signed_headers("wrong-secret-of-sufficient-length", body)
		response = self.request(
			"POST", query_string={"key": self.key}, data=body, content_type="message/rfc822", headers=headers
		)
		self.assertEqual(response.status_code, 401)
		self.assertEqual(json.loads(response.get_data(as_text=True))["message"], UNAUTHORIZED)

	def test_get_is_refused(self):
		response = self.request("GET", query_string={"key": self.key})
		self.assertEqual(response.status_code, 403)
