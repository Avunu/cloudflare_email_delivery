<!-- Copyright (c) 2026, Avunu LLC and contributors
For license information, please see license.txt-->

# Cloudflare Email Delivery

A [Frappe](https://frappeframework.com) app that routes outgoing email through the [Cloudflare Email Sending API](https://developers.cloudflare.com/email-routing/email-workers/send-email-workers/) instead of a traditional SMTP server.

## Features

- Send outgoing email via Cloudflare's transactional email API
- Per-domain configuration — enable Cloudflare sending on specific Email Domains only; all other domains continue to use their normal SMTP settings
- Skips SMTP/IMAP connection validation for Cloudflare-enabled domains, so no email password is required on the account
- API token validation on Email Domain save (calls `GET /user/tokens/verify`)
- Automatic fallback to Frappe's standard SMTP path for non-Cloudflare accounts
- Full attachment support (inline and attached)

## Requirements

- Frappe v15+
- A Cloudflare account with [Email Routing](https://developers.cloudflare.com/email-routing/) enabled and an API token with **Email Routing Write** permissions

## Installation

```bash
bench get-app https://github.com/Avunu/cloudflare_email_delivery
bench --site <your-site> install-app cloudflare_email_delivery
```

## Configuration

### 1. Configure an Email Domain

1. Go to **Email Domain** and open (or create) the domain you want to send from.
2. Check **Send via Cloudflare**.
3. Enter your **Cloudflare Account ID** (found in the Cloudflare dashboard sidebar).
4. Enter your **Cloudflare API Token** — the token must have the *Email Routing: Edit* permission.
5. Save. Frappe will verify the token against the Cloudflare API before saving.

### 2. Configure an Email Account

1. Go to **Email Account** and open (or create) an outgoing account linked to the domain above.
2. Set **Domain** to the Email Domain configured in step 1.
3. Enable **Enable Outgoing**.
4. No SMTP server or password is required — leave those fields blank.
5. Save.

## How It Works

The app registers three overrides:

| Hook | Purpose |
|---|---|
| `override_doctype_class["Email Domain"]` | Replaces IMAP/SMTP validation with a Cloudflare token check when *Send via Cloudflare* is enabled |
| `override_doctype_class["Email Account"]` | Skips SMTP connection validation and password requirement for Cloudflare-backed accounts |
| `override_email_send` | Intercepts every outgoing email; builds a JSON payload and POSTs it to `POST /accounts/{id}/email/sending/send`; falls back to SMTP for non-Cloudflare senders |

## License

MIT
