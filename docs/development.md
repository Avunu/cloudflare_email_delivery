# Development

`nix develop` (or direnv) gives you Python, Node, the git hooks and a dev bench with this app installed.

| Command                    | What it does                                                                                                    |
| -------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `frappe-test --ci`         | Runs the tests the way CI's `ci / test` does, with coverage                                                     |
| `frappe-test --reuse-site` | Runs the tests on the dev site as it is                                                                         |
| `yarn check`               | Formats, lints and type-checks the desk script (`public/js/email_account.js`) and the screenshot spec           |
| `frappe-shots`             | Rebuilds the demo site (`cloudflare_email_delivery/demo.py`) and retakes the screenshots in `docs/screenshots/` |

## Tests

```sh
frappe-test --ci   # in the frappe-nix dev shell: what CI's ci / test runs

# on any bench
bench --site test_site install-app cloudflare_email_delivery
bench --site test_site run-tests --app cloudflare_email_delivery
```

`tests/test_relay.py` and `tests/test_cloudflare_api.py` are plain `unittest` (no site needed:
`python -m unittest cloudflare_email_delivery.tests.test_relay`). The site-backed suites cover the
webhook (in-process and through the real WSGI app), threading, the send hook and the demo.

## The mail guard

frappe-nix's mail guard deliberately strips the very Email Account and Email Domain extensions and the
`override_email_send` hook these tests exercise, so `[tool.frappe-nix.tests] devguard-disable =
["mail"]` in `pyproject.toml` turns it off for `frappe-test`'s test processes only. The dev shell
and `devenv up` keep it, so mail sent while working on the app by hand still lands in Mailpit, and
the egress guard stays on everywhere: nothing, SMTP and the Cloudflare API included, leaves a dev
bench. To run the site-backed tests with plain `bench run-tests` on a frappe-nix bench, turn the
guard off for that one command: `FRAPPE_DEVGUARD_DISABLE=mail bench --site … run-tests …`.

The demo runs with the guard on, so `demo.py` builds the webhook URL itself (the step the Email
Account controller's `validate()` takes in production) and mocks the token check.

## Where things live

| File                                                | What                                                                                    |
| --------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `cloudflare_email_delivery/custom/email_domain.py`  | The Email Domain extension and the `override_email_send` hook, with the SMTP fallback   |
| `cloudflare_email_delivery/custom/email_account.py` | The Email Account extension: webhook key, secret and URL, no IMAP or SMTP               |
| `cloudflare_email_delivery/custom/*.json`           | The custom fields and property setters on Email Account, Email Domain and Communication |
| `public/js/email_account.js`                        | The copy and regenerate buttons                                                         |
| `api.py`                                            | The inbound webhook and the desk helpers for the secret                                 |
| `relay.py`                                          | The relay's wire contract: signatures and envelope headers, with no Frappe import       |
| `inbound.py`                                        | `InboundMail` that also threads on `References`                                         |
| `relay_user.py`                                     | The least-privilege user the webhook processes mail as                                  |
| `cloudflare_api.py`                                 | The Cloudflare Email Sending API client                                                 |
| `demo.py`                                           | The demo data for `frappe-demo` and the screenshots                                     |
| `marketplace/`                                      | The marketplace listing and the screenshot spec                                         |

`cloudflare_api.py` is shared with the Odoo module `mail_cloudflare` in
[Avunu/avunu-odoo-addons](https://github.com/Avunu/avunu-odoo-addons); `relay.py` implements the
contract documented in the [relay's README](https://github.com/Avunu/cloudflare-email-relay).
