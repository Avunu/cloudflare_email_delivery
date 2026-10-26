---
title: Cloudflare Email Delivery
description: Send and receive Frappe email through Cloudflare, with no SMTP or IMAP server to run or poll.
nav_title: Overview
order: 0
updated: 2026-10-26
---

Running email for a Frappe or ERPNext site usually means SMTP credentials for sending, an IMAP mailbox the scheduler polls for receiving, and a mail provider in between. Cloudflare Email Delivery replaces both for sites whose domains are on Cloudflare. Outgoing mail goes straight from Frappe to the Cloudflare Email Sending API. Incoming mail is pushed to the site by the [Cloudflare email relay](https://github.com/Avunu/cloudflare-email-relay), a Cloudflare Worker, over a signed HTTPS webhook, and Frappe processes it the way it processes mail it pulls from a mailbox: **Append To**, auto-replies, attachments, notifications and reply threading work as before.

It is for administrators of Frappe sites who already manage their domains on Cloudflare and want one place for mail. It runs on Frappe v16 and is free software under the [MIT licence](../license.txt).

## What you get

### Sending without SMTP

An Email Domain with **Send via Cloudflare** holds a Cloudflare account ID and API token instead of SMTP settings, and saving it checks the token. Every Email Account on that domain then sends through the Cloudflare API, with attachments, inline images and the threading headers. See [Send mail through Cloudflare](sending.md).

![An Email Domain set to Send via Cloudflare, with the Cloudflare account ID and API token in place of SMTP settings](screenshots/sending-domain-light.webp)

### Receiving without IMAP polling

An incoming Email Account on that domain gets a **Webhook URL** and a **Webhook Secret** when you save it. The relay posts every message to the URL, the app checks its signature and recipient domain, and the message becomes a Communication on the account's **Append To** document. See [Receive mail from the relay](receiving.md).

![An Email Account on a Cloudflare domain: its Incoming tab shows the Webhook URL, key and secret for the relay, and the Cloudflare Webhook buttons](screenshots/webhook-light.webp)

### Replies that land on the right document

Cloudflare replaces the `Message-ID` of every message it sends, which would normally break Frappe's reply matching. The app keeps Frappe's own id in `References` and matches replies on it, so a customer's answer lands in the timeline of the document it belongs to. See [Reply threading](threading.md).

![A ToDo created from a customer's e-mail, with the reply sent from the desk and the customer's answer threaded in its timeline](screenshots/threaded-reply-light.webp)

### A documented webhook

The webhook answers each delivery with a status code that tells the relay whether to retry, park or accept the message, and processes mail as a disabled, least-privilege user instead of Administrator. See [The webhook contract](webhook.md).

## Where to start

| You are                                          | Read                                                                                                                                                             |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A desk user sending and answering email          | [Reply threading](threading.md)                                                                                                                                  |
| The administrator setting it up                  | [Install and upgrade](install.md), [Configuration](configuration.md), [Send mail through Cloudflare](sending.md) and [Receive mail from the relay](receiving.md) |
| The administrator when something goes wrong      | [Limitations and troubleshooting](troubleshooting.md)                                                                                                            |
| Whoever runs the relay, or writes another sender | [The webhook contract](webhook.md)                                                                                                                               |
| A developer changing the app                     | [Development](development/README.md) and [How it works](development/architecture.md)                                                                             |

## Requirements

- A bench with Frappe v16. The app declares `frappe >=16.0.0,<17.0.0` and Python 3.14 or newer. It needs no other Frappe app.
- A Cloudflare account on the Workers Paid plan, with the domain onboarded for Email Sending and Email Routing enabled on its zone.
- A Cloudflare API token with the _Email Sending: Edit_ permission.
- For incoming mail: the site reachable over HTTPS with `host_name` set in its site config, and a deployment of the [Cloudflare email relay](https://github.com/Avunu/cloudflare-email-relay) with a tenant entry for the site.

> [!NOTE]
> These pages describe the code in the repository. Where a statement depends on Frappe's own behaviour, the page says so; it was checked against Frappe 16.52.

## Source and changes

The source is at [github.com/Avunu/cloudflare_email_delivery](https://github.com/Avunu/cloudflare_email_delivery). Release notes are on the [GitHub releases page](https://github.com/Avunu/cloudflare_email_delivery/releases), and release-please writes them to `CHANGELOG.md` with the first release.

The app is one part of Avunu's Cloudflare Email suite, beside the relay, the Odoo module [mail_cloudflare](https://github.com/Avunu/avunu-odoo-addons/tree/18.0/mail_cloudflare) and the [WordPress plugin](https://github.com/Avunu/wordpress-cloudflare-email).
