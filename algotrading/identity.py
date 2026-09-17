"""Verify gateway identity; never accept unsigned proxy identity headers."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

import jwt


@dataclass(frozen=True)
class HostedSettings:
    origin: str
    issuer: str
    audience: str
    root: Path

    def __post_init__(self):
        origin = urlsplit(self.origin)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
            or origin.port not in (None, 443)
        ):
            raise ValueError("PUBLIC_ORIGIN must be an HTTPS origin without a trailing slash")
        if not re.fullmatch(r"https://[a-z0-9-]+\.cloudflareaccess\.com", self.issuer):
            raise ValueError("ACCESS_ISSUER must be your HTTPS Cloudflare Access team origin")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", self.audience):
            raise ValueError("ACCESS_AUDIENCE must be the application's 64-character AUD tag")
        if not self.root.is_absolute():
            raise ValueError("HOSTED_ROOT must be an absolute private data directory")

    @classmethod
    def from_env(cls):
        return cls(
            os.environ["PUBLIC_ORIGIN"],
            os.environ["ACCESS_ISSUER"],
            os.environ["ACCESS_AUDIENCE"],
            Path(os.environ["HOSTED_ROOT"]),
        )


def subject_id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Expected a canonical Access user UUID")
    return value


class AccessVerifier:
    def __init__(self, settings):
        self.settings = settings
        # No indefinite per-key cache: removed keys expire with the JWKS cache.
        self.client = jwt.PyJWKClient(
            settings.issuer + "/cdn-cgi/access/certs", cache_keys=False, lifespan=300, timeout=5
        )
        self.lock = Lock()
        self.refreshed = 0.0
        self.keys = {}

    def verify(self, token):
        if not token or len(token) > 16384:
            raise jwt.InvalidTokenError("Missing or oversized token")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or len(kid) > 256:
            raise jwt.InvalidTokenError("Invalid signing header")
        # Random unknown kids must not cause a network request on every attempt.
        with self.lock:
            now = monotonic()
            if now - self.refreshed > 300 or (kid not in self.keys and now - self.refreshed > 30):
                self.refreshed = now
                self.keys = {}  # Fail closed when refresh fails; no stale-key fallback.
                self.keys = {
                    key.key_id: key.key for key in self.client.get_signing_keys(refresh=True)
                }
            key = self.keys.get(kid)
        if key is None:
            raise jwt.InvalidTokenError("Unknown signing key")
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=self.settings.audience,
            issuer=self.settings.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "type"]},
            leeway=5,
        )
        if claims["type"] != "app" or claims["exp"] - claims["iat"] > 3600:
            raise jwt.InvalidTokenError("User application token must last at most one hour")
        try:
            return subject_id(claims["sub"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise jwt.InvalidTokenError("Invalid user subject") from exc
