# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The demo hook frappe-demo calls (``cloudflare_email_delivery.demo.setup``)."""

import datetime

import frappe
from frappe.tests import IntegrationTestCase

from cloudflare_email_delivery import demo
from cloudflare_email_delivery.relay_user import RELAY_USER

CTX = {"today": datetime.date(2026, 1, 15), "seed": 1, "company": None, "erpnext_demo": False}


class TestDemo(IntegrationTestCase):
	def test_setup_makes_a_cloudflare_domain_and_account(self):
		host_name = frappe.local.conf.get("host_name")
		demo.setup(CTX)
		self.assertEqual(frappe.db.get_value("Email Domain", demo.DOMAIN, "send_via_cloudflare"), 1)
		url = frappe.db.get_value("Email Account", demo.ACCOUNT, "cf_webhook_url")
		assert isinstance(url, str)
		self.assertTrue(
			url.startswith(f"{demo.HOST_NAME}/api/method/cloudflare_email_delivery.api.inbound?key=")
		)
		self.assertEqual(frappe.local.conf.get("host_name"), host_name)

	def test_setup_threads_the_answer_onto_the_todo(self):
		demo.setup(CTX)
		rows = frappe.get_all(
			"Communication",
			filters={"email_account": demo.ACCOUNT},
			fields=["sent_or_received", "reference_doctype", "reference_name", "owner"],
			order_by="communication_date asc",
		)
		self.assertEqual([r.sent_or_received for r in rows], ["Received", "Sent", "Received"])
		self.assertEqual({r.reference_doctype for r in rows}, {"ToDo"})
		self.assertEqual(len({r.reference_name for r in rows}), 1)
		self.assertEqual(rows[-1].owner, RELAY_USER)

	def test_setup_is_idempotent(self):
		demo.setup(CTX)
		demo.setup(CTX)
		self.assertEqual(frappe.db.count("Email Account", {"name": demo.ACCOUNT}), 1)
		self.assertEqual(frappe.db.count("Communication", {"email_account": demo.ACCOUNT}), 3)
