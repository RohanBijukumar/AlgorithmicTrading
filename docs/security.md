# Hosted Security Boundary

## Decision

The local CLI and `ui serve` are single-owner tools. Do not publish that server.
Hosted mode uses a separate ASGI entry point behind Cloudflare Tunnel and Access.
Access and a managed identity provider own passwords, recovery, login throttling,
and mandatory MFA. The app independently verifies RS256 Access tokens, including
issuer, audience, expiry, and subject. An operator must also provision that subject
in the local allowlist. There is no public signup or password database here.

Use individual identity-provider invitations or one-time activation links delivered
over your chosen third-party channel, never a shared account or reusable password.
Require phishing-resistant WebAuthn/passkeys or security keys, including for admins.
Do not enable Access email-OTP-only, bypass, or service-token policies for this app.
MFA is a deployment policy, not something the application can infer from a valid
Access JWT. Enforce it in Access/your identity provider and test it before launch.

## Threat Model

Protect portfolios, trade history, exports, job events, and future credentials from
unauthenticated callers, other invited users, browser request forgery, and accidental
public exposure. Authenticated users are not trusted with another user's records.
Host administrators and the identity provider remain trusted. Host compromise,
malicious dependencies, stolen active sessions, and denial of service are residual
risks; no claim of absolute security or brokerage readiness is made.

Each account has an independent SQLite database and in-memory job stores. This
avoids relying on every legacy SQL query remembering a tenant filter. Existing
local portfolios are never silently assigned to a remote user. Market data may
be copied separately by an administrator; the first version trades disk space for
simple isolation. A shared PostgreSQL market store, durable worker queue and
distributed admission control are required before horizontal scaling.

## Required Controls

- HTTPS-only public origin, no published origin port, gateway authentication on
  every path, and origin-side signature verification (never trust email headers).
- Explicit local allowlist, immediate disable checks, private filesystem modes,
  per-user jobs, bounded global work and request rates, and bounded request bodies.
- Exact-origin checks and custom AJAX headers on mutations, no CORS, restrictive
  CSP, no framing, no caching sensitive responses, and sanitized server errors.
- Access audit logs plus structured application security events exported to a
  restricted log sink. Logs must not contain JWTs, passwords or request bodies.
- Encrypted disks/backups, restore drills, dependency updates/scans, and tested
  account recovery/offboarding. Local audit logs are not tamper-proof.

## Brokerage Gate: Not Implemented

This remains paper trading only. Do not enter brokerage credentials or attach a
funded account. Before enabling real trading: separate execution service and
network boundary, broker OAuth with least privilege/no withdrawals, KMS-backed
credential encryption, fresh step-up authentication for account linking/orders,
idempotent orders and reconciliation, hard position/notional/loss limits, kill
switches, immutable audit trails, independent security review and penetration
testing. Review applicable broker, market-data redistribution and legal terms.

## References

- [Access JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
- [Access MFA requirements](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/mfa-requirements/)
- [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OWASP security headers](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
