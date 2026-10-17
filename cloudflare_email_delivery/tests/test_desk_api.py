# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The Email Account form's webhook helpers: System Manager only, and regenerating the
secret keeps the key (and so the webhook URL)."""

from typing import cast

import frappe
from frappe.tests import IntegrationTestCase

from cloudflare_email_delivery import api
from cloudflare_email_delivery.cloudflare_email_delivery.custom.email_account import CloudflareEmailAccount
from cloudflare_email_delivery.tests import fixtures
from cloudflare_email_delivery.tests.fixtures import INBOUND_ACCOUNT

SMTP_ACCOUNT = "_Test CF Desk SMTP"


def get_account(name: str) -> CloudflareEmailAccount:
	return cast(CloudflareEmailAccount, frappe.get_doc("Email Account", name))


class TestDeskApi(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fixtures.ensure_fixtures()
		cls.addClassCleanup(fixtures.remove_fixtures)

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def test_get_webhook_secret(self):
		account = get_account(INBOUND_ACCOUNT)
		self.assertEqual(api.get_webhook_secret(INBOUND_ACCOUNT), account.get_password("cf_webhook_secret"))

	def test_only_a_system_manager(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			api.get_webhook_secret(INBOUND_ACCOUNT)
		with self.assertRaises(frappe.PermissionError):
			api.regenerate_webhook_secret(INBOUND_ACCOUNT)

	def test_regenerate_webhook_secret(self):
		before = get_account(INBOUND_ACCOUNT)
		old_secret = before.get_password("cf_webhook_secret")
		frappe.clear_messages()
		api.regenerate_webhook_secret(INBOUND_ACCOUNT)
		after = get_account(INBOUND_ACCOUNT)
		new_secret = after.get_password("cf_webhook_secret")
		self.assertNotEqual(new_secret, old_secret)
		self.assertEqual(len(new_secret), 43)
		self.assertEqual(after.cf_webhook_key, before.cf_webhook_key)
		self.assertEqual(after.cf_webhook_url, before.cf_webhook_url)
		self.assertEqual(after.cf_webhook_secret, "*" * len(new_secret))
		self.assertEqual(api.get_webhook_secret(INBOUND_ACCOUNT), new_secret)
		self.assertTrue(frappe.message_log)

	def test_regenerate_refuses_an_account_that_is_not_ours(self):
		frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": SMTP_ACCOUNT,
				"email_id": "desk@smtp-test.example",
				"enable_outgoing": 1,
				"smtp_server": "localhost",
				"smtp_port": 2525,
				"no_smtp_authentication": 1,
				"awaiting_password": 1,
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			api.regenerate_webhook_secret(SMTP_ACCOUNT)
