# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The Email Account and Email Domain extensions: a Cloudflare-domain account has no
IMAP/POP or SMTP server and carries the webhook credentials, and anything else goes
through core's controller unchanged."""

from typing import cast
from unittest.mock import MagicMock, patch

import frappe
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.email.doctype.email_domain.email_domain import EmailDomain
from frappe.tests import IntegrationTestCase

from cloudflare_email_delivery import cloudflare_api
from cloudflare_email_delivery.cloudflare_api import CloudflareEmailError
from cloudflare_email_delivery.cloudflare_email_delivery.custom import email_domain
from cloudflare_email_delivery.cloudflare_email_delivery.custom.email_account import (
	INBOUND_METHOD,
	CloudflareEmailAccount,
)
from cloudflare_email_delivery.cloudflare_email_delivery.custom.email_domain import CloudflareEmailDomain
from cloudflare_email_delivery.tests import fixtures
from cloudflare_email_delivery.tests.fixtures import (
	ACCOUNT_ID,
	API_TOKEN,
	DISABLED_ACCOUNT,
	DOMAIN,
	INBOUND_ACCOUNT,
	OUTBOUND_ACCOUNT,
)


def get_account(name: str) -> CloudflareEmailAccount:
	return cast(CloudflareEmailAccount, frappe.get_doc("Email Account", name))


def get_domain() -> CloudflareEmailDomain:
	return cast(CloudflareEmailDomain, frappe.get_doc("Email Domain", DOMAIN))


def plain_account() -> CloudflareEmailAccount:
	"""An unsaved account with no Email Domain: not ours, so core's code path."""
	return cast(CloudflareEmailAccount, frappe.new_doc("Email Account"))


class TestEmailAccount(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fixtures.ensure_fixtures()
		cls.addClassCleanup(fixtures.remove_fixtures)

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def test_controller_is_composed(self):
		account = frappe.get_doc("Email Account", INBOUND_ACCOUNT)
		self.assertIsInstance(account, CloudflareEmailAccount)
		self.assertIsInstance(account, EmailAccount)

	def test_cloudflare_account_has_no_servers_and_gets_credentials(self):
		account = get_account(INBOUND_ACCOUNT)
		self.assertEqual(account.cf_enabled, 1)
		self.assertFalse(account.use_imap)
		self.assertFalse(account.email_server)
		self.assertFalse(account.smtp_server)
		self.assertEqual(len(account.cf_webhook_key or ""), 43)
		self.assertTrue(account.get_password("cf_webhook_secret"))
		self.assertEqual(
			account.cf_webhook_url,
			f"{frappe.utils.get_url().rstrip('/')}/api/method/{INBOUND_METHOD}?key={account.cf_webhook_key}",
		)

	def test_credentials_are_never_rotated_on_save(self):
		account = get_account(INBOUND_ACCOUNT)
		key, secret = account.cf_webhook_key, account.get_password("cf_webhook_secret")
		account.save(ignore_permissions=True)
		account = get_account(INBOUND_ACCOUNT)
		self.assertEqual(account.cf_webhook_key, key)
		self.assertEqual(account.get_password("cf_webhook_secret"), secret)

	def test_outgoing_only_account_has_no_webhook_url(self):
		account = get_account(OUTBOUND_ACCOUNT)
		self.assertTrue(account.cf_webhook_key)
		self.assertIsNone(account.cf_webhook_url)
		account.cf_webhook_key = None
		self.assertIsNone(account.get_webhook_url())

	def test_webhook_key_must_be_unique(self):
		account = get_account(DISABLED_ACCOUNT)
		account.cf_webhook_key = get_account(INBOUND_ACCOUNT).cf_webhook_key
		with self.assertRaises(frappe.ValidationError):
			account.save(ignore_permissions=True)
		account.cf_webhook_key = None
		account.validate_webhook_key_unique()

	def test_webhook_url_must_be_https_in_production(self):
		account = get_account(INBOUND_ACCOUNT)
		for url in ("https://erp.example.com/x", "http://localhost:8000/x", "http://127.0.0.1/x"):
			account.cf_webhook_url = url
			account.validate_webhook_url_scheme()
		account.cf_webhook_url = "http://erp.example.com/x"
		account.validate_webhook_url_scheme()  # tests and developer mode accept it
		with (
			patch.object(frappe, "in_test", False),
			patch.dict(frappe.conf, {"developer_mode": 0}),
			self.assertRaises(frappe.ValidationError),
		):
			account.validate_webhook_url_scheme()

	def test_one_default_outgoing(self):
		self.addCleanup(self.remove_second_default)
		# Core's version loads and re-saves each sibling default; ours must not reach it.
		with patch.object(EmailAccount, "there_must_be_only_one_default") as core_one_default:
			second = fixtures.ensure_account(
				"_Test CF Outbound 2", f"other@{DOMAIN}", outgoing=True, default_outgoing=1
			)
		core_one_default.assert_not_called()
		self.assertEqual(frappe.db.get_value("Email Account", OUTBOUND_ACCOUNT, "default_outgoing"), 0)
		self.assertEqual(frappe.db.get_value("Email Account", second.name, "default_outgoing"), 1)

	def remove_second_default(self):
		frappe.db.rollback()
		frappe.delete_doc(
			"Email Account", "_Test CF Outbound 2", force=True, ignore_permissions=True, ignore_missing=True
		)
		frappe.db.set_value("Email Account", OUTBOUND_ACCOUNT, "default_outgoing", 1)
		frappe.db.commit()

	def test_no_smtp_for_a_cloudflare_domain(self):
		account = get_account(OUTBOUND_ACCOUNT)
		with (
			patch.object(EmailAccount, "validate_smtp_conn") as validate_smtp,
			patch.object(EmailAccount, "get_smtp_server") as get_smtp,
			patch.object(EmailAccount, "append_email_to_sent_folder") as append,
		):
			self.assertIsNone(account.validate_smtp_conn())
			self.assertIsNone(account.get_smtp_server())
			self.assertIsNone(account.append_email_to_sent_folder(b"raw"))
		validate_smtp.assert_not_called()
		get_smtp.assert_not_called()
		append.assert_not_called()

	def test_no_imap_for_a_cloudflare_domain(self):
		account = get_account(INBOUND_ACCOUNT)
		with (
			patch.object(EmailAccount, "get_inbound_mails") as get_inbound,
			patch.object(EmailAccount, "get_incoming_server") as get_incoming,
		):
			self.assertEqual(account.get_inbound_mails(), [])
			with self.assertRaises(frappe.ValidationError):
				account.get_incoming_server()
		get_inbound.assert_not_called()
		get_incoming.assert_not_called()

	def test_other_accounts_use_core(self):
		account = plain_account()
		self.assertFalse(account._is_cloudflare_domain())
		with (
			patch.object(EmailAccount, "validate_smtp_conn", return_value="smtp-ok") as validate_smtp,
			patch.object(EmailAccount, "get_smtp_server", return_value="server") as get_smtp,
			patch.object(EmailAccount, "append_email_to_sent_folder") as append,
			patch.object(EmailAccount, "get_inbound_mails", return_value=["mail"]) as get_inbound,
			patch.object(EmailAccount, "get_incoming_server", return_value="imap") as get_incoming,
			patch.object(EmailAccount, "validate") as validate,
		):
			self.assertEqual(account.validate_smtp_conn(), "smtp-ok")
			self.assertEqual(account.get_smtp_server(), "server")
			account.append_email_to_sent_folder(b"raw")
			self.assertEqual(account.get_inbound_mails(), ["mail"])
			self.assertEqual(account.get_incoming_server(True, "ALL"), "imap")
			account.validate()
		validate_smtp.assert_called_once_with()
		get_smtp.assert_called_once_with()
		append.assert_called_once_with(b"raw")
		get_inbound.assert_called_once_with()
		get_incoming.assert_called_once_with(True, "ALL")
		validate.assert_called_once_with()
		self.assertFalse(account.cf_webhook_key)


class TestEmailDomain(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fixtures.ensure_fixtures()
		cls.addClassCleanup(fixtures.remove_fixtures)

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def test_controller_is_composed(self):
		domain = frappe.get_doc("Email Domain", DOMAIN)
		self.assertIsInstance(domain, CloudflareEmailDomain)
		self.assertIsInstance(domain, EmailDomain)

	def test_no_imap_check_for_a_cloudflare_domain(self):
		with patch.object(EmailDomain, "validate_incoming_server_conn") as core:
			self.assertIsNone(get_domain().validate_incoming_server_conn())
		core.assert_not_called()

	def test_verifies_the_api_token_instead_of_smtp(self):
		domain = get_domain()
		with (
			patch.object(EmailDomain, "validate_outgoing_server_conn") as core,
			patch.object(cloudflare_api, "verify_token", return_value={"status": "active"}) as verify,
		):
			domain.validate_outgoing_server_conn()
		core.assert_not_called()
		verify.assert_called_once_with(API_TOKEN)

	def test_rejected_token(self):
		domain = get_domain()
		error = CloudflareEmailError("Invalid API Token")
		with (
			patch.object(cloudflare_api, "verify_token", side_effect=error),
			self.assertRaisesRegex(frappe.ValidationError, "Invalid API Token"),
		):
			domain.validate_outgoing_server_conn()

	def test_missing_credentials(self):
		domain = get_domain()
		domain.cf_account_id = ""
		with (
			patch.object(cloudflare_api, "verify_token") as verify,
			self.assertRaises(frappe.ValidationError),
		):
			domain.validate_outgoing_server_conn()
		verify.assert_not_called()

	def test_other_domains_use_core(self):
		domain = get_domain()
		domain.send_via_cloudflare = 0
		with (
			patch.object(EmailDomain, "validate_incoming_server_conn", return_value="in") as incoming,
			patch.object(EmailDomain, "validate_outgoing_server_conn", return_value="out") as outgoing,
		):
			self.assertEqual(domain.validate_incoming_server_conn(), "in")
			self.assertEqual(domain.validate_outgoing_server_conn(), "out")
		incoming.assert_called_once_with()
		outgoing.assert_called_once_with()

	def test_cloudflare_settings(self):
		account = get_account(OUTBOUND_ACCOUNT)
		self.assertEqual(
			email_domain.get_cloudflare_settings(account), {"account_id": ACCOUNT_ID, "api_token": API_TOKEN}
		)
		self.assertIsNone(email_domain.get_cloudflare_settings(None))
		self.assertIsNone(email_domain.get_cloudflare_settings(plain_account()))
		domain = get_domain()
		domain.send_via_cloudflare = 0
		with patch.object(frappe, "get_cached_doc", return_value=domain):
			self.assertIsNone(email_domain.get_cloudflare_settings(account))
		domain.send_via_cloudflare = 1
		domain.cf_account_id = ""
		with (
			patch.object(frappe, "get_cached_doc", return_value=domain),
			self.assertRaises(frappe.ValidationError),
		):
			email_domain.get_cloudflare_settings(account)


class TestSendFallback(IntegrationTestCase):
	"""``send_via_fallback``: the transport core would have used for an account that does
	not send via Cloudflare."""

	def queue(self, reference_doctype="ToDo"):
		return frappe._dict(reference_doctype=reference_doctype)

	def test_frappe_mail(self):
		account = MagicMock(service="Frappe Mail")
		email_domain.send_via_fallback(self.queue("Newsletter"), account, "a@x.example", "b@y.example", "m")
		account.get_frappe_mail_client.return_value.send_raw.assert_called_once_with(
			sender="a@x.example", recipients="b@y.example", message="m", is_newsletter=True
		)
		account.get_smtp_server.assert_not_called()

	def test_smtp(self):
		account = MagicMock(service="")
		email_domain.send_via_fallback(self.queue(), account, "a@x.example", "b@y.example", "m")
		account.get_smtp_server.return_value.session.sendmail.assert_called_once_with(
			from_addr="a@x.example", to_addrs="b@y.example", msg=b"m"
		)

	def test_no_smtp_server(self):
		account = MagicMock(service="")
		account.name = "_Test No SMTP"
		account.get_smtp_server.return_value = None
		with self.assertRaises(frappe.ValidationError):
			email_domain.send_via_fallback(self.queue(), account, "a@x.example", "b@y.example", "m")
