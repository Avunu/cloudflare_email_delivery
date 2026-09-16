// Copyright (c) 2026, Avunu LLC and contributors
// For license information, please see license.txt

// Webhook credentials on a Cloudflare-domain Email Account: copy the URL (anyone who can see
// the form), copy or regenerate the secret (System Manager). Core's "Pull Emails" button is
// meaningless here — the relay pushes — so it goes.
frappe.ui.form.on("Email Account", {
	refresh(frm) {
		if (!frm.doc.cf_enabled || !frm.doc.enable_incoming || frm.is_new()) {
			return;
		}
		frm.remove_custom_button(__("Pull Emails"));
		const group = __("Cloudflare Webhook");
		frm.add_custom_button(
			__("Copy Webhook URL"),
			() => frappe.utils.copy_to_clipboard(frm.doc.cf_webhook_url),
			group
		);
		if (!frappe.user.has_role("System Manager")) {
			return;
		}
		frm.add_custom_button(
			__("Copy Webhook Secret"),
			() =>
				frappe
					.call({
						method: "cloudflare_email_delivery.api.get_webhook_secret",
						args: { email_account: frm.doc.name },
					})
					.then((r) => frappe.utils.copy_to_clipboard(r.message)),
			group
		);
		frm.add_custom_button(
			__("Regenerate Webhook Secret"),
			() =>
				frappe.confirm(
					__(
						"Requests signed with the current secret will be rejected until the relay's tenant secret is updated. Continue?"
					),
					() =>
						frappe
							.call({
								method: "cloudflare_email_delivery.api.regenerate_webhook_secret",
								args: { email_account: frm.doc.name },
							})
							.then(() => frm.reload_doc())
				),
			group
		);
	},
});
