# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The relay's wire contract: request signing and the SMTP envelope headers.

Pure functions, no Frappe import, so they can be unit-tested without a site and
stay byte-for-byte compatible with the Odoo module and the relay itself. The
contract is documented in Avunu/cloudflare-email-relay's README; the vector
every implementation pins is ``sign("key", 1700000000, b"hello")``.
"""

import hashlib
import hmac
import re
import time
from email.utils import parseaddr
from urllib.parse import unquote

# Signature scheme the relay sends as ``X-Email-Relay-Signature``. The ``v1=``
# prefix leaves room for rotating the algorithm without breaking deployed relays.
SIGNATURE_SCHEME = "v1"
# Seconds a signed request stays valid on either side of the server clock:
# wide enough for a relay attempt queued behind a slow request, narrow enough
# that a captured request cannot be replayed at leisure.
SIGNATURE_TOLERANCE = 300

HEADER_ID = "X-Email-Relay-Id"
HEADER_TENANT = "X-Email-Relay-Tenant"
HEADER_TIMESTAMP = "X-Email-Relay-Timestamp"
HEADER_SIGNATURE = "X-Email-Relay-Signature"
HEADER_ENVELOPE_FROM = "X-Email-Relay-Envelope-From"
HEADER_ENVELOPE_TO = "X-Email-Relay-Envelope-To"
HEADER_ATTEMPT = "X-Email-Relay-Attempt"

# Header field names are printable US-ASCII except the colon (RFC 5322 §3.6.8)
# and only ever start a line: folded continuation lines start with whitespace,
# so this cannot match inside a value.
_HEADER_NAME_RE = re.compile(rb"^([\x21-\x39\x3b-\x7e]+):", re.MULTILINE)
_HEADER_BLOCK_END_RE = re.compile(rb"\r?\n\r?\n")
_ENVELOPE_FORBIDDEN_RE = re.compile(r"[\x00-\x1f\x7f]")
_MSGID_RE = re.compile(r"<([^<>\s]+)>")


def _as_bytes(body: bytes | str) -> bytes:
	return body.encode() if isinstance(body, str) else bytes(body)


def sign(secret: str, timestamp: int | str, body: bytes | str) -> str:
	"""``v1=`` + hex HMAC-SHA256 over ``"<timestamp>."`` followed by the body bytes."""
	digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + _as_bytes(body), hashlib.sha256)
	return f"{SIGNATURE_SCHEME}={digest.hexdigest()}"


def verify_signature(
	secret: str | None,
	timestamp: str | None,
	signature: str | None,
	body: bytes | str,
	now: int | None = None,
	tolerance: int = SIGNATURE_TOLERANCE,
) -> bool:
	"""Whether ``signature`` is ``sign(secret, timestamp, body)`` for a ``timestamp``
	within ``tolerance`` seconds of ``now``.

	Any malformed input is a plain ``False``: the endpoint answers 401 without
	saying which check failed.
	"""
	if not secret or not timestamp or not signature:
		return False
	timestamp = str(timestamp).strip()
	try:
		issued_at = int(timestamp)
	except ValueError:
		return False
	now = int(time.time()) if now is None else int(now)
	if abs(now - issued_at) > tolerance:
		return False
	scheme, _sep, digest = str(signature).strip().partition("=")
	if scheme != SIGNATURE_SCHEME or not digest:
		return False
	# Sign the timestamp exactly as received: the relay signed the string it
	# sent, and a re-formatted integer would not round-trip "0042".
	expected = sign(secret, timestamp, body).partition("=")[2]
	try:
		return hmac.compare_digest(expected, digest.lower())
	except TypeError:  # non-ASCII digest
		return False


def check_envelope(name: str, value: str | None) -> str | None:
	"""``value`` decoded and stripped, or ``None`` when absent; raises ``ValueError``
	on anything that could smuggle a second header line.

	HTTP header values are ASCII, so the relay percent-encodes anything else in
	an envelope address (an SMTPUTF8 mailbox, or a literal ``%``) exactly as
	``encodeURIComponent`` would; ``unquote`` is its inverse and leaves an
	ordinary address untouched. The control-character check runs on the decoded
	value, so encoding is no way around it.
	"""
	if value is None:
		return None
	value = unquote(str(value))
	if _ENVELOPE_FORBIDDEN_RE.search(value):
		raise ValueError(f"Invalid {name} address: control characters")
	return value.strip()


def existing_header_names(body: bytes) -> set[str]:
	"""Lower-cased names of the header fields at the top of ``body``."""
	match = _HEADER_BLOCK_END_RE.search(body)
	head = body[: match.start()] if match else body
	return {name.decode("ascii").lower() for name in _HEADER_NAME_RE.findall(head)}


def prepend_envelope_headers(body: bytes | str, envelope_from: str | None, envelope_to: str | None) -> bytes:
	"""Prepend the envelope as ``Delivered-To`` / ``Return-Path`` headers when the raw
	message lacks them. The relay injects the same headers before storing the
	message; this is belt-and-braces for messages posted by anything else.
	"""
	body = _as_bytes(body)
	present = existing_header_names(body)
	lines = []
	if envelope_to and "delivered-to" not in present:
		lines.append(f"Delivered-To: {envelope_to}")
	if envelope_from is not None and "return-path" not in present:
		# An empty envelope sender (a bounce) is written as the null path.
		lines.append(f"Return-Path: <{envelope_from}>")
	if not lines:
		return body
	return "".join(f"{line}\r\n" for line in lines).encode() + body


def first_header(body: bytes, name: str) -> str | None:
	"""The first value of header ``name`` in the message's header block, unfolded."""
	match = _HEADER_BLOCK_END_RE.search(body)
	head = body[: match.start()] if match else body
	pattern = re.compile(
		rb"^" + re.escape(name.encode()) + rb":[ \t]*(.*?)(?:\r?\n(?![ \t]))", re.I | re.M | re.S
	)
	found = pattern.search(head + b"\n")
	if not found:
		return None
	return re.sub(rb"\r?\n[ \t]+", b" ", found.group(1)).decode("utf-8", "replace").strip()


def recipient_domain(address: str | None) -> str | None:
	"""The lower-cased domain of an address (``Name <a@b>`` or bare), or ``None``."""
	if not address:
		return None
	_name, addr = parseaddr(address)
	addr = addr or address.strip()
	if "@" not in addr:
		return None
	return addr.rsplit("@", 1)[1].strip().rstrip(".").lower() or None


def message_ids(value: str | None) -> list[str]:
	"""The bare ids (no angle brackets) in an ``In-Reply-To`` / ``References`` value, in order."""
	return _MSGID_RE.findall(value or "")
