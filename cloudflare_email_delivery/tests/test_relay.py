# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""``relay`` on its own: signing, the envelope headers and the header helpers.
Pure ``unittest`` — no site needed."""

import unittest

from cloudflare_email_delivery import relay

VECTOR = "v1=4d583a269f4f276a3fa80ff31b5a01879a848096983222a17893d198418939aa"
SECRET = "a-webhook-secret-of-sufficient-length"
BODY = b"Subject: hi\r\n\r\nbody\r\n"


class TestSignature(unittest.TestCase):
	def test_vector(self):
		# The vector the relay's and the Odoo module's tests pin; keep every side in sync.
		self.assertEqual(relay.sign("key", 1700000000, b"hello"), VECTOR)
		self.assertEqual(relay.sign("key", "1700000000", "hello"), VECTOR)

	def test_verify_round_trip(self):
		now = 1700000000
		signature = relay.sign(SECRET, now, BODY)
		self.assertTrue(relay.verify_signature(SECRET, str(now), signature, BODY, now=now))
		self.assertTrue(
			relay.verify_signature(SECRET, str(now), signature.upper().replace("V1", "v1"), BODY, now=now)
		)
		self.assertTrue(relay.verify_signature(SECRET, str(now), signature, BODY.decode(), now=now))

	def test_tolerance(self):
		now = 1700000000
		signature = relay.sign(SECRET, now, BODY)
		for skew in (-300, -1, 0, 1, 300):
			self.assertTrue(relay.verify_signature(SECRET, str(now), signature, BODY, now=now + skew), skew)
		for skew in (-301, 301, 86400):
			self.assertFalse(relay.verify_signature(SECRET, str(now), signature, BODY, now=now + skew), skew)

	def test_timestamp_signed_as_received(self):
		# "0042" must not be re-formatted to "42" before signing.
		signature = relay.sign(SECRET, "0042", BODY)
		self.assertTrue(relay.verify_signature(SECRET, "0042", signature, BODY, now=42))
		self.assertFalse(relay.verify_signature(SECRET, "42", signature, BODY, now=42))

	def test_rejects_tampering_and_malformed_input(self):
		now = 1700000000
		signature = relay.sign(SECRET, now, BODY)
		self.assertFalse(relay.verify_signature(SECRET, str(now), signature, BODY + b"x", now=now))
		self.assertFalse(relay.verify_signature(SECRET, str(now + 1), signature, BODY, now=now))
		self.assertFalse(
			relay.verify_signature("other-secret-of-sufficient-len", str(now), signature, BODY, now=now)
		)
		self.assertFalse(
			relay.verify_signature(SECRET, str(now), signature.replace("v1=", "v2="), BODY, now=now)
		)
		self.assertFalse(relay.verify_signature(SECRET, str(now), signature.partition("=")[2], BODY, now=now))
		self.assertFalse(relay.verify_signature(SECRET, str(now), "v1=", BODY, now=now))
		self.assertFalse(relay.verify_signature(SECRET, str(now), "v1=zz", BODY, now=now))
		self.assertFalse(relay.verify_signature(SECRET, str(now), "v1=é", BODY, now=now))
		self.assertFalse(relay.verify_signature(SECRET, "soon", signature, BODY, now=now))
		self.assertFalse(relay.verify_signature(SECRET, None, signature, BODY, now=now))
		self.assertFalse(relay.verify_signature(SECRET, str(now), None, BODY, now=now))
		self.assertFalse(relay.verify_signature(None, str(now), signature, BODY, now=now))
		self.assertFalse(relay.verify_signature("", str(now), signature, BODY, now=now))


class TestEnvelope(unittest.TestCase):
	def test_check_envelope_decodes_and_strips(self):
		self.assertEqual(relay.check_envelope("envelope-to", " a@b.test "), "a@b.test")
		self.assertEqual(relay.check_envelope("envelope-to", "jos%C3%A9@b.test"), "josé@b.test")
		self.assertEqual(relay.check_envelope("envelope-to", "100%25@b.test"), "100%@b.test")
		self.assertEqual(relay.check_envelope("envelope-from", ""), "")
		self.assertIsNone(relay.check_envelope("envelope-from", None))

	def test_check_envelope_rejects_control_characters_even_encoded(self):
		for value in ("a@b.test\r\nBcc: x@y", "a@b.test%0d%0aBcc: x@y", "a\x00@b.test", "a%7f@b.test"):
			with self.assertRaises(ValueError):
				relay.check_envelope("envelope-to", value)

	def test_prepend_envelope_headers(self):
		out = relay.prepend_envelope_headers(BODY, "s@x.test", "r@y.test")
		self.assertEqual(out, b"Delivered-To: r@y.test\r\nReturn-Path: <s@x.test>\r\n" + BODY)
		# A bounce has the null reverse-path.
		self.assertTrue(
			relay.prepend_envelope_headers(BODY, "", "r@y.test").startswith(
				b"Delivered-To: r@y.test\r\nReturn-Path: <>\r\n"
			)
		)
		# Nothing to add: untouched.
		self.assertEqual(relay.prepend_envelope_headers(BODY, None, None), BODY)
		self.assertEqual(relay.prepend_envelope_headers(BODY, None, ""), BODY)

	def test_prepend_is_idempotent(self):
		once = relay.prepend_envelope_headers(BODY, "s@x.test", "r@y.test")
		self.assertEqual(relay.prepend_envelope_headers(once, "other@x.test", "other@y.test"), once)
		# Present but spelled differently, or folded, is still present.
		body = b"delivered-to: r@y.test\r\nReturn-Path:\r\n <s@x.test>\r\n" + BODY
		self.assertEqual(relay.prepend_envelope_headers(body, "s@x.test", "r@y.test"), body)

	def test_first_header_and_recipient_domain(self):
		body = b"Delivered-To: Support\r\n <Support@Acme.Example.>\r\nTo: x@y\r\n\r\nDelivered-To: not-a-header\r\n"
		self.assertEqual(relay.first_header(body, "delivered-to"), "Support <Support@Acme.Example.>")
		self.assertIsNone(relay.first_header(body, "Return-Path"))
		self.assertEqual(relay.recipient_domain(relay.first_header(body, "Delivered-To")), "acme.example")
		self.assertEqual(relay.recipient_domain("a@b.test"), "b.test")
		self.assertIsNone(relay.recipient_domain("nobody"))
		self.assertIsNone(relay.recipient_domain(None))

	def test_message_ids(self):
		self.assertEqual(relay.message_ids("<a@x> <b@y>\r\n\t<c@z>"), ["a@x", "b@y", "c@z"])
		self.assertEqual(relay.message_ids(None), [])
		self.assertEqual(relay.message_ids("no brackets"), [])
