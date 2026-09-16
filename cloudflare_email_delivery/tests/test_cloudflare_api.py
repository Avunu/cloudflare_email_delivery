# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""``cloudflare_api`` on its own: MIME -> JSON mapping and the HTTP session.
Pure ``unittest`` with a scripted ``requests.Session`` — no site, no network."""

import base64
import email
import email.policy
import json
import unittest
from email.message import EmailMessage
from unittest.mock import patch

import requests
from requests.structures import CaseInsensitiveDict

from cloudflare_email_delivery import cloudflare_api
from cloudflare_email_delivery.cloudflare_api import (
	MAX_HEADER_VALUE_BYTES,
	MAX_RECIPIENTS,
	SEND_URL,
	VERIFY_URL,
	CloudflareEmailError,
	CloudflareSendingSession,
	build_payload,
	verify_token,
)

ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
API_TOKEN = "cf-test-token"
SITE_ID = "abc123@site.example"

INNER_EML = (
	b"From: Forwarded <fwd@example.com>\r\n"
	b"To: someone@example.com\r\n"
	b"Subject: Inner subject\r\n"
	b"Message-ID: <inner@example.com>\r\n"
	b"Content-Type: text/plain; charset=utf-8\r\n"
	b"\r\n"
	b"Inner body\r\n"
)


def build(
	to="Alice <alice@example.com>",
	cc=None,
	text="Hello world",
	html="<p>Hello <b>world</b></p>",
	headers=None,
	attachments=(),
	message_id=f"<{SITE_ID}>",
):
	"""A message the way Frappe's EmailQueue builds one: alternative text+html, then attachments."""
	message = EmailMessage()
	message["From"] = "Sender <sender@cf.example.com>"
	message["To"] = to
	if cc:
		message["Cc"] = cc
	message["Subject"] = "Subject"
	if message_id:
		message["Message-Id"] = message_id
	for name, value in (headers or {}).items():
		message[name] = value
	if text is not None:
		message.set_content(text)
	if html is not None:
		if text is None:
			message.set_content(html, subtype="html")
		else:
			message.add_alternative(html, subtype="html")
	for filename, content, mimetype, extra in attachments:
		maintype, subtype = mimetype.split("/", 1)
		message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename, **extra)
	return message


class ScriptedHttp:
	"""A ``requests.Session`` stand-in serving a script of responses and recording requests."""

	def __init__(self, responses=None):
		self.script = list(responses or [])
		self.requests = []

	def request(self, method, url, **kwargs):
		record = self._record(method, url, kwargs)
		self.requests.append(record)
		item = self.script.pop(0) if self.script else self._default(record)
		if callable(item):
			item = item(record)
		if isinstance(item, BaseException):
			raise item
		return self._response(item, url)

	def get(self, url, **kwargs):
		return self.request("GET", url, **kwargs)

	def post(self, url, **kwargs):
		return self.request("POST", url, **kwargs)

	def close(self):
		pass

	@staticmethod
	def _record(method, url, kwargs):
		body = kwargs.get("data")
		if body is None and kwargs.get("json") is not None:
			body = json.dumps(kwargs["json"]).encode()
		if isinstance(body, str):
			body = body.encode()
		try:
			payload = json.loads(body) if body else None
		except ValueError:
			payload = None
		return {
			"method": method.upper(),
			"url": url,
			"headers": CaseInsensitiveDict(kwargs.get("headers") or {}),
			"body": body,
			"json": payload,
			"timeout": kwargs.get("timeout"),
		}

	@staticmethod
	def recipients(record):
		payload = record["json"] or {}
		return [e["address"] for field in ("to", "cc", "bcc") for e in payload.get(field) or []]

	@classmethod
	def _default(cls, record):
		if record["url"] == VERIFY_URL:
			return (
				200,
				{
					"success": True,
					"errors": [],
					"messages": [],
					"result": {"id": "token-id", "status": "active"},
				},
			)
		return success(delivered=cls.recipients(record))

	@staticmethod
	def _response(item, url):
		status, body, *rest = item
		headers = CaseInsensitiveDict(rest[0] if rest else {})
		if body is None:
			content = b""
		elif isinstance(body, bytes | bytearray):
			content = bytes(body)
		elif isinstance(body, str):
			content = body.encode()
		else:
			content = json.dumps(body).encode()
			headers.setdefault("Content-Type", "application/json")
		response = requests.Response()
		response.status_code = status
		response.url = url
		response.headers = headers
		response.encoding = "utf-8"
		response._content = content
		return response


def success(
	delivered=None,
	queued=None,
	permanent_bounces=None,
	suppressed_recipients=None,
	message_id="cf-message-id",
):
	return (
		200,
		{
			"success": True,
			"errors": [],
			"messages": [],
			"result": {
				"delivered": delivered or [],
				"queued": queued or [],
				"permanent_bounces": permanent_bounces or [],
				"suppressed_recipients": suppressed_recipients or [],
				"message_id": message_id,
			},
		},
	)


def error(status, code, message, headers=None):
	body = {"success": False, "errors": [{"code": code, "message": message}], "messages": [], "result": None}
	return (status, body, headers) if headers else (status, body)


class TestPayload(unittest.TestCase):
	def payload(self, message=None, envelope=("alice@example.com",)):
		return build_payload(message or build(), list(envelope))

	def addresses(self, payload, field):
		return [e["address"] for e in payload.get(field, [])]

	def test_text_and_html(self):
		payload = self.payload()
		self.assertEqual(payload["from"], {"address": "sender@cf.example.com", "name": "Sender"})
		self.assertEqual(payload["to"], [{"address": "alice@example.com", "name": "Alice"}])
		self.assertEqual(payload["subject"], "Subject")
		self.assertIn("Hello world", payload["text"])
		self.assertIn("<b>world</b>", payload["html"])
		self.assertNotIn("attachments", payload)

	def test_plain_and_html_only(self):
		self.assertNotIn("html", self.payload(build(html=None)))
		self.assertNotIn("text", self.payload(build(text=None)))

	def test_empty_body_raises(self):
		with self.assertRaises(CloudflareEmailError):
			self.payload(build(text="", html=None))

	def test_reply_to(self):
		payload = self.payload(build(headers={"Reply-To": "Replies <replies@cf.example.com>"}))
		self.assertEqual(payload["reply_to"], {"address": "replies@cf.example.com", "name": "Replies"})

	def test_envelope_recipient_only(self):
		# Frappe sends one copy per recipient: the envelope is one address, Cc is shown, not sent.
		payload = self.payload(build(cc="Bob <bob@example.com>"), envelope=("alice@example.com",))
		self.assertEqual(self.addresses(payload, "to"), ["alice@example.com"])
		self.assertNotIn("cc", payload)
		self.assertNotIn("bcc", payload)

	def test_cc_recipient_promoted_to_to(self):
		# The copy addressed to a cc'd recipient names them in Cc only; Cloudflare requires `to`.
		payload = self.payload(build(cc="Bob <bob@example.com>"), envelope=("bob@example.com",))
		self.assertEqual(payload["to"], [{"address": "bob@example.com", "name": "Bob"}])
		self.assertNotIn("cc", payload)

	def test_hidden_recipient_becomes_visible(self):
		# The To placeholder was not expanded: the recipient is named nowhere in the headers.
		payload = self.payload(build(to="undisclosed-recipients:;"), envelope=("carol@example.com",))
		self.assertEqual(payload["to"], [{"address": "carol@example.com"}])

	def test_several_hidden_recipients_stay_hidden(self):
		payload = self.payload(
			build(to="undisclosed-recipients:;"), envelope=("a@example.com", "b@example.com")
		)
		self.assertEqual(self.addresses(payload, "bcc"), ["a@example.com", "b@example.com"])

	def test_recipients_deduplicated_case_insensitively(self):
		payload = self.payload(
			build(to="Alice <alice@example.com>, ALICE@EXAMPLE.COM"),
			envelope=("alice@example.com", "ALICE@EXAMPLE.COM"),
		)
		self.assertEqual(self.addresses(payload, "to"), ["alice@example.com"])

	def test_no_and_too_many_recipients(self):
		with self.assertRaises(CloudflareEmailError):
			self.payload(envelope=())
		with self.assertRaises(CloudflareEmailError):
			self.payload(envelope=tuple(f"r{i}@example.com" for i in range(MAX_RECIPIENTS + 1)))

	def test_attachment(self):
		payload = self.payload(build(attachments=[("report.pdf", b"%PDF-1.4", "application/pdf", {})]))
		[attachment] = payload["attachments"]
		self.assertEqual(attachment["filename"], "report.pdf")
		self.assertEqual(attachment["type"], "application/pdf")
		self.assertEqual(attachment["disposition"], "attachment")
		self.assertEqual(base64.b64decode(attachment["content"]), b"%PDF-1.4")
		self.assertNotIn("content_id", attachment)

	def test_inline_cid(self):
		message = build()
		message.add_attachment(
			b"PNG1",
			maintype="image",
			subtype="png",
			filename="logo.png",
			cid="<logo@site>",
			disposition="inline",
		)
		message.add_attachment(
			b"PNG2", maintype="image", subtype="png", filename="chart.png", cid="<chart@site>"
		)
		payload = build_payload(message, ["alice@example.com"])
		self.assertEqual(
			[(a["filename"], a["disposition"], a["content_id"]) for a in payload["attachments"]],
			[("logo.png", "inline", "logo@site"), ("chart.png", "inline", "chart@site")],
		)
		self.assertEqual(payload["attachments"][0]["type"], "image/png")

	def test_rfc822_attachment_stays_whole(self):
		message = build()
		inner = email.message_from_bytes(INNER_EML, policy=email.policy.default)
		message.add_attachment(inner, filename="forwarded.eml")
		payload = build_payload(message, ["alice@example.com"])
		[attachment] = payload["attachments"]
		self.assertEqual(attachment["type"], "message/rfc822")
		self.assertIn(b"Inner body", base64.b64decode(attachment["content"]))
		self.assertNotIn("Inner body", payload["text"])

	def test_header_allowlist(self):
		payload = self.payload(
			build(
				headers={
					"X-Frappe-Site": "site.example",
					"In-Reply-To": "<parent@site.example>",
					"Precedence": "bulk",
					"Disposition-Notification-To": "x@y",
					"Date": "Mon, 1 Jan 2026 00:00:00 +0000",
				}
			)
		)
		headers = payload["headers"]
		self.assertEqual(headers["X-Frappe-Site"], "site.example")
		self.assertEqual(headers["Precedence"], "bulk")
		self.assertNotIn("Disposition-Notification-To", headers)
		self.assertNotIn("Date", headers)
		self.assertNotIn("Message-Id", headers)

	def test_references_appended_from_own_message_id(self):
		payload = self.payload()
		self.assertEqual(payload["headers"]["References"], f"<{SITE_ID}>")

	def test_references_seeded_from_in_reply_to(self):
		# Frappe only ever sets In-Reply-To; the chain must start from it so the reply threads.
		payload = self.payload(build(headers={"In-Reply-To": "<parent@site.example>"}))
		self.assertEqual(payload["headers"]["References"], f"<parent@site.example> <{SITE_ID}>")
		self.assertEqual(payload["headers"]["In-Reply-To"], "<parent@site.example>")

	def test_references_present_kept_and_own_id_not_duplicated(self):
		payload = self.payload(build(headers={"References": f"<a@x>  <b@y>, <{SITE_ID}>"}))
		self.assertEqual(payload["headers"]["References"], f"<a@x> <b@y> <{SITE_ID}>")

	def test_references_trimmed_oldest_first(self):
		many = " ".join(f"<{i:0>60}@x>" for i in range(60))
		payload = self.payload(build(headers={"References": many}))
		refs = payload["headers"]["References"]
		self.assertLessEqual(len(refs.encode()), MAX_HEADER_VALUE_BYTES)
		self.assertTrue(refs.endswith(f"<{SITE_ID}>"))
		self.assertNotIn(f"<{0:0>60}@x>", refs)

	def test_headers_too_large_raises(self):
		with self.assertRaises(CloudflareEmailError):
			self.payload(build(headers={"X-Big": "x" * (MAX_HEADER_VALUE_BYTES + 1)}))

	def test_no_message_id(self):
		payload = self.payload(build(message_id=None))
		self.assertNotIn("headers", payload)


class TestSession(unittest.TestCase):
	def setUp(self):
		self.sleeps = []
		patcher = patch.object(cloudflare_api, "_sleep", self.sleeps.append)
		patcher.start()
		self.addCleanup(patcher.stop)

	def session(self, responses=None):
		self.http = ScriptedHttp(responses)
		return CloudflareSendingSession(ACCOUNT_ID, API_TOKEN, http=self.http)

	def send(self, session, message=None, envelope=("alice@example.com",)):
		return session.send_message(message or build(), "sender@cf.example.com", list(envelope))

	def test_posts_the_payload_with_the_token(self):
		session = self.session([success(["alice@example.com"], message_id="cf-42")])
		self.assertEqual(self.send(session), "cf-42")
		[request] = self.http.requests
		self.assertEqual(
			(request["method"], request["url"]), ("POST", SEND_URL.format(account_id=ACCOUNT_ID))
		)
		self.assertEqual(request["headers"]["Authorization"], f"Bearer {API_TOKEN}")
		self.assertEqual(request["json"]["to"], [{"address": "alice@example.com", "name": "Alice"}])

	def test_too_large_before_http(self):
		message = build(
			attachments=[("big.bin", b"\x00" * (4 * 1024 * 1024), "application/octet-stream", {})]
		)
		with self.assertRaises(CloudflareEmailError) as raised:
			self.send(self.session(), message)
		self.assertIn("MiB", str(raised.exception))
		self.assertEqual(self.http.requests, [])

	def test_400_no_retry(self):
		with self.assertRaises(CloudflareEmailError) as raised:
			self.send(self.session([error(400, 10001, "invalid_request_schema")]))
		self.assertEqual(
			(raised.exception.status, raised.exception.code, raised.exception.fatal), (400, 10001, False)
		)
		self.assertEqual(len(self.http.requests), 1)
		self.assertEqual(self.sleeps, [])

	def test_401_fatal_fails_the_rest_of_the_batch(self):
		session = self.session([error(401, 10101, "unauthorized")])
		with self.assertRaises(CloudflareEmailError) as raised:
			self.send(session)
		self.assertTrue(raised.exception.fatal)
		with self.assertRaises(CloudflareEmailError) as raised:
			self.send(session)
		self.assertIn("Not retried", str(raised.exception))
		self.assertEqual(len(self.http.requests), 1)

	def test_403_fatal(self):
		with self.assertRaises(CloudflareEmailError) as raised:
			self.send(self.session([error(403, 10102, "forbidden")]))
		self.assertTrue(raised.exception.fatal)

	def test_429_honours_retry_after_and_caps_backoff(self):
		session = self.session(
			[
				error(429, 10004, "rate limited", {"Retry-After": "3"}),
				error(429, 10004, "rate limited"),
				success(["alice@example.com"]),
			]
		)
		self.send(session)
		self.assertEqual(len(self.http.requests), 3)
		self.assertEqual(self.sleeps[0], 3)
		self.assertLessEqual(self.sleeps[1], 10)

	def test_5xx_then_success_and_exhausted(self):
		self.send(self.session([error(502, 0, "bad gateway"), success(["alice@example.com"])]))
		self.assertEqual(len(self.http.requests), 2)
		self.assertEqual(self.sleeps, [1])
		with self.assertRaises(CloudflareEmailError) as raised:
			self.send(self.session([error(503, 0, "down")] * 5))
		self.assertEqual(raised.exception.status, 503)
		self.assertEqual(len(self.http.requests), 3)

	def test_network_error_then_success_and_exhausted(self):
		self.send(self.session([requests.ConnectionError("dns"), success(["alice@example.com"])]))
		self.assertEqual(len(self.http.requests), 2)
		with self.assertRaises(CloudflareEmailError):
			self.send(self.session([requests.ConnectionError("dns")] * 5))

	def test_success_false_and_non_json_200(self):
		with self.assertRaises(CloudflareEmailError):
			self.send(self.session([(200, {"success": False, "errors": [{"code": 1, "message": "no"}]})]))
		with self.assertRaises(CloudflareEmailError):
			self.send(self.session([(200, "<html>ok</html>")]))

	def test_bounces(self):
		with self.assertRaises(CloudflareEmailError):
			self.send(self.session([success(permanent_bounces=["alice@example.com"])]))
		with self.assertRaises(CloudflareEmailError):
			self.send(self.session([success(suppressed_recipients=["alice@example.com"])]))
		session = self.session(
			[
				success(
					delivered=["alice@example.com"],
					permanent_bounces=["bob@example.com"],
					message_id="cf-partial",
				)
			]
		)
		with self.assertLogs(cloudflare_api._logger, level="WARNING") as logs:
			result = self.send(
				session, build(cc="Bob <bob@example.com>"), envelope=("alice@example.com", "bob@example.com")
			)
		self.assertEqual(result, "cf-partial")
		self.assertTrue(any("bob@example.com" in line for line in logs.output))


class TestVerifyToken(unittest.TestCase):
	def test_active(self):
		http = ScriptedHttp()
		self.assertEqual(verify_token("secret-token", http=http)["status"], "active")
		[request] = http.requests
		self.assertEqual((request["method"], request["url"]), ("GET", VERIFY_URL))
		self.assertEqual(request["headers"]["Authorization"], "Bearer secret-token")

	def test_rejected_inactive_unreachable(self):
		with self.assertRaises(CloudflareEmailError) as raised:
			verify_token("bad", http=ScriptedHttp([error(401, 1000, "Invalid API Token")]))
		self.assertTrue(raised.exception.fatal)
		with self.assertRaises(CloudflareEmailError) as raised:
			verify_token(
				"old",
				http=ScriptedHttp([(200, {"success": True, "errors": [], "result": {"status": "expired"}})]),
			)
		self.assertIn("expired", str(raised.exception))
		with self.assertRaises(CloudflareEmailError):
			verify_token("x", http=ScriptedHttp([requests.ConnectionError("dns")]))
