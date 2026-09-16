# Cloudflare Email Delivery

Send and receive Frappe / ERPNext email through Cloudflare, with no SMTP or IMAP provider in the
loop. Outbound mail goes to the **Cloudflare Email Sending** REST API straight from Frappe's
`override_email_send` hook; inbound mail arrives from the **Cloudflare email relay** (the
[`@avunu/cloudflare-email-relay`](https://github.com/Avunu/cloudflare-email-relay) Worker, deployed
per fleet) over a signed HTTPS webhook and is fed to Frappe's own `InboundMail` — the same path IMAP
polling uses, so *Append To*, auto-replies, attachments, notifications and reply threading all
behave as they do for a mailbox.

## What it does

**Outbound.** An Email Domain with *Send via Cloudflare* holds an account id and an API token
instead of SMTP settings. Every Email Queue entry sent from an account on that domain becomes one
REST call per recipient: the `From`, the recipient, `Reply-To`, text and HTML bodies, attachments
(inline ones by `Content-ID`), and the headers Cloudflare allows (`In-Reply-To`, `References`,
`X-*`, `Precedence`, …). Frappe keeps its own `Message-Id` — the value Email Queue and
Communication store — and adds it to `References`, because Cloudflare assigns a `Message-ID` of its
own. Rate limits and transient errors are retried briefly; an invalid token fails the rest of the
batch fast; recipients Cloudflare bounces or suppresses synchronously fail the entry. Accounts on
other domains keep using SMTP or Frappe Mail as before.

**Inbound.** An Email Account on a Cloudflare domain with *Enable Incoming* has no IMAP server; it
gets a **Webhook URL** and a **Webhook Secret** the moment it is saved. The relay posts every
message routed to the account's domain to that URL, signed; the app verifies the signature, checks
the recipient domain is the account's own, and hands the raw message to `InboundMail` with the
account's *Append To*. Replies to Cloudflare-sent mail thread correctly even though Cloudflare
replaced the `Message-ID`: the app matches `References` against the ids it issued or stored.

## Requirements

- Frappe 16.
- A Cloudflare account on the Workers Paid plan with the domain onboarded for **Email Sending**
  and **Email Routing** enabled on the zone.
- An API token with *Email Sending: Edit* for outbound.
- For inbound: the site reachable over https, with `host_name` set in its site config (the
  webhook URL is built from it), and a tenant entry in the relay fleet.

## Setup

### Sending

1. *Email Domain*: tick **Send via Cloudflare**, fill **Cloudflare Account ID** and **Cloudflare
   API Token**. Saving verifies the token.
2. *Email Account*: link the domain, set the address, tick **Enable Outgoing**. No password, no
   SMTP.

### Receiving

1. On the same Email Account tick **Enable Incoming**, choose **Append To** (ToDo, Issue, Lead, …)
   and the usual options (auto-reply, attachment limit, notifications). Save.
2. The **Cloudflare Email Receiving** section now shows the **Webhook URL** (*Copy Webhook URL*)
   and, for System Managers, *Copy Webhook Secret*. Hand both to the relay fleet's onboarding as
   the tenant's `inboundUrl` and `secret`, together with the domain(s) whose mail should reach this
   site.
3. In the zone's **Email Routing**, route the addresses (or the catch-all) to the relay Worker.

*Regenerate Webhook Secret* rotates the secret only; the URL stays, so only the tenant's relay
secret needs updating afterwards (deliveries in between park as `rejected` on the relay and can be
bulk-retried).

### The webhook contract

`POST /api/method/cloudflare_email_delivery.api.inbound?key=<key>`, body `message/rfc822`, headers:

| Header | Meaning |
| --- | --- |
| `X-Email-Relay-Id` | the relay's queue id (echoed back; deduplicates retries of a message without a `Message-ID`) |
| `X-Email-Relay-Tenant` | the tenant slug the relay routed by (logged) |
| `X-Email-Relay-Timestamp` | Unix seconds; rejected outside ± 300 s |
| `X-Email-Relay-Signature` | `v1=` + hex `HMAC-SHA256(secret, "<timestamp>." + body)` |
| `X-Email-Relay-Envelope-From` / `-To` | the SMTP envelope; written into the message as `Return-Path` / `Delivered-To` when absent |

| Response | Meaning | Relay reaction |
| --- | --- | --- |
| `200 {"message": {"ok": true, "remote_ref": "<Communication>" or null}}` | processed (`null`: our own mail looped back, deliberately ignored) | delivered |
| `401` | bad or missing signature, stale timestamp | parked as rejected |
| `404` | unknown key, or incoming disabled on the account | parked as rejected |
| `422` | recipient domain not this account's, invalid envelope, or the reference document refused the message | parked as rejected |
| `500` | anything else — logged to Error Log and stored as an Unhandled Email | retried with back-off |

The endpoint is a guest method; the HMAC is the credential. A message larger than the site's
`max_file_size` (25 MiB by default) is refused by the web server before it reaches the app.

## Limitations

- Asynchronous bounces never reach Frappe: `Return-Path` is Cloudflare's and bounces go to its own
  `cf-bounce` subdomain. Only the recipients Cloudflare rejects synchronously are surfaced.
- Cloudflare rewrites `Message-ID`; reply matching relies on the replying client propagating
  `References`, which every mainstream client does.
- Limits: 5 MiB per message, 50 recipients, 20 custom headers of 2 KB each; headers outside
  Cloudflare's allow-list (`Disposition-Notification-To`, …) are dropped.
- Bcc'd recipients receive their copy but are not listed anywhere in the delivered message
  (Frappe sends each recipient their own copy).

## Tests

```sh
bench --site test_site install-app cloudflare_email_delivery
bench --site test_site run-tests --app cloudflare_email_delivery
```

`tests/test_relay.py` and `tests/test_cloudflare_api.py` are plain `unittest` (no site needed:
`python -m unittest cloudflare_email_delivery.tests.test_relay`). The site-backed suites cover the
webhook (in-process and through the real WSGI app), threading and the send hook. On a frappe-nix dev
bench the mail guard must be off for the process (`FRAPPE_DEVGUARD_DISABLE=mail`): it deliberately
strips the very overrides these tests exercise.

## How it works

| Hook | Purpose |
| --- | --- |
| `override_doctype_class` → `EmailDomain` | verify the API token instead of SMTP/IMAP connections |
| `override_doctype_class` → `EmailAccount` | blank the server fields, generate the webhook key + secret, build the URL, no-op the IMAP pull |
| `override_email_send` → `email_domain.send` | the Email Sending API call, with Frappe Mail / SMTP fallback for other accounts |
| `doctype_js` → `email_account.js` | the copy / regenerate buttons |
| `api.inbound` | the webhook |

`cloudflare_api.py` (the API client) is shared with the Odoo module `mail_cloudflare` in
[Avunu/avunu-odoo-addons](https://github.com/Avunu/avunu-odoo-addons); `relay.py` implements the
contract documented in the relay's README.

## Related

- [`Avunu/cloudflare-email-relay`](https://github.com/Avunu/cloudflare-email-relay) — the Worker
  and the full wire contract.
- [`Avunu/cloudflare-email-workers`](https://github.com/Avunu/cloudflare-email-workers) — the fleet
  that deploys it and onboards tenants.

## License

MIT. `cloudflare_email_delivery/cloudflare_api.py` derives from the AGPL-3.0 Odoo module of the
same author, relicensed here by its copyright holder, Avunu LLC.
