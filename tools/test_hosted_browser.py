"""Real Chromium against the ASGI app, with ephemeral signed identities and no listener.

The browser transport is intercepted only in this test, not in the production app.
No Cloudflare/IdP policy is tested here. Install playwright and Chromium first.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from playwright.sync_api import expect, sync_playwright
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_backtest import seeded_backtest_db

from algotrading.hosted import create_app
from algotrading.hosted_admin import seed_market
from algotrading.identity import AccessVerifier, HostedSettings
from algotrading.portfolio import PortfolioService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/algotrading-browser"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as playwright:
        root = Path(tmp)
        settings = HostedSettings(
            "https://research.example.com",
            "https://example.cloudflareaccess.com",
            "a" * 64,
            root / "hosted",
        )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        verifier = AccessVerifier(settings)
        verifier.client = Mock()
        verifier.client.get_signing_keys.return_value = [
            SimpleNamespace(key_id="test", key=key.public_key())
        ]
        app = create_app(settings, verifier)
        registry = app.state.registry
        alice = registry.provision(str(uuid4()), "Alice Research")
        bob = registry.provision(str(uuid4()), "Bob Research")
        source = seeded_backtest_db(root)
        seed_market(registry, alice["subject"], source.path)
        PortfolioService(registry.database(alice)).create("Alice private account", 10000)

        def token(subject):
            now = int(time.time())
            return jwt.encode(
                dict(
                    iss=settings.issuer,
                    aud=[settings.audience],
                    sub=subject,
                    iat=now,
                    exp=now + 1800,
                    type="app",
                ),
                key,
                algorithm="RS256",
                headers={"kid": "test"},
            )

        with TestClient(app, base_url=settings.origin) as client:
            browser = playwright.chromium.launch()
            for width, height in [(1440, 1000), (390, 844)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                identity = {"token": token(alice["subject"])}

                def route_request(route):
                    request = route.request
                    headers = dict(request.headers)
                    headers["cf-access-jwt-assertion"] = identity["token"]
                    response = client.request(
                        request.method,
                        request.url,
                        headers=headers,
                        content=request.post_data_buffer,
                    )
                    route.fulfill(
                        status=response.status_code,
                        headers=dict(response.headers),
                        body=response.content,
                    )

                context.route("**/*", route_request)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(settings.origin)
                expect(page.locator("#account-label")).to_have_text("Alice Research")
                expect(page.locator("#account-logout")).to_be_visible()
                page.click('[data-view="portfolio"]')
                expect(page.locator("#portfolio-table")).to_contain_text("Alice private account")
                name = f"Browser {width}"
                page.fill('#portfolio-create-form input[name="name"]', name)
                page.fill('#portfolio-create-form input[name="cash"]', "10000.37")
                page.click('#portfolio-create-form button[type="submit"]')
                expect(page.locator("#portfolio-table")).to_contain_text(name)
                assert registry.database(alice).get_portfolio(name)["starting_cash"] == 10000.37
                page.screenshot(path=str(args.output_dir / f"hosted-{width}.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    page.evaluate(
                        "[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth).slice(0,20).map(e => [e.tagName,e.id,e.className,e.getBoundingClientRect().width])"
                    )
                )
                assert not errors, errors
                assert (
                    page.locator("#account-logout").get_attribute("href")
                    == "/cdn-cgi/access/logout"
                )
                identity["token"] = token(bob["subject"])
                page.reload()
                expect(page.locator("#account-label")).to_have_text("Bob Research")
                expect(page.locator("#portfolio-table")).not_to_contain_text(
                    "Alice private account"
                )
                assert page.evaluate('localStorage.getItem("backtestDraft")') is None
                identity["token"] = "expired"
                page.evaluate("api('/api/session').catch(() => null)")
                expect(page.locator("#session-dialog")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator("#session-dialog")).to_be_visible()
                page.screenshot(path=str(args.output_dir / f"expired-{width}.png"), full_page=True)
                context.close()
            browser.close()
    print(
        "Chromium desktop/mobile: signed identity, private portfolios, fractional cash, logout link, expiry, CSP-compatible scripts and layout passed."
    )


if __name__ == "__main__":
    main()
