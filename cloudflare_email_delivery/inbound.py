# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""``InboundMail`` for mail that arrives from the relay.

Cloudflare Email Sending rewrites the outbound ``Message-ID``, so a reply's
``In-Reply-To`` points at an id this site never issued; Frappe threads only on
``In-Reply-To`` (``frappe.email.receive``). The outbound side appends Frappe's
own id to ``References`` (see ``cloudflare_api._passthrough_headers``); this
side looks through ``References`` for an id it knows. Everything else in
``InboundMail`` — ``is_reply``, ``parent_email_queue``, ``parent_communication``,
``reference_document`` — reads ``in_reply_to``, so overriding that one property
threads the reply onto the right Email Queue entry / Communication / document.
"""

import frappe
from frappe.email.receive import InboundMail

from cloudflare_email_delivery.relay import message_ids


def is_known_message_id(mid: str) -> bool:
	"""Whether ``mid`` is one this site issued (its ``make_msgid`` domain is the site
	name) or one it stored on a Communication or an Email Queue entry."""
	site = getattr(frappe.local, "site", None)
	if site and site in mid:
		return True
	return bool(
		frappe.db.exists("Communication", {"message_id": mid})
		or frappe.db.exists("Email Queue", {"message_id": mid})
	)


class RelayInboundMail(InboundMail):
	"""``InboundMail`` that also threads on ``References``."""

	_resolved_in_reply_to: str | None = None

	@property
	def in_reply_to(self) -> str:
		if self._resolved_in_reply_to is None:
			self._resolved_in_reply_to = self._resolve_in_reply_to()
		return self._resolved_in_reply_to

	def candidate_message_ids(self) -> list[str]:
		"""``In-Reply-To`` first, then ``References`` newest-first, de-duplicated."""
		ids = message_ids(self.mail.get("In-Reply-To"))
		ids += reversed(message_ids(self.mail.get("References")))
		seen: set[str] = set()
		out = []
		for mid in ids:
			if mid not in seen:
				seen.add(mid)
				out.append(mid)
		return out

	def _resolve_in_reply_to(self) -> str:
		candidates = self.candidate_message_ids()
		for mid in candidates:
			if is_known_message_id(mid):
				return mid
		# Nothing known: keep core's behaviour (its own fallback tries the local
		# part of In-Reply-To as a Communication name).
		return candidates[0] if candidates else ""
