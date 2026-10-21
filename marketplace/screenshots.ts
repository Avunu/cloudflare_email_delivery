// Copyright (c) 2026, Avunu LLC and contributors
// For license information, please see license.txt

import type { ShotSpec } from "./shots.d.ts";

// The demo from cloudflare_email_delivery/demo.py: the example.com Email Domain, the Support
// Email Account on it, and a ToDo made from a support conversation the relay delivered.
const formReady = (doctype: string, name: string) =>
	`window.cur_frm && cur_frm.doctype === ${JSON.stringify(doctype)} && cur_frm.doc.name === ${JSON.stringify(name)} && !cur_frm.is_dirty()`;

// The ToDo's name is generated when the first message arrives; open it by its subject.
const OPEN_TODO = `frappe.db.get_value("ToDo", { description: "Order 1042 arrived with a damaged lid" }, "name")
	.then((r) => frappe.set_route("Form", "ToDo", r.message.name));`;

export default {
	demo: { date: "2026-01-15", seed: 1 },
	shots: [
		{
			name: "webhook",
			route: "/app/email-account/Support",
			alt: "An Email Account on a Cloudflare domain: its Incoming tab shows the Webhook URL, key and secret for the relay, and the Cloudflare Webhook buttons",
			waitForFn: formReady("Email Account", "Support"),
			actions: [
				{ eval: 'cur_frm.scroll_to_field("cf_webhook_url");' },
				{ waitFor: '[data-fieldname="cf_webhook_url"] .control-value' },
				{ click: '.inner-group-button[data-label="Cloudflare%20Webhook"] button' },
				{ wait: 500 },
			],
			mask: [".frappe-timestamp"],
			readme: "hero",
			featured: true,
		},
		{
			name: "sending-domain",
			route: "/app/email-domain/example.com",
			alt: "An Email Domain set to Send via Cloudflare, with the Cloudflare account ID and API token in place of SMTP settings",
			waitForFn: formReady("Email Domain", "example.com"),
			actions: [{ eval: 'cur_frm.scroll_to_field("send_via_cloudflare");' }, { wait: 500 }],
			mask: [".frappe-timestamp"],
			readme: "feature",
			featured: true,
		},
		{
			name: "threaded-reply",
			route: "/app/todo",
			alt: "A ToDo created from a customer's e-mail, with the reply sent from the desk and the customer's answer threaded in its timeline",
			waitForFn: "window.cur_list && cur_list.doctype === 'ToDo' && !!document.querySelector('.list-row')",
			actions: [
				{ eval: OPEN_TODO },
				{ waitFor: ".new-timeline .timeline-item .timeline-message-box" },
				{ eval: 'document.querySelector(".new-timeline").scrollIntoView({ block: "start" });' },
				{ wait: 500 },
			],
			mask: [".frappe-timestamp"],
			// The ToDo's generated name changes on every run.
			hide: [".form-name-container"],
			readme: "feature",
			featured: true,
		},
	],
} satisfies ShotSpec;
