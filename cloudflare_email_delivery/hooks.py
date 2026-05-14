from . import __version__ as app_version

app_name = "cloudflare_email_delivery"
app_title = "Cloudflare Email Delivery"
app_publisher = "Avunu LLC"
app_description = "Send outgoing email via Cloudflare Email Sending API"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "mail@avunu.net"
app_license = "MIT"

override_email_send = ["cloudflare_email_delivery.controller.send"]
