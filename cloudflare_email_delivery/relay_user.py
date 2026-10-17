# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt
"""The least-privilege user the relay webhook processes mail as.

The webhook is a guest request, and core's ``InboundMail.process`` saves the
attachments (``File.save``) and re-saves the new Communication without
``ignore_permissions``, so it cannot run as Guest. Rather than run it as
Administrator, the webhook switches to this user for the processing only:

- a disabled Website User (it can never log in, reset a password or hold an API
  key) whose only role is ``Cloudflare Email Relay``, a role without desk access;
- one extra permission rule: that role may read and write the Communications it
  owns (``if_owner``). Core's "All" rule already lets every user create Files and
  read and delete its own Communications; a File attached to a Communication is
  allowed when the user owns the File.

Everything else ``InboundMail`` writes (the Communication insert, the reference
document, contacts, the Email Queue) core writes with ``ignore_permissions``
itself, exactly as it does when the scheduler pulls IMAP mail.

``ensure_relay_user`` runs on install and on every migrate and resets the user to
exactly this shape, so a role added by hand does not survive a migrate.
"""

from collections.abc import Generator
from contextlib import contextmanager

import frappe
from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
from frappe.permissions import setup_custom_perms

RELAY_USER = "cloudflare-email-relay@relay.invalid"
RELAY_ROLE = "Cloudflare Email Relay"
# The one rule the role adds on top of core's: ``{doctype: rights}``, owner-only.
OWNER_PERMISSIONS = {"Communication": ("read", "write")}


class RelayUserMissing(Exception):
	"""The relay user does not exist yet: the site has not been migrated."""


def _ensure_role() -> None:
	if frappe.db.exists("Role", RELAY_ROLE):
		role = frappe.get_doc("Role", RELAY_ROLE)
	else:
		role = frappe.new_doc("Role", role_name=RELAY_ROLE)
	role.update({"desk_access": 0, "disabled": 0, "two_factor_auth": 0, "home_page": None})
	role.save(ignore_permissions=True)


def _ensure_user() -> None:
	if frappe.db.exists("User", RELAY_USER):
		user = frappe.get_doc("User", RELAY_USER)
	else:
		user = frappe.new_doc("User", email=RELAY_USER, first_name=RELAY_ROLE, send_welcome_email=0)
	user.flags.no_welcome_mail = True
	user.update(
		{
			"enabled": 0,
			"user_type": "Website User",
			"roles": [{"role": RELAY_ROLE}],
			# User.validate re-adds every role of an assigned Role Profile, so a profile would
			# survive the reset above: clear profiles and everything else that grants access.
			"role_profiles": [],
			"role_profile_name": None,
			"module_profile": None,
			"user_emails": [],
			"api_key": None,
		}
	)
	user.save(ignore_permissions=True)
	# A document shared with the user widens its access past the role too.
	frappe.db.delete("DocShare", {"user": RELAY_USER})
	# The user may have had other roles cached from before the reset.
	frappe.clear_cache(user=RELAY_USER)


_PERM_RIGHTS = (
	"select",
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"export",
	"import",
	"share",
	"print",
	"email",
)


def _ensure_permissions() -> None:
	for doctype, rights in OWNER_PERMISSIONS.items():
		# Custom DocPerm replaces a doctype's standard rules wholesale, so the standard
		# rules are copied first (core's add_permission does the same).
		setup_custom_perms(doctype)
		rows = frappe.get_all("Custom DocPerm", {"parent": doctype, "role": RELAY_ROLE}, pluck="name")
		for extra in rows[1:]:
			frappe.delete_doc("Custom DocPerm", extra, ignore_permissions=True, force=True)
		perm = frappe.get_doc("Custom DocPerm", rows[0]) if rows else frappe.new_doc("Custom DocPerm")
		perm.update(
			{
				"parent": doctype,
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": RELAY_ROLE,
				"permlevel": 0,
				"if_owner": 1,
			}
		)
		for right in _PERM_RIGHTS:
			perm.set(right, int(right in rights))
		perm.save(ignore_permissions=True)
		validate_permissions_for_doctype(doctype)
		frappe.clear_cache(doctype=doctype)


def ensure_relay_user() -> None:
	"""Create, or reset to least privilege, the relay role, user and permission rule.

	Idempotent; the ``after_install`` and ``after_migrate`` hook."""
	_ensure_role()
	_ensure_user()
	_ensure_permissions()


def remove_relay_user() -> None:
	"""Undo ``ensure_relay_user``; the ``before_uninstall`` hook."""
	for doctype in OWNER_PERMISSIONS:
		for name in frappe.get_all("Custom DocPerm", {"parent": doctype, "role": RELAY_ROLE}, pluck="name"):
			frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)
		frappe.clear_cache(doctype=doctype)
	frappe.delete_doc("User", RELAY_USER, ignore_permissions=True, force=True, ignore_missing=True)
	frappe.delete_doc("Role", RELAY_ROLE, ignore_permissions=True, force=True, ignore_missing=True)


@contextmanager
def acting_as_relay_user() -> Generator[str]:
	"""Run the block as the relay user and always hand the session back.

	``frappe.set_user`` also resets the session id, session data and form dict, so
	those are restored too: the request ends exactly as it started."""
	if not frappe.db.exists("User", RELAY_USER):
		raise RelayUserMissing(RELAY_USER)
	session = frappe.local.session
	previous = (session.user, session.sid, session.data, frappe.local.form_dict)
	# Reviewed: the narrow switch this module exists for. The relay user holds only the role above,
	# and the finally block below restores the caller's session whatever the block does.
	frappe.set_user(RELAY_USER)  # nosemgrep: frappe-setuser
	try:
		yield RELAY_USER
	finally:
		user, sid, data, form_dict = previous
		# Reviewed: hands the session back to the caller (Guest, for the webhook).
		frappe.set_user(user)  # nosemgrep: frappe-setuser
		session.sid, session.data = sid, data
		frappe.local.form_dict = form_dict
