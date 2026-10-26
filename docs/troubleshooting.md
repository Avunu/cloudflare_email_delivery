---
title: Limitations and troubleshooting
description: What Cloudflare Email Delivery cannot do, and the error messages you may meet when saving, sending or receiving, with their causes and fixes.
nav_title: Troubleshooting
order: 8
updated: 2026-10-26
---

## Limitations

- **Late bounces never reach Frappe.** Cloudflare owns the `Return-Path` of every message and sends bounces to its own `cf-bounce` subdomain. Frappe only learns about recipients Cloudflare refuses while accepting the message: permanent bounces it already knows of and addresses on its suppression list.
- **Threading needs `References`.** Cloudflare rewrites the `Message-ID` of every message it sends, so matching a reply relies on the replying mail program carrying `References`, which every mainstream mail program does. See [Reply threading](threading.md).
- **Cloudflare's sending limits apply.** An outgoing message may be at most 5 MiB once encoded, with at most 20 custom headers, 2,048 bytes per header value and 16 KB of custom headers in all. Headers outside Cloudflare's allow-list, such as `Disposition-Notification-To`, are dropped. Cloudflare also allows at most 50 recipients per message, but Frappe sends each recipient a copy of its own, so that limit does not come into play.
- **Bcc recipients are not listed in the message.** They get their copy, but because Frappe sends each recipient a copy of its own, no delivered message names them.
- **Incoming messages are limited by the site's request size.** Frappe refuses any request larger than `max_file_size` in the site config, 25 MiB when it is not set, and the app refuses anything over 26 MiB. Cloudflare Email Routing accepts messages up to 25 MiB, and the relay adds a few envelope headers, so on a site without `max_file_size` the largest messages can be refused with `413`.
- **One send hook per site.** Frappe calls only the first `override_email_send` hook it finds, in the order the apps were installed. If another app installed before this one registers that hook too, this app's sending is never called.

## Troubleshooting

### Saving an Email Domain

**"Cloudflare Account ID and API Token are required when Send via Cloudflare is enabled."**
Fill in both fields, or untick **Send via Cloudflare**.

**"Cloudflare API token validation failed: …"** (titled "Outgoing email account not correct")
Cloudflare did not confirm the token. The text after the colon says why:

| It continues with                                               | Cause and fix                                                                                        |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| "Cloudflare rejected the API token: HTTP 401 …" or "HTTP 403 …" | The token is wrong or revoked. Create a new token in Cloudflare and paste it in again.               |
| "The API token is not active (status: …)."                      | The token exists but is disabled or expired. Re-enable it in Cloudflare or create a new one.         |
| "Token verification failed: …"                                  | Cloudflare answered with another error, named after the colon. Try again; check Cloudflare's status. |
| "Could not reach Cloudflare: …"                                 | The server could not connect to `api.cloudflare.com`. Check the server's outbound network and proxy. |

### Saving an Email Account

**"Webhook key already in use by Email Account …"**
Two accounts hold the same webhook key, usually because the account was made with **Duplicate** from another one, which copies the key. Create the account as a new document instead; a new account gets a key of its own on its first save.

**"The webhook URL (…) must be https: set `host_name` in the site config to the site's public https address."**
The site's address, as Frappe sees it, is plain `http`. Set `host_name` in the site config to the full `https://` address, for example `bench --site <SITE_NAME> set-config host_name https://erp.example.com`, and save again. The check is skipped on `localhost` and in developer mode.

**"Email Account … receives mail from the Cloudflare email relay; there is no IMAP/POP server."**
Something tried to connect to the account's mailbox, for example a script or another app that pulls mail. A Cloudflare account has no mailbox; its mail arrives through the webhook.

### Sending

When a message fails, open the Email Queue entry. Its **Error** holds the traceback, ending in a line that starts `CloudflareEmailError:`.

| Error                                                                                        | Cause and fix                                                                                                                                                                                                                                                 |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "Cloudflare Account ID and API Token must be configured on Email Domain …."                  | The domain has **Send via Cloudflare** ticked but lost one of the two values. Fill them in on the Email Domain.                                                                                                                                               |
| "Cloudflare refused the message: HTTP 401 …" or "HTTP 403 …"                                 | The token is wrong, revoked, or lacks _Email Sending: Edit_ for this account. Fix the token on the Email Domain. The rest of the batch then fails with "Not retried: an earlier send in this batch failed with …"; the scheduler retries those entries later. |
| "Cloudflare refused the message: HTTP 400 …" or another 4xx                                  | Cloudflare refused this message and does not accept it on a retry. Its own error code and text follow in brackets; look them up in Cloudflare's Email Sending documentation.                                                                                  |
| "Giving up after 3 attempts: …"                                                              | Cloudflare kept answering with a rate limit or a server error, or could not be reached. Frappe retries the entry later; if it persists, check Cloudflare's status and your sending volume.                                                                    |
| "Cloudflare answered HTTP 200 … without success: true"                                       | Cloudflare's answer was not a success. Retry; if it persists, report it with the full error.                                                                                                                                                                  |
| "No recipient could be reached (permanently bounced: …; suppressed: …)."                     | Cloudflare knows the address bounces, or has it on the account's suppression list. Correct the address, or remove it from the suppression list in Cloudflare.                                                                                                 |
| "Message is … MiB once encoded; Cloudflare accepts at most 5 MiB."                           | The message with its attachments is too large. Send the files as links, or fewer at a time.                                                                                                                                                                   |
| "Headers exceed Cloudflare's limits …" or "… custom headers; Cloudflare accepts at most 20." | The message carries too many or too large `X-` headers, usually added by another app. Remove some.                                                                                                                                                            |
| "The message has neither a text nor an html body; Cloudflare requires at least one."         | The message is empty. Add a body.                                                                                                                                                                                                                             |
| "The message has no valid From address."                                                     | The sender address could not be read. Check **Email Address** on the account and the sender of the message.                                                                                                                                                   |
| "No SMTP server is configured for Email Account …."                                          | An account that is not on a Cloudflare domain has no outgoing server. Set one up on the account or its domain, or move the account to a Cloudflare domain.                                                                                                    |

### Receiving

Mail that does not arrive is best traced from the relay first: it records every delivery and the answer the site gave. On the site:

| Symptom                                                                       | Cause and fix                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The relay shows `401`                                                         | The key or the secret does not match, the account's **Enable Incoming** is unticked, its domain no longer sends via Cloudflare, or the relay's clock and the site's differ by more than 5 minutes. Copy the URL and secret again and update the tenant. The app's log, `cloudflare_email_delivery.log` in the bench's and the site's `logs` folders, says whether the key or the signature failed. |
| The relay shows `413`                                                         | The message is larger than the site's request limit. Raise `max_file_size` in the site config (in bytes; 27262976 is 26 MiB), knowing that it is also the fallback limit for file uploads.                                                                                                                                                                                                         |
| The relay shows `422` with "recipient domain does not belong to this account" | The relay routed mail for another domain to this account. Fix the tenant's domains in the relay, or set up an Email Account on that domain.                                                                                                                                                                                                                                                        |
| The relay shows `422` with another text                                       | The document the message belongs to refused it, or an envelope header was malformed. The text is the validation message; the relay keeps the message as rejected.                                                                                                                                                                                                                                  |
| The relay shows `500`                                                         | Open **Error Log** and look for "Cloudflare relay inbound"; the raw message is also in **Unhandled Email**. Fix the cause, then retry the delivery from the relay.                                                                                                                                                                                                                                 |
| The relay shows `503`                                                         | The relay user is missing. Run `bench --site <SITE_NAME> migrate`; the relay retries on its own.                                                                                                                                                                                                                                                                                                   |
| The relay shows `200` but nothing appears                                     | `remote_ref` is `null`: the message was sent from the account's own address and is not a reply, so it was ignored on purpose.                                                                                                                                                                                                                                                                      |
| A reply created a new document                                                | The reply carried no id the site knows. See [Reply threading](threading.md#when-a-reply-does-not-thread).                                                                                                                                                                                                                                                                                          |

**"… does not receive mail via Cloudflare."** when you click **Regenerate Webhook Secret**: the account's domain no longer has **Send via Cloudflare** ticked. Reload the form; the button is only meant for Cloudflare accounts.
