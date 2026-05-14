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

	def validate_smtp_conn(self):
		"""Skip SMTP validation when the domain sends via Cloudflare."""
		if self._is_cloudflare_domain():
			return
		return super().validate_smtp_conn()

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
