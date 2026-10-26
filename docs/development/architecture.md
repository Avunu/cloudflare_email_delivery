---
title: How it works
description: "The design behind Cloudflare Email Delivery: the hooks it registers, the outbound and inbound paths, threading, and the relay user."
nav_title: How it works
order: 10
updated: 2026-10-26
---

This page describes the design behind the code, for developers. For commands, tests and CI, see [Develop Cloudflare Email Delivery](README.md).

## Where things live

| File                                                                          | What                                                                                                  |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `cloudflare_email_delivery/hooks.py`                                          | The hooks below                                                                                       |
| `cloudflare_email_delivery/cloudflare_email_delivery/custom/email_domain.py`  | The Email Domain extension and the `override_email_send` hook, with the Frappe Mail and SMTP fallback |
| `cloudflare_email_delivery/cloudflare_email_delivery/custom/email_account.py` | The Email Account extension: webhook key, secret and URL, no IMAP or SMTP                             |
| `cloudflare_email_delivery/cloudflare_email_delivery/custom/*.json`           | The custom fields and property settings on Email Account, Email Domain and Communication              |
| `cloudflare_email_delivery/public/js/email_account.js`                        | The **Cloudflare Webhook** buttons                                                                    |
| `cloudflare_email_delivery/api.py`                                            | The inbound webhook and the desk helpers for the secret                                               |
| `cloudflare_email_delivery/relay.py`                                          | The relay's wire contract: signatures and envelope headers, with no Frappe import                     |
| `cloudflare_email_delivery/inbound.py`                                        | `RelayInboundMail`, Frappe's `InboundMail` that also threads on `References`                          |
| `cloudflare_email_delivery/relay_user.py`                                     | The least-privilege user the webhook processes mail as                                                |
| `cloudflare_email_delivery/cloudflare_api.py`                                 | The Cloudflare Email Sending API client, with no Frappe import                                        |
| `cloudflare_email_delivery/demo.py`                                           | The demo data for `frappe-demo` and the screenshots                                                   |
| `marketplace/`                                                                | The marketplace listing and the screenshot spec                                                       |

## Hooks

| Hook                             | Target                                       | Purpose                                                                                          |
| -------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `extend_doctype_class`           | Email Domain → `CloudflareEmailDomain`       | Verify the API token instead of testing SMTP and IMAP connections                                |
| `extend_doctype_class`           | Email Account → `CloudflareEmailAccount`     | Clear the server fields, generate the webhook key and secret, build the URL, no-op the IMAP pull |
| `override_email_send`            | `email_domain.send`                          | Send through the Email Sending API, or through Frappe Mail or SMTP for other accounts            |
| `doctype_js`                     | Email Account → `public/js/email_account.js` | The copy and regenerate buttons                                                                  |
| `after_install`, `after_migrate` | `relay_user.ensure_relay_user`               | Create the relay user, role and permission rule, or reset them to least privilege                |
| `before_uninstall`               | `relay_user.remove_relay_user`               | Remove the relay user, role and permission rule                                                  |

The controllers are extensions, not overrides: each subclasses the stock controller, and Frappe stacks it with any other app that extends or overrides the same doctype. Every method checks whether the account's domain has `send_via_cloudflare` and calls `super()` when it does not. The one exception is `CloudflareEmailAccount.there_must_be_only_one_default`, which replaces Frappe's version for every account: it clears the other accounts' default flags with one database update instead of loading and saving each of them, which would re-run their connection checks.

Both modules keep an alias under the old class name (`EmailDomain`, `EmailAccount`), so that documents cached in Redis before the switch to `extend_doctype_class` still unpickle. They can go once every site has cleared its cache.

## Sending

Frappe's Email Queue builds one complete RFC 5322 message per recipient and calls the `override_email_send` hook with it. `email_domain.send`:

1. finds the queue entry's Email Account and, through `get_cloudflare_settings`, its domain's account ID and token. For an account that is not on a Cloudflare domain, it sends as Frappe would have, through Frappe Mail or the account's SMTP server (`send_via_fallback`);
2. parses the message and hands it, with the bare recipient address, to a `CloudflareSendingSession`. Sessions are kept per Cloudflare account in `frappe.local.flags`, so the messages of one job share a connection pool and a 401 or 403 fails the rest of the job without further calls;
3. `cloudflare_api.build_payload` maps the message to the JSON request: addresses as objects, text and HTML bodies, attachments in base64 with inline parts by `Content-ID`, and the allow-listed headers. `_passthrough_headers` appends Frappe's `Message-Id` to `References`, seeding the chain from `In-Reply-To` when there is no `References` yet, and trims the oldest ids when the header would pass Cloudflare's limits;
4. `CloudflareSendingSession._post` sends it with the retry rules in [Send mail through Cloudflare](../sending.md#when-a-send-fails), and `_check_recipients` raises when Cloudflare reports that no recipient was reached.

An exception from the hook is what tells Frappe the send failed; its message becomes the queue entry's error.

In a test run without `frappe.flags.testing_email`, `send` stores the message in `frappe.flags.sent_mail` and returns, as Frappe does on its own path.

## Receiving

`api.inbound` is a guest, POST-only whitelisted method. It never lets an exception escape, because Frappe would turn it into a `417` or `500` with a traceback, and the relay reads `417` as a permanent rejection. In order, it:

1. refuses a `Content-Length` over 26 MiB (`413`);
2. finds the Email Account with that `cf_webhook_key` and **Enable Incoming**, on a Cloudflare domain (`401` otherwise);
3. reads the body, checks its size again, and verifies the signature with `relay.verify_signature` (`401`);
4. decodes and checks the envelope headers (`422`) and prepends them as `Delivered-To` and `Return-Path` when the message lacks them;
5. checks that the domain of `Delivered-To`, or of the envelope recipient, is the account's domain or its address's domain (`422`);
6. switches to the relay user with `acting_as_relay_user` (`503` when the user is missing) and calls `_deliver`.

`_deliver` returns the existing Communication when one carries the same `cf_relay_id` for the account. Otherwise it runs `RelayInboundMail.process()`, which also returns the existing Communication for a known `Message-ID`, records `cf_relay_id` on a new one, commits, then sends the auto-reply and the notifications. `SentEmailInInboxError` (the account's own mail coming back) is a `200` with no reference; a `ValidationError` is a `422`; anything else is rolled back, logged, stored as an Unhandled Email, and answered `500`. `store_unhandled_email` is Frappe's `handle_bad_emails` without its IMAP condition.

## Threading

`RelayInboundMail` overrides one property of Frappe's `InboundMail`, `in_reply_to`. Its candidates are the ids in `In-Reply-To`, then those in `References` from newest to oldest, without duplicates. The first one the site knows wins: an id that contains the site's name (Frappe's `make_msgid` uses it), or one stored as `message_id` on a Communication or an Email Queue entry. When none is known, the first candidate is kept, so Frappe's own fallbacks still apply. Everything else in `InboundMail` that finds the parent (`is_reply`, `parent_email_queue`, `parent_communication`, `reference_document`) reads `in_reply_to`, so the reply attaches to the right document.

## The relay user

Frappe's `InboundMail.process` saves attachments and re-saves the new Communication without ignoring permissions, so it cannot run as Guest. Rather than Administrator, the webhook runs it as `cloudflare-email-relay@relay.invalid`:

- a disabled Website User, so it can never log in, reset a password or hold an API key;
- whose only role, `Cloudflare Email Relay`, has no desk access;
- with one extra permission rule: the role may read and write the Communications it owns (`if_owner`). Frappe's rules for **All** already let every user create Files and read and delete its own Communications.

`ensure_relay_user` resets the user on install and on every migrate: roles, role profiles, module profile, emails, API key and document shares. Because a Custom DocPerm replaces a doctype's standard rules, `_ensure_permissions` first copies Communication's standard rules with Frappe's `setup_custom_perms`. `acting_as_relay_user` restores the caller's user, session id, session data and form dict when the block ends, however it ends.

## Webhook credentials

`ensure_webhook_credentials` generates the key and the secret once, each `secrets.token_urlsafe(32)`, and never rotates them on save: saving an Email Domain re-saves every account on it, and the relay holds these values. `regenerate_webhook_secret` replaces only the secret, through `set_encrypted_password`, so the URL stays valid. `set_webhook_url` rebuilds the URL from `get_url()` on every save and refuses anything but `https`, except on loopback hosts, in developer mode and in tests.
