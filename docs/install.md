---
title: Install and upgrade
description: Install Cloudflare Email Delivery on a Frappe v16 bench, see what the install adds to the site, and upgrade or remove it.
nav_title: Install
order: 1
updated: 2026-10-26
---

Cloudflare Email Delivery installs like any Frappe app. It has no build step of its own and no setup wizard: once it is installed, you switch an Email Domain to Cloudflare as [Send mail through Cloudflare](sending.md) describes.

## Before you start

- A bench with Frappe v16 (`frappe >=16.0.0,<17.0.0`) and Python 3.14 or newer.
- A System Manager login on the site.
- The Cloudflare side ready, as listed in the [requirements](README.md#requirements). You can install the app before that and set it up later: nothing changes for your existing email until an Email Domain has **Send via Cloudflare** ticked.

## Install

Run these from the bench directory. `<SITE_NAME>` is your site, for example `erp.example.com`.

```bash
bench get-app https://github.com/Avunu/cloudflare_email_delivery --branch version-16
bench --site <SITE_NAME> install-app cloudflare_email_delivery
```

The `version-16` branch is created by the first release. Until then, get the app from `main`:

```bash
bench get-app https://github.com/Avunu/cloudflare_email_delivery --branch main
```

## What installing sets up

The custom fields and property settings come with the app and are synced into the site on install and on every migrate:

| Document      | What is added                                                                                                                                                                                                                                                                            |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Email Domain  | A **Cloudflare Email Sending** section with **Send via Cloudflare**, **Cloudflare Account ID** and **Cloudflare API Token**. **Incoming Server** and **Outgoing Server** are no longer required when **Send via Cloudflare** is ticked, and the **Outgoing Settings** section is hidden. |
| Email Account | A hidden, read-only **Cloudflare Enabled** check, and a **Cloudflare Email Receiving** section on the **Incoming** tab with **Webhook URL**, **Webhook Key** and **Webhook Secret**. On an account whose domain sends via Cloudflare, the password, IMAP and SMTP fields are hidden.     |
| Communication | A hidden, read-only **Relay Id** field that records which relay delivery created the Communication.                                                                                                                                                                                      |

The full list of fields is in [Configuration](configuration.md#what-the-app-adds-to-your-site).

The install (`after_install`) also creates the user the webhook processes mail as:

- a Role, **Cloudflare Email Relay**, without desk access;
- a User, `cloudflare-email-relay@relay.invalid`, that is disabled, is a Website User and holds only that role;
- one permission rule on Communication: that role may read and write the Communications it owns.

To add that rule, the app copies Communication's standard permission rules into Custom DocPerm first, as Frappe's own **Role Permissions Manager** does when you add a rule there. From then on, Communication's permissions are the customised set.

The app ships no patches.

## Upgrade

Update the app the way you update any Frappe app: `bench update`, or pull the new code in `apps/cloudflare_email_delivery` and then run this from the bench directory.

```bash
bench --site <SITE_NAME> migrate
```

`bench migrate` re-syncs the custom fields and property settings, and its `after_migrate` hook resets the relay user, its role and its permission rule to the shape described above. A role, role profile, module profile, email, API key or document share added to that user by hand does not survive a migrate.

> [!IMPORTANT]
> Until a site with the app has been migrated, the relay user may not exist. The webhook then answers every delivery with `503`, and the relay keeps the messages and retries them. Run `bench migrate` after installing the app on a site restored from a backup, too.

## Uninstall

```bash
bench --site <SITE_NAME> uninstall-app cloudflare_email_delivery
```

Before you uninstall, give every Email Account on a Cloudflare domain another way to send and receive, or disable it. The app cleared the server fields of those accounts, so once it is gone, Frappe has no SMTP or IMAP server to use for them.

Uninstalling removes:

- the relay user, the **Cloudflare Email Relay** role and that role's permission rule on Communication (the app's `before_uninstall` hook);
- the custom fields and property settings, because they belong to the app's module.

These stay behind:

- the copied standard rules of Communication, as Custom DocPerm rows;
- the database columns of the removed custom fields, until you run `bench --site <SITE_NAME> trim-tables`;
- the Communications and Unhandled Emails the webhook created, and your Email Domains and Email Accounts.
