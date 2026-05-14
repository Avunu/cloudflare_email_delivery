# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

import frappe
from frappe.email.doctype.email_account.email_account import EmailAccount as BaseEmailAccount


class EmailAccount(BaseEmailAccount):
	def _is_cloudflare_domain(self) -> bool:
		"""Return True if the linked Email Domain has send_via_cloudflare enabled."""
		if not self.domain:
			return False
		return bool(frappe.db.get_value("Email Domain", self.domain, "send_via_cloudflare"))

	def validate(self):
		"""If the linked Email Domain has send_via_cloudflare enabled, skip
		SMTP/IMAP connection validation and the password requirement."""
		if self._is_cloudflare_domain():
			# Set in_install so the base validate skips connection tests and
			# the "Password is required" throw entirely.
			_prev = frappe.local.flags.in_install
			frappe.local.flags.in_install = True
			try:
				super().validate()
			finally:
				frappe.local.flags.in_install = _prev
		else:
			super().validate()

	def validate_smtp_conn(self):
		"""Skip SMTP validation when the domain sends via Cloudflare."""
		if self._is_cloudflare_domain():
			return
		return super().validate_smtp_conn()

	def get_smtp_server(self):
		"""Return a no-op stub instead of building an SMTPServer when the domain
		sends via Cloudflare — avoids fetching a password that doesn't exist."""
		if self._is_cloudflare_domain():
			return None
		return super().get_smtp_server()

	def there_must_be_only_one_default(self):
		"""Un-default all other accounts using db_set to avoid triggering
		SMTP/IMAP validation on sibling accounts."""
		for field in ("default_incoming", "default_outgoing"):
			if not self.get(field):
				continue
			frappe.db.set_value(
				"Email Account",
				{field: 1, "name": ["!=", self.name]},
				field,
				0,
			)
