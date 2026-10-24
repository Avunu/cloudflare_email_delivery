# Changelog

## [16.0.0](https://github.com/Avunu/cloudflare_email_delivery/compare/v0.1.0...v16.0.0) (2026-10-24)


### Features

* conditionally decode message prior to sending ([ce59c71](https://github.com/Avunu/cloudflare_email_delivery/commit/ce59c71909edc66db9f4212f86b1a9557bd33fc3))
* hide the ougoing email settings when sending via cloudflare ([5674734](https://github.com/Avunu/cloudflare_email_delivery/commit/5674734cf86e34f78c7babb86a5a3ccbd99bc7d3))
* override password requirement on email accounts ([aa922a9](https://github.com/Avunu/cloudflare_email_delivery/commit/aa922a95685795af9aab8e6ded9be23f98a7fdaf))
* receive mail from the Cloudflare email relay, and fix the send path ([c280c48](https://github.com/Avunu/cloudflare_email_delivery/commit/c280c48e2702de803e555aaa8b8b4872f94fcd93))


### Bug Fixes

* **api:** process relayed mail as a least-privilege user ([#14](https://github.com/Avunu/cloudflare_email_delivery/issues/14)) ([1017509](https://github.com/Avunu/cloudflare_email_delivery/commit/1017509aa8f00e1ba7d1a55ecf8b447814b44aa2))
* don't require auth for associated email accounts ([c0eb5c9](https://github.com/Avunu/cloudflare_email_delivery/commit/c0eb5c96c5ffce927a078ca6ed40f3a756511942))
* don't require imap settings on cloudflare domains ([3231752](https://github.com/Avunu/cloudflare_email_delivery/commit/3231752583cb76d2d4dfe76813cadc8f6dd3c907))
* **email-account:** list the webhook answers the endpoint really gives ([#26](https://github.com/Avunu/cloudflare_email_delivery/issues/26)) ([68ac65d](https://github.com/Avunu/cloudflare_email_delivery/commit/68ac65d9eda0349a9c4d00ae587a0e908843f305))
* isolate email account from sender on account detection ([a44bae9](https://github.com/Avunu/cloudflare_email_delivery/commit/a44bae948e9b5d6f9972ce628d79da33b36301a3))


### Continuous Integration

* adopt the shared CI gates ([a40220f](https://github.com/Avunu/cloudflare_email_delivery/commit/a40220f4d4c15cdb14b3d0181703c4e355996d23))
