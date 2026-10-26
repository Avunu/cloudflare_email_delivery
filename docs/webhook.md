---
title: The webhook contract
description: The request the Cloudflare email relay sends to the site, how it is signed, and what each answer tells the relay to do.
nav_title: Webhook
order: 6
updated: 2026-10-26
---

This page is for whoever runs the [Cloudflare email relay](https://github.com/Avunu/cloudflare-email-relay) or writes another sender for the webhook. The relay's README is the reference for the wire contract; this page describes how this app implements it.

## The request

```text
POST /api/method/cloudflare_email_delivery.api.inbound?key=<WEBHOOK_KEY>
Content-Type: message/rfc822
```

The body is the raw message. The **Webhook URL** on the Email Account is this address with the site's `host_name` in front. Only `POST` is accepted.

| Header                        | Meaning                                                                                                                                                           |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `X-Email-Relay-Id`            | The relay's id for the delivery. Echoed back in the answer, stored on the new Communication, and used to recognise a retry of a message that has no `Message-ID`. |
| `X-Email-Relay-Tenant`        | The tenant the relay routed by. Written to the app's log only.                                                                                                    |
| `X-Email-Relay-Timestamp`     | Unix seconds when the request was signed. Refused when more than 300 seconds away from the site's clock.                                                          |
| `X-Email-Relay-Signature`     | `v1=` followed by the hex HMAC-SHA256 of `<TIMESTAMP>.` and the body, keyed with the **Webhook Secret**.                                                          |
| `X-Email-Relay-Envelope-From` | The SMTP envelope sender. Written into the message as `Return-Path` when the message has none.                                                                    |
| `X-Email-Relay-Envelope-To`   | The SMTP envelope recipient. Written into the message as `Delivered-To` when the message has none.                                                                |
| `X-Email-Relay-Attempt`       | Sent by the relay; not read by the app.                                                                                                                           |

The envelope headers may be percent-encoded; the app decodes them and refuses any that hold control characters.

### Signing

The signature covers the timestamp exactly as sent, a dot, then the body bytes:

```python
import hashlib
import hmac


def sign(secret: str, timestamp: str, body: bytes) -> str:
	digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
	return "v1=" + digest.hexdigest()
```

The app compares signatures in constant time.

## The answer

Frappe wraps the answer in `message`. A delivered message looks like this:

```json
{ "message": { "ok": true, "remote_ref": "<COMMUNICATION_NAME>", "id": "<RELAY_ID>" } }
```

A refusal carries `"ok": false` and an `error` text instead.

| Status | When                                                                                                                                                                      | What the relay does   |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| `200`  | Processed. `remote_ref` is the Communication, or `null` when the message was the account's own outgoing mail coming back, which is ignored on purpose.                    | Marks it delivered    |
| `401`  | No enabled incoming account on a Cloudflare domain for the key, or a missing or wrong signature, or a timestamp more than 300 seconds off. The answer does not say which. | Parks it as rejected  |
| `413`  | Larger than 26 MiB, or than the site's own request limit (`max_file_size` in the site config, 25 MiB when not set), which Frappe applies first.                           | Parks it as rejected  |
| `422`  | The routed recipient's domain is not the account's, an envelope header holds control characters, or the document the message belongs to refused it (a validation error).  | Parks it as rejected  |
| `500`  | Anything else. The error is logged in **Error Log** ("Cloudflare relay inbound") and the message is stored as an **Unhandled Email**.                                     | Retries with back-off |
| `503`  | The relay user does not exist: the site needs `bench migrate`.                                                                                                            | Retries with back-off |

Deliveries are idempotent: a retry of a message the site already has answers `200` with the existing Communication, recognised by `X-Email-Relay-Id` or by the message's `Message-ID`.

## Security

The endpoint accepts guests, because the relay has no login. The signature is the credential: the size is checked first, and nothing is processed until the key matches an account and the signature verifies against that account's secret. The recipient-domain check stops a shared relay from delivering one tenant's mail to another site.

Processing then runs as `cloudflare-email-relay@relay.invalid`, a disabled Website User whose only role, **Cloudflare Email Relay**, may read and write the Communications it owns. Everything else Frappe's inbound processing writes, it writes with its own permission bypass, as it does for mail pulled from a mailbox. The session is handed back to Guest when processing ends.
