---
title: Configuration
description: Every setting Cloudflare Email Delivery reads, the site config keys it depends on, who may do what, and everything the app adds to a site.
nav_title: Configuration
order: 2
updated: 2026-10-26
---

The app has no settings page of its own. It is configured on two documents you already use, Email Domain and Email Account, plus a few keys of the site config. This page lists them, then lists everything the app adds to your site.

## Email Domain

The **Cloudflare Email Sending** section sits at the end of the Email Domain form.

| Field                     | What it does                                                                                                                                                                                                                           | Default  |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| **Send via Cloudflare**   | Sends the mail of every Email Account on this domain through the Cloudflare Email Sending API, and lets those accounts receive from the relay. Saving the domain then checks the token instead of connecting to SMTP and IMAP servers. | Unticked |
| **Cloudflare Account ID** | The ID of the Cloudflare account that owns the domain's Email Sending. Required when **Send via Cloudflare** is ticked.                                                                                                                | Empty    |
| **Cloudflare API Token**  | An API token with the _Email Sending: Edit_ permission, stored encrypted. Required when **Send via Cloudflare** is ticked.                                                                                                             | Empty    |

The domain's own SMTP fields are hidden while **Send via Cloudflare** is ticked. Saving an Email Domain also re-saves every Email Account linked to it; that is Frappe's behaviour, and it keeps each account's webhook key and secret.

## Email Account

An Email Account sends and receives through Cloudflare when its **Domain** is an Email Domain with **Send via Cloudflare** ticked. You do not tick anything on the account for that: saving it sets the hidden **Cloudflare Enabled** check from the domain.

On such an account, saving also clears **Incoming Server**, **Outgoing Server**, **Use IMAP**, **Use STARTTLS** and **Awaiting password**, and skips Frappe's connection tests and password check. The **Cloudflare Email Receiving** section on the **Incoming** tab then shows:

| Field              | What it does                                                                                                                                                                                                               | Default                                           |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| **Webhook URL**    | Where the relay posts this account's mail: `https://<HOST_NAME>/api/method/cloudflare_email_delivery.api.inbound?key=<WEBHOOK_KEY>`. Read-only. Rebuilt on every save, and shown only while **Enable Incoming** is ticked. | Built on save                                     |
| **Webhook Key**    | The random part of the URL that identifies the account. Read-only, and never changed once set.                                                                                                                             | 43 random characters, generated on the first save |
| **Webhook Secret** | The secret the relay signs every delivery with, stored encrypted. Read-only; change it with **Regenerate Webhook Secret**.                                                                                                 | 43 random characters, generated on the first save |

The standard Email Account fields keep their meaning: **Enable Outgoing** and **Enable Incoming**, **Append To**, **Attachment Limit (MB)**, **Create Contacts from Incoming Emails**, **Enable Automatic Linking in Documents**, **Notify if unreplied**, **Enable Auto Reply**, **Default Incoming** and **Default Outgoing**. The **Pull Emails** button is removed from an incoming Cloudflare account, because the relay pushes mail and there is nothing to pull.

## Site config

| Key              | What it does                                                                                                                                                                                                                                      |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `host_name`      | The site's public address, with its scheme: `https://erp.example.com`. The **Webhook URL** is built from it through Frappe's `get_url`, which falls back to the request's host when it is not set. A value without a scheme is read as `http://`. |
| `developer_mode` | When set, the **Webhook URL** may be plain `http`. Otherwise it must be `https`, except on `localhost`, `127.0.0.1` and `::1`.                                                                                                                    |
| `max_file_size`  | Frappe's limit, in bytes, on the size of any request other than a file upload, 25 MiB when not set. A message larger than that is refused by Frappe before the app sees it. The app adds its own limit of 26 MiB.                                 |

## Roles and permissions

| Who                                               | What they can do                                                                                                                                                       |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Anyone who can open the Email Account             | Sees the **Webhook URL** and **Webhook Key**, and uses **Copy Webhook URL**.                                                                                           |
| A System Manager with write access to the account | Also sees **Copy Webhook Secret** and **Regenerate Webhook Secret**. The server checks both the role and the write permission.                                         |
| The relay (a guest request)                       | Posts mail to the webhook. It is let in by the signature, not by a login, and the message is then processed as the relay user.                                         |
| `cloudflare-email-relay@relay.invalid`            | The disabled user the webhook processes mail as. Its only role, **Cloudflare Email Relay**, has no desk access and may read and write only the Communications it owns. |

## What the app adds to your site

This is the complete list, so you can see what installing or migrating changes.

### Custom fields

| Document      | Field (fieldname, type)                                                                                                                | Notes                                                                                      |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Email Domain  | **Cloudflare Email Sending** (`cloudflare_email_section`, Section Break)                                                               | After **Sent Folder Name**                                                                 |
| Email Domain  | **Send via Cloudflare** (`send_via_cloudflare`, Check)                                                                                 |                                                                                            |
| Email Domain  | **Cloudflare Account ID** (`cf_account_id`, Data)                                                                                      | Shown and required when **Send via Cloudflare** is ticked                                  |
| Email Domain  | **Cloudflare API Token** (`cf_api_token`, Password)                                                                                    | Shown and required when **Send via Cloudflare** is ticked                                  |
| Email Account | **Cloudflare Enabled** (`cf_enabled`, Check)                                                                                           | Hidden, read-only, after **Domain**                                                        |
| Email Account | **Cloudflare Email Receiving** (`cf_receiving_section`, Section Break)                                                                 | On the **Incoming** tab; shown when **Cloudflare Enabled** and **Enable Incoming** are set |
| Email Account | **Webhook URL** (`cf_webhook_url`, Data), **Webhook Key** (`cf_webhook_key`, Data), **Webhook Secret** (`cf_webhook_secret`, Password) | Read-only                                                                                  |
| Email Account | An HTML field (`cf_webhook_help`) that summarises the webhook's headers and answers                                                    |                                                                                            |
| Communication | **Relay Id** (`cf_relay_id`, Data)                                                                                                     | Hidden, read-only, after `uid`                                                             |

### Property settings

| Document      | Change                                                                                                                                                                                                                                                            |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Email Domain  | **Incoming Server** and **Outgoing Server** are required only when **Send via Cloudflare** is unticked. The **Outgoing Settings** section is hidden when it is ticked.                                                                                            |
| Email Account | When **Cloudflare Enabled** is set: the **Incoming (POP/IMAP) Settings** and **Outgoing (SMTP) Settings** sections, **Password**, **Awaiting password**, **Use ASCII encoding for password**, **Use IMAP**, **IMAP Folder** and **Email Sync Option** are hidden. |

### Behaviour of standard doctypes

| Where                      | Change                                                                                                                                                                                                                                                                                                                                  |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Email Domain               | Extended with `extend_doctype_class`. A domain with **Send via Cloudflare** checks its API token on save instead of connecting to SMTP and IMAP. Any other domain runs Frappe's checks.                                                                                                                                                 |
| Email Account              | Extended with `extend_doctype_class`. An account on a Cloudflare domain clears its server fields, generates and keeps its webhook key and secret, builds its **Webhook URL**, never connects to an SMTP or IMAP server and returns nothing when the scheduler pulls mail. Any other account runs Frappe's code.                         |
| Email Account              | For every account on the site, Cloudflare or not: when you save an account as **Default Incoming** or **Default Outgoing**, the flag is cleared on the other accounts directly in the database. Frappe would load and save each of them, which re-runs their connection checks and fails when one of their mail servers is unreachable. |
| Email Account              | `doctype_js` adds the **Cloudflare Webhook** button group (**Copy Webhook URL**, **Copy Webhook Secret**, **Regenerate Webhook Secret**) and removes **Pull Emails** on a saved incoming account of a Cloudflare domain.                                                                                                                |
| Email Queue                | The `override_email_send` hook sends every outgoing message. For an account on a Cloudflare domain it calls the Cloudflare API; for any other account it sends through Frappe Mail or SMTP, as Frappe would.                                                                                                                            |
| Role, User, Custom DocPerm | The relay role, user and permission rule described in [Install and upgrade](install.md#what-installing-sets-up), created on install and reset on every migrate.                                                                                                                                                                         |

### Endpoints

| Method                                                    | Who can call it                                     | What it does                                                      |
| --------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------- |
| `cloudflare_email_delivery.api.inbound`                   | Anyone (POST only); the signature is the credential | The webhook. See [The webhook contract](webhook.md).              |
| `cloudflare_email_delivery.api.get_webhook_secret`        | A System Manager with write access to the account   | Returns the account's webhook secret, for **Copy Webhook Secret** |
| `cloudflare_email_delivery.api.regenerate_webhook_secret` | A System Manager with write access to the account   | Replaces the webhook secret and keeps the key and URL             |

The app adds no doctypes and no scheduled jobs.
