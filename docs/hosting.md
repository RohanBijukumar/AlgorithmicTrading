# Private Multi-User Hosting

This is a small, single-instance **paper research** deployment, not a broker-connected
trading service. Read [security.md](security.md) before exposing it. The local
`ui serve` command is deliberately loopback-only; never tunnel that local server.

## Architecture

Browser -> HTTPS Cloudflare Access (managed login + mandatory MFA) -> outbound-only
Cloudflare Tunnel -> Uvicorn/Starlette -> verified Access JWT -> local account
allowlist -> private SQLite workspace. The app issues no passwords or browser
auth tokens. It does not trust `Cf-Access-Authenticated-User-Email` or forwarded IPs.

Use a dedicated hostname, not a subpath or a shared hosting origin. Cloudflare is
a trust boundary and TLS terminator; it can see application traffic. Choose a
different reviewed architecture if that conflicts with your data requirements.

## 1. Configure Identity Before Publishing

1. Create a Cloudflare Zero Trust organization and connect a managed identity
   provider with individual accounts (for example, your existing Entra ID or
   Google Workspace). Configure provider invitations/activation and recovery.
2. Create a self-hosted Access application covering the **entire hostname**,
   including `/api/*`, assets and exports. Record the team issuer URL and AUD tag.
3. Allow only specifically invited identities/groups. Require phishing-resistant
   MFA/passkeys/security keys using Access's MFA policy or the IdP's enforced
   authentication policy. Test the policy with a user lacking the required factor.
   No public signup, bypass rules, service-token policies, or email-OTP-only login.
4. Set application, global, and IdP session policies conservatively. Application
   tokens must have a lifetime of **one hour or less**; the app rejects longer
   tokens. Set 15 minutes where your workflow permits. Configure secure/HttpOnly
   authorization cookies, protect all paths, and enable Access audit logs.
5. Configure a remotely managed Tunnel: public hostname to `http://app:8000`.
   Preserve the public Host header (or explicitly set it to the public hostname).
   Enable Access validation on the tunnel as defense in depth. Do not publish
   port 8000, open inbound origin ports, or deploy a second unprotected hostname.
6. Configure edge rate limits and alerts. In-app limits protect admitted users;
   they do not replace the gateway's bot/login/DDoS protections.

See [JWT verification](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/),
[MFA enforcement](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/mfa-requirements/),
and [session management](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/).
These cloud settings cannot be established or verified by this repository alone.

## 2. Build and Run

Use a maintained Linux host with encrypted storage, Docker Compose, private SSH
administration, and monitored CPU/disk/memory. Start with at least 4 GB RAM and
size disk for each user's market cache. Do not mount an NFS volume for SQLite.

Populate `.env` using `.env.example`. Resolve reviewed Python 3.13 and cloudflared
images to immutable digests. Put the Tunnel token in `secrets/tunnel-token`, not
in Git, the app environment, command history, or an image layer. Restrict the
directory to the operator; ensure the secret file is readable by container UID
10001 (on Linux, owner 10001 and mode 0400). The token is mounted only in the
tunnel container. Restrict Docker/socket access: it is equivalent to host admin.

```bash
docker compose config --quiet
docker compose build --pull app
docker compose up -d
docker compose logs --tail 100 app tunnel
```

No service publishes a host port. Open your `PUBLIC_ORIGIN` in the browser.
All pages and API endpoints, including health, require authentication and a
provisioned account. Container health checks only test the local listener.

For a non-container private origin behind a separately configured Tunnel:

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-hosted.txt
.venv/bin/pip install --no-deps .
# Export PUBLIC_ORIGIN, ACCESS_ISSUER, ACCESS_AUDIENCE and absolute HOSTED_ROOT.
.venv/bin/uvicorn algotrading.hosted:create_app --factory \
  --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers \
  --no-access-log --no-server-header --limit-concurrency 32
```

Only one process may own a hosted data root. Startup fails if another process
holds its lock. Do not add Uvicorn workers or replicas: jobs and request quotas
are in-memory. A restart cancels work at cooperative checkpoints; a hard crash
can leave partial portfolios requiring operator inspection. Saved results persist.

## 3. Invite and Provision a User

1. Invite the person through the managed IdP. Send their activation link and site
   address through your chosen third-party channel. Do not send shared passwords.
2. Add them to the Access allow policy. Have them complete login and MFA once;
   the app still denies access until provisioned.
3. Obtain that user's **Access user ID (`sub`, a UUID)** from the Access user
   directory/API, not an unverified email/header or a JWT they paste into chat.
4. Provision that exact subject locally:

```bash
docker compose exec app python -m algotrading.hosted_admin add \
  --subject ACCESS-USER-UUID --label 'Alice'
docker compose exec app python -m algotrading.hosted_admin list
```

They now have their own portfolios, universe changes, market cache, runs, reports,
and jobs. The label is display-only, never used as identity. Existing local
portfolio data is not migrated automatically. Account administration has no web
endpoint; users cannot promote themselves or provision others.

To revoke access immediately at the application boundary:

```bash
docker compose exec app python -m algotrading.hosted_admin disable \
  --subject ACCESS-USER-UUID
```

New requests are denied on the next registry check. Jobs cancel at their next
checkpoint; an already admitted request may complete. Also remove the Access/IdP
membership and revoke their Access sessions. Logout uses the gateway's
`/cdn-cgi/access/logout`; it is not an independent origin-side JWT revocation list.
The origin must remain private because a stolen signed token remains
cryptographically valid until expiry. Disabled accounts are retained for audit
and backup; this version intentionally has no automatic re-enable/delete flow.

## 4. Data and CLI

Each user may sync up to 100 symbols per request; batch larger downloads. Hosted
backtests allow up to 2,000 resolved symbols and 500,000 cached input bars (including
pre-start warm-up history); select fewer symbols for larger datasets. Online discovery is disabled: the
discovery strategy can use cached research only. At most two background jobs run
globally, with one backtest and one sync per user. Busy requests return 429 instead
of building an unbounded queue. Quotas are 300 reads / 20 writes per minute per
user, reset by a restart. The initial deployment is capped at 50 provisioned
accounts, not a promise of 50 concurrent compute-heavy users. Apply host disk
quotas and retention policies; application quotas are not a hard storage limit.

For an offline host installation, copy *only market tables* into a new workspace:

```bash
# Stop the server; use the same exported settings and service OS identity.
.venv/bin/python -m algotrading.hosted_admin seed-market \
  --subject ACCESS-USER-UUID --source /absolute/path/to/local.sqlite3
```

Source is opened read-only; personal records and provider error logs are excluded.
The destination must have no prices or portfolios. In Docker, run this command in
a one-off app container with the source mounted read-only while the main app is
stopped. Never copy the complete owner's database to provision someone else.

`hosted_admin list` prints database paths for trusted operators. The existing CLI
can operate on a specific user's database with `--db PATH`; it bypasses web auth
because it already requires server filesystem access. Never distribute host SSH,
Docker access, registry files or database paths as a user login mechanism.

## 5. Operations and Recovery

- Export structured `algotrading.security` logs and Access/IdP logs to a restricted
  off-host sink. Alert on repeated denials, account changes, busy workers and disk
  growth. Request IDs correlate errors without logging credentials or bodies.
- Stop the server, run `hosted_admin backup --output /private/new-snapshot`, then
  restart. It snapshots every SQLite database (including disabled users), includes
  WAL contents through SQLite's backup API, and checks integrity. Run backup as
  the service user. For Docker use a stopped app's one-off container with a private
  backup volume mounted; the output must be outside the main data root.
- Encrypt and copy backups off-host. Verify restores to a new directory with the
  original issuer, then run isolation and smoke checks before replacing live data.
  Also retain configuration separately; the backup does not contain Tunnel tokens.
- Update and scan base images and locked dependencies regularly. Never use blind
  automatic upgrades on a future real-money execution service.
- Keep the origin private even during maintenance. Gateways and identity providers
  have costs/limits; check your plans and market-data redistribution rights.

## Release Checks

```bash
.venv/bin/pip install -e '.[hosted,hosted-test]'
.venv/bin/python -m unittest discover -s tests
.venv/bin/pip-audit -r requirements-hosted.txt --disable-pip
```

Before invitations: test real IdP login and recovery, mandatory MFA, unknown user
denial, two-user isolation, account disable during a run, session expiry, logout,
direct-origin denial, HTTPS/security headers, resource exhaustion, backups and a
restore. Automated tests verify origin behavior but cannot verify your Access
dashboard settings. Obtain independent security review before public production
use. Brokerage linking/execution requires the additional controls in security.md.
