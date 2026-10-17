# App-specific Nix for cloudflare_email_delivery, imported by flake.nix.
{
  perSystem = _: {
    # This app is an email transport: it extends Email Account and Email Domain and hooks
    # override_email_send. The mail guard strips exactly those methods and hooks from every
    # Email Account and Email Domain subclass, so with it on the dev shell (and `frappe-test --ci`
    # in CI) runs core's controllers instead of this app's, and the site-backed tests can't run.
    # The egress guard stays on: every connection that isn't to this machine, SMTP and the
    # Cloudflare API included, is still refused, so nothing leaves a dev bench.
    frappe-nix.devguard.mail.enable = false;
  };
}
