# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

from . import __version__

app_color = "grey"
app_description = "Send outgoing email via Cloudflare Email Sending API"
app_email = "mail@cloudflare_email_delivery.net"
app_icon = "octicon octicon-file-directory"
app_license = "MIT"
app_name = "cloudflare_email_delivery"
app_publisher = "Avunu LLC"
app_title = "Cloudflare Email Delivery"
app_version = __version__
override_doctype_class = {
	"Email Domain": "cloudflare_email_delivery.cloudflare_email_delivery.custom.email_domain.EmailDomain",
	"Email Account": "cloudflare_email_delivery.cloudflare_email_delivery.custom.email_account.EmailAccount",
}
override_email_send = ["cloudflare_email_delivery.cloudflare_email_delivery.custom.email_domain.send"]
