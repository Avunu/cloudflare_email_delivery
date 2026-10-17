# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The relay user: its shape (least privilege), the install/migrate hooks that keep
it that way, and the session switch the webhook makes."""

from typing import cast

import frappe
from frappe.core.doctype.user.user import User
from frappe.tests import IntegrationTestCase

from cloudflare_email_delivery import hooks, relay_user
from cloudflare_email_delivery.relay_user import RELAY_ROLE, RELAY_USER


class TestRelayUser(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		relay_user.ensure_relay_user()

	def tearDown(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		super().tearDown()

	def relay_rules(self, doctype="Communication"):
		return frappe.get_all(
			"Custom DocPerm",
			{"parent": doctype, "role": RELAY_ROLE},
			["permlevel", "if_owner", "read", "write", "create", "delete", "share", "export", "report"],
		)

	def test_hooks_are_registered(self):
		self.assertEqual(hooks.after_install, "cloudflare_email_delivery.relay_user.ensure_relay_user")
		self.assertEqual(hooks.after_migrate, "cloudflare_email_delivery.relay_user.ensure_relay_user")
		self.assertEqual(hooks.before_uninstall, "cloudflare_email_delivery.relay_user.remove_relay_user")

	def test_the_user_is_least_privilege(self):
		user = cast(User, frappe.get_doc("User", RELAY_USER))
		self.assertFalse(user.enabled)
		self.assertEqual(user.user_type, "Website User")
		self.assertEqual([row.role for row in user.roles], [RELAY_ROLE])
		self.assertEqual(set(frappe.get_roles(RELAY_USER)), {RELAY_ROLE, "All", "Guest"})
		self.assertFalse(frappe.db.get_value("Role", RELAY_ROLE, "desk_access"))
		self.assertFalse(user.api_key)

	def test_the_role_adds_one_owner_only_rule(self):
		[rule] = self.relay_rules()
		self.assertEqual(
			dict(rule),
			{
				"permlevel": 0,
				"if_owner": 1,
				"read": 1,
				"write": 1,
				"create": 0,
				"delete": 0,
				"share": 0,
				"export": 0,
				"report": 0,
			},
		)
		self.assertFalse(
			frappe.get_all(
				"Custom DocPerm",
				{"role": RELAY_ROLE, "parent": ("!=", "Communication")},
				pluck="name",
			)
		)
		# Copying the standard rules first keeps everyone else's access as it was.
		self.assertTrue(
			frappe.db.exists("Custom DocPerm", {"parent": "Communication", "role": "System Manager"})
		)

	def test_writes_only_its_own_communications(self):
		def communication(owner):
			doc = frappe.get_doc(
				{
					"doctype": "Communication",
					"communication_medium": "Email",
					"subject": "Relay user perms",
					"sent_or_received": "Received",
				}
			).insert(ignore_permissions=True)
			doc.db_set("owner", owner)
			doc.reload()
			return doc

		own, others = communication(RELAY_USER), communication("Administrator")

		self.assertTrue(frappe.has_permission("Communication", "write", own, user=RELAY_USER))
		self.assertFalse(frappe.has_permission("Communication", "write", others, user=RELAY_USER))
		self.assertFalse(frappe.has_permission("Communication", "read", others, user=RELAY_USER))
		self.assertFalse(frappe.has_permission("Email Account", "read", user=RELAY_USER))
		self.assertFalse(frappe.has_permission("System Settings", "write", user=RELAY_USER))

	def test_ensure_resets_drift(self):
		user = cast(User, frappe.get_doc("User", RELAY_USER))
		user.append("roles", {"role": "System Manager"})
		user.enabled = 1
		user.save(ignore_permissions=True)
		frappe.db.set_value("Custom DocPerm", {"parent": "Communication", "role": RELAY_ROLE}, "delete", 1)

		relay_user.ensure_relay_user()
		relay_user.ensure_relay_user()  # idempotent

		user.reload()
		self.assertFalse(user.enabled)
		self.assertEqual([row.role for row in user.roles], [RELAY_ROLE])
		self.assertNotIn("System Manager", frappe.get_roles(RELAY_USER))
		[rule] = self.relay_rules()
		self.assertEqual((rule.delete, rule.write), (0, 1))

	def test_remove_then_ensure(self):
		relay_user.remove_relay_user()
		self.assertFalse(frappe.db.exists("User", RELAY_USER))
		self.assertFalse(frappe.db.exists("Role", RELAY_ROLE))
		self.assertFalse(self.relay_rules())
		relay_user.ensure_relay_user()
		self.assertTrue(frappe.db.exists("User", RELAY_USER))
		self.assertEqual(len(self.relay_rules()), 1)

	def test_acting_as_restores_the_session(self):
		frappe.set_user("Guest")
		session = frappe.local.session
		form_dict = frappe.local.form_dict = frappe._dict(key="value")
		data = session.data
		with relay_user.acting_as_relay_user() as user:
			self.assertEqual(user, RELAY_USER)
			self.assertEqual(frappe.session.user, RELAY_USER)
			self.assertNotIn("Administrator", frappe.get_roles())
		self.assertEqual((frappe.session.user, frappe.session.sid), ("Guest", "Guest"))
		self.assertIs(frappe.local.form_dict, form_dict)
		self.assertIs(frappe.session.data, data)

	def test_acting_as_restores_the_session_on_error(self):
		frappe.set_user("Guest")
		with self.assertRaises(RuntimeError), relay_user.acting_as_relay_user():
			raise RuntimeError("boom")
		self.assertEqual(frappe.session.user, "Guest")

	def test_acting_as_without_the_user(self):
		relay_user.remove_relay_user()
		frappe.set_user("Guest")
		with self.assertRaises(relay_user.RelayUserMissing), relay_user.acting_as_relay_user():
			pass
		self.assertEqual(frappe.session.user, "Guest")
