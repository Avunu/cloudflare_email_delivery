---
title: Develop Cloudflare Email Delivery
description: Set up the dev shell, run the tests and checks the way CI does, retake the screenshots, and see how CI and releases work for this repository.
nav_title: Development
order: 9
updated: 2026-10-26
---

This page is for people changing the app. [How it works](architecture.md) explains the design behind the code. The managed files, checks and release flow that every Avunu app shares are described once in the frappe-nix [app standards documentation](https://docs.avunu.net/frappe-nix/docs/app-standards/); this page covers what is specific to this repository.

## Set up

The repository is a frappe-nix app. `nix develop --no-pure-eval` (or `direnv allow`, which runs the same) gives you Python, Node, the git hooks and a dev bench with the app installed:

```bash
nix develop --no-pure-eval
prek install
```

`prek install` installs the git hooks that CI's lint job runs.

## Commands

| Command                    | What it does                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `frappe-test --ci`         | Runs the tests the way CI's `ci / test` does, with coverage                                                         |
| `frappe-test --reuse-site` | Runs the tests on the dev site as it is                                                                             |
| `yarn check`               | Checks the formatting, lints and type-checks the desk script (`public/js/email_account.js`) and the screenshot spec |
| `yarn lint:py`             | Runs ruff on the Python code                                                                                        |
| `yarn typecheck:py`        | Runs ty on the Python code against the dev bench                                                                    |
| `frappe-shots`             | Rebuilds the demo site (`cloudflare_email_delivery/demo.py`) and retakes the screenshots in `docs/screenshots/`     |
| `prek run --all-files`     | Runs every pre-commit hook on the whole repository, as CI's `ci / lint` does                                        |

## Tests

The tests are in `cloudflare_email_delivery/tests/`. Coverage must stay at 80% or more (`fail_under` in `pyproject.toml`).

| File                     | Covers                                                                                                                                            |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_relay.py`          | Signing, the envelope headers and the header helpers. Plain `unittest`, no site.                                                                  |
| `test_cloudflare_api.py` | The mapping from a MIME message to the API request, the HTTP session with its retries, and the token check. Plain `unittest`, no site or network. |
| `test_controllers.py`    | The Email Account and Email Domain extensions, and the fallback to SMTP or Frappe Mail for other accounts                                         |
| `test_outbound.py`       | `frappe.sendmail` through a Cloudflare account, end to end: one API call per recipient with the threading headers                                 |
| `test_inbound.py`        | The webhook, in-process with a fake request and twice through the real WSGI app                                                                   |
| `test_threading.py`      | A reply threads by `References` when its `In-Reply-To` names Cloudflare's `Message-ID`                                                            |
| `test_desk_api.py`       | The secret helpers: System Manager only, and regenerating keeps the key                                                                           |
| `test_relay_user.py`     | The relay user's least-privilege shape, the install and migrate hooks that keep it, and the session switch                                        |
| `test_demo.py`           | The demo data that `frappe-demo` and the screenshots use                                                                                          |

`fixtures.py` builds the shared records: a Cloudflare Email Domain, an incoming and an outgoing Email Account on it, and a MIME builder.

The two plain suites run without a site:

```bash
python -m unittest cloudflare_email_delivery.tests.test_relay cloudflare_email_delivery.tests.test_cloudflare_api
```

On a bench outside frappe-nix, the site-backed tests run with `bench`. Use a development or test site, never production:

```bash
bench --site <TEST_SITE> set-config allow_tests true
bench --site <TEST_SITE> install-app cloudflare_email_delivery
bench --site <TEST_SITE> run-tests --app cloudflare_email_delivery
```

### The mail guard

frappe-nix's mail guard strips the Email Account and Email Domain extensions and the `override_email_send` hook, so that mail sent from a dev bench lands in Mailpit. Those are exactly what the tests exercise, so `[tool.frappe-nix.tests] devguard-disable = ["mail"]` in `pyproject.toml` turns the guard off for `frappe-test`'s test processes only. The dev shell and `devenv up` keep it, and the egress guard stays on everywhere: nothing, the Cloudflare API included, leaves a dev bench.

To run the site-backed tests with plain `bench run-tests` on a frappe-nix bench, turn the mail guard off for that one command:

```bash
FRAPPE_DEVGUARD_DISABLE=mail bench --site <TEST_SITE> run-tests --app cloudflare_email_delivery
```

The demo runs with the guard on, so `demo.py` builds the webhook URL itself, the step the Email Account controller's `validate()` takes on a real site, and mocks the token check.

## Continuous integration

`.github/workflows/ci.yml` is managed by frappe-nix and runs the shared gates on every pull request, every push to `main` and once a day. Each gate is a check named `ci / <gate>`:

- `ci / lint`: the pre-commit hooks, run with prek;
- `ci / typecheck`: the type checks;
- `ci / test`: the tests with coverage;
- `ci / marketplace`: the registry checks of `marketplace/listing.toml`, `hooks.py` and the logo.

`pr-policy.yml` adds `ci / pr-policy`, which checks a pull request's base, title and commits: conventional commits as `committed.toml` defines them, with no `!` and no `BREAKING CHANGE`. `nightly.yml` runs the integration suites each night and is never a required check. `deps.yml` relocks after a dependency bump and turns on auto-merge for those pull requests.

The `Docs` workflow (`.github/workflows/docs.yml`) checks this documentation when `docs/` changes: see [Working on these docs](#working-on-these-docs).

## Releases

`release.yml` runs release-please on every push to `main`. It keeps a release pull request open that bumps the version in `package.json` and `cloudflare_email_delivery/__init__.py` and writes `CHANGELOG.md`. Merging it tags the release and moves the `version-16` branch forward, which is the branch the installation instructions point at. `fix:` commits make a patch release and `feat:` commits a minor one; the major version is the Frappe major and never changes.

## Conventions

- Python is formatted with ruff: tabs, double quotes and a line length of 110. Every source file starts with the copyright header the `validate_copyright` hook checks; `docs/` is excluded from it.
- `cloudflare_api.py` and `relay.py` import nothing from Frappe, so they can be tested without a site. Keep them that way.
- `cloudflare_api.py` derives from the Odoo module `mail_cloudflare` in [Avunu/avunu-odoo-addons](https://github.com/Avunu/avunu-odoo-addons) and is relicensed here under MIT by its copyright holder. The two encode the same API facts; change them together.
- `relay.py` implements the contract in the [relay's README](https://github.com/Avunu/cloudflare-email-relay). The test vector `sign("key", 1700000000, b"hello")` is shared by every implementation.
- The app ships no patches. Anything a site needs must happen in `after_install` and `after_migrate`, as `relay_user.ensure_relay_user` does.
- Never edit a file whose header says frappe-nix manages it, or the text between `frappe-nix:begin` and `frappe-nix:end` markers in `README.md`. `nix run --no-pure-eval .#frappe-init -- --check` reports drift.

## Working on these docs

The documentation is Markdown in `docs/`. It is published with the other Avunu projects at [docs.avunu.net/cloudflare-email-delivery/docs/](https://docs.avunu.net/cloudflare-email-delivery/docs/) by [Avunu/site-docs](https://github.com/Avunu/site-docs), which picks up the latest release of this repository. The screenshots in `docs/screenshots/` are generated by `frappe-shots`; do not edit them by hand.

To check a change as the pull request check does, clone Avunu/site-docs beside this repository and, from its folder, run:

```bash
npm ci --ignore-scripts && npm run build
node build/cli.js check --only cloudflare-email-delivery --source cloudflare-email-delivery=../cloudflare_email_delivery
```
