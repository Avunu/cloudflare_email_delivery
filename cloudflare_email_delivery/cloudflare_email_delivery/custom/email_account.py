# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

import secrets
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.email.doctype.email_account.email_account import EmailAccount as BaseEmailAccount
from frappe.types import DF
from frappe.utils import get_url

# Hosts on which a plain-http site URL is accepted for the webhook URL: the local
# `wrangler dev` + bench loop has no TLS. Anything else must be https, because the
# URL carries the key and the body carries customer mail.
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
INBOUND_METHOD = "cloudflare_email_delivery.api.inbound"


def new_token() -> str:
	"""32 random bytes -> 43 URL-safe characters; the same shape the Odoo module uses."""
	return secrets.token_urlsafe(32)


class EmailAccount(BaseEmailAccount):
	if TYPE_CHECKING:
		cf_enabled: DF.Check
		cf_webhook_key: DF.Data | None
		cf_webhook_secret: DF.Password | None
		cf_webhook_url: DF.Data | None

	def _is_cloudflare_domain(self) -> bool:
		"""Return True if the linked Email Domain has send_via_cloudflare enabled."""
		if not self.domain:
			return False
		return bool(frappe.db.get_value("Email Domain", self.domain, "send_via_cloudflare"))

	def _is_cloudflare_receiving(self) -> bool:
		return bool(self.enable_incoming) and self._is_cloudflare_domain()

	# -- validation ------------------------------------------------------------

	def validate(self):
		"""A Cloudflare account has no IMAP/POP or SMTP server: outbound goes to the
		Email Sending API, inbound arrives from the relay. Everything core would
		test a connection for is blanked, and the base validation runs with the
		``in_install`` flag so it skips connection tests and the password rule."""
		if not self._is_cloudflare_domain():
			super().validate()
			return
		self.cf_enabled = 1
		self.use_imap = 0
		self.use_starttls = 0
		self.email_server = None
		self.smtp_server = None
		self.awaiting_password = 0
		self.ensure_webhook_credentials()
		_prev = frappe.local.flags.in_install
		frappe.local.flags.in_install = True
		try:
			super().validate()
		finally:
			frappe.local.flags.in_install = _prev
		self.validate_webhook_key_unique()
		self.set_webhook_url()

	def ensure_webhook_credentials(self):
		"""Generate the key and secret once. Never rotate here: saving the Email
		Domain re-saves every linked account, and the relay was deployed with
		these values."""
		if not self.cf_webhook_key:
			self.cf_webhook_key = new_token()
		if not self.get_password("cf_webhook_secret", raise_exception=False):
			# Encrypted into __Auth by _save_passwords() on save.
			self.cf_webhook_secret = new_token()

	def validate_webhook_key_unique(self):
		if not self.cf_webhook_key:
			return
		clash = frappe.db.exists(
			"Email Account", {"cf_webhook_key": self.cf_webhook_key, "name": ("!=", self.name)}
		)
		if clash:
			frappe.throw(_("Webhook key already in use by Email Account {0}").format(clash))

	def get_webhook_url(self) -> str | None:
		if not self.cf_webhook_key:
			return None
		return f"{get_url().rstrip('/')}/api/method/{INBOUND_METHOD}?key={self.cf_webhook_key}"

	def set_webhook_url(self):
		if not self._is_cloudflare_receiving():
			self.cf_webhook_url = None
			return
		self.cf_webhook_url = self.get_webhook_url()
		self.validate_webhook_url_scheme()

	def validate_webhook_url_scheme(self):
		parts = urlsplit(self.cf_webhook_url or "")
		if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS):
			return
		if frappe.conf.developer_mode or frappe.in_test:
			return
		frappe.throw(
			_(
				"The webhook URL ({0}) must be https: set <code>host_name</code> in the site config to the site's public https address."
			).format(self.cf_webhook_url)
		)

	# -- no SMTP -----------------------------------------------------------------

	def validate_smtp_conn(self):
		"""Skip SMTP validation when the domain sends via Cloudflare."""
		if self._is_cloudflare_domain():
			return
		return super().validate_smtp_conn()

	def get_smtp_server(self):
		"""No SMTPServer for a Cloudflare domain — the send hook posts to the API."""
		if self._is_cloudflare_domain():
			return None
		return super().get_smtp_server()

	def append_email_to_sent_folder(self, message):
		"""Nothing to append to: there is no IMAP mailbox."""
		if self._is_cloudflare_domain():
			return
		return super().append_email_to_sent_folder(message)

	# -- no polling ---------------------------------------------------------------

	def get_inbound_mails(self):
		"""The scheduler's pull is a no-op: the relay pushes to ``api.inbound``."""
		if self._is_cloudflare_domain():
			return []
		return super().get_inbound_mails()

	def get_incoming_server(self, in_receive=False, email_sync_rule="UNSEEN"):
		if self._is_cloudflare_domain():
			frappe.throw(
				_(
					"Email Account {0} receives mail from the Cloudflare email relay; there is no IMAP/POP server."
				).format(self.name)
			)
		return super().get_incoming_server(in_receive, email_sync_rule)

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
