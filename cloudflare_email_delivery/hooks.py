# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

from . import __version__

app_color = "grey"
app_description = "Send and receive email via Cloudflare Email Sending and the Cloudflare email relay"
app_email = "mail@avu.nu"
app_icon = "octicon octicon-file-directory"
app_license = "MIT"
app_name = "cloudflare_email_delivery"
app_publisher = "Avunu LLC"
app_title = "Cloudflare Email Delivery"
app_version = __version__
# Extensions, not overrides: each class subclasses the stock controller and calls super(),
# so it stacks with any other app that extends or overrides the same DocType.
extend_doctype_class = {
	"Email Domain": [
		"cloudflare_email_delivery.cloudflare_email_delivery.custom.email_domain.CloudflareEmailDomain"
	],
	"Email Account": [
		"cloudflare_email_delivery.cloudflare_email_delivery.custom.email_account.CloudflareEmailAccount"
	],
}
override_email_send = ["cloudflare_email_delivery.cloudflare_email_delivery.custom.email_domain.send"]
doctype_js = {"Email Account": "public/js/email_account.js"}
# The least-privilege user the relay webhook processes mail as (relay_user.py).
after_install = "cloudflare_email_delivery.relay_user.ensure_relay_user"
after_migrate = "cloudflare_email_delivery.relay_user.ensure_relay_user"
before_uninstall = "cloudflare_email_delivery.relay_user.remove_relay_user"
