"""Single-instance, authenticated ASGI adapter for the existing research services."""

import asyncio
import fcntl
import json
import logging
import mimetypes
import os
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date
from threading import BoundedSemaphore, Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import jwt
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .identity import AccessVerifier, HostedSettings
from .jobs import JobStore
from .tenancy import Registry
from .web import STATIC_DIR, AppHandler

LOG = logging.getLogger("algotrading.security")
MAX_BODY = 65536
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
}


class Rejected(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class PrivateJobs(JobStore):
    def __init__(self, registry, subject):
        super().__init__(max_active=1)
        self.registry, self.subject = registry, subject

    def cancelled(self, key):
        return self.registry.get(self.subject) is None or super().cancelled(key)

    def emit(self, key, event):
        if event.get("status") == "failed":
            event = {
                "symbol": event.get("symbol"),
                "status": "failed",
                "message": "Provider download failed; retry later or contact the operator",
            }
        super().emit(key, event)

    def update(self, key, **kwargs):
        if kwargs.get("error"):
            kwargs["error"] = "Research job failed; check inputs or contact the operator"
        kwargs.pop("errors", None)
        super().update(key, **kwargs)


class Workspace:
    def __init__(self, registry, user):
        self.database = registry.database(user)
        self.backtests = PrivateJobs(registry, user["subject"])
        self.sync = PrivateJobs(registry, user["subject"])


class Runtime:
    def __init__(self, registry):
        self.registry = registry
        self.workspaces = {}
        self.rates = defaultdict(deque)
        self.lock = Lock()
        self.slots = BoundedSemaphore(2)
        self.request_slots = BoundedSemaphore(8)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research")

    def workspace(self, user):
        with self.lock:
            if user["subject"] not in self.workspaces:
                self.workspaces[user["subject"]] = Workspace(self.registry, user)
            return self.workspaces[user["subject"]]

    def admit(self, subject, mutation):
        now = monotonic()
        with self.lock:
            queue = self.rates[(subject, mutation)]
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if len(queue) >= (20 if mutation else 300):
                raise Rejected(429, "Request limit reached; wait a minute")
            queue.append(now)

    def submit(self, store, target, job_id, args):
        if not self.slots.acquire(blocking=False):
            store.update(job_id, status="failed", error="Server busy; try again shortly")
            raise Rejected(429, "All research workers are busy; try again shortly")

        def work():
            try:
                target(job_id, *args)
                # Legacy provider exceptions may contain URLs or internal paths.
                with store.lock:
                    job = store.jobs[job_id]
                    if job.get("error"):
                        job["error"] = "Research job failed; check inputs or contact the operator"
                    job.pop("errors", None)
            finally:
                self.slots.release()

        try:
            self.executor.submit(work)
        except Exception:
            self.slots.release()
            store.update(job_id, status="failed", error="Server shutting down")
            raise

    def close(self):
        for workspace in self.workspaces.values():
            for store in (workspace.backtests, workspace.sync):
                with store.lock:
                    for job in store.jobs.values():
                        job["cancel"].set()
        self.executor.shutdown(wait=True, cancel_futures=True)


class HostedHandler(AppHandler):
    def __init__(self, workspace, runtime):
        self.database = workspace.database
        self.backtest_jobs = workspace.backtests
        self.market_sync_jobs = workspace.sync
        self.runtime = runtime
        self.response = None

    def _json(self, payload, status=200):
        self.response = Response(
            json.dumps(payload, default=str), status_code=int(status), media_type="application/json"
        )

    def _download(self, content, mime, filename):
        self.response = Response(
            content,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _submit_job(self, store, target, job_id, *args):
        self.runtime.submit(store, target, job_id, args)


def validate_work(path, body, handler):
    if path in ("/api/market/sync", "/api/backtests", "/api/backtests/preflight"):
        start, end = date.fromisoformat(body["from"]), date.fromisoformat(body["to"])
        if start > end or (end - start).days > 36525:
            raise ValueError("Invalid research date range")
        if not isinstance(body.get("symbols", []), list):
            raise ValueError("symbols must be a list")
        supplied = body.get("symbols", [])
        if len(supplied) > 2000 or any(not isinstance(s, str) or len(s) > 32 for s in supplied):
            raise ValueError("Too many or invalid symbols")
        if path == "/api/market/sync":
            symbols = handler.market.list_symbols() if body.get("all") else supplied
            if len(handler.backtests._resolve_symbols(symbols)) > 100:
                raise Rejected(422, "Hosted sync is limited to 100 symbols per request")
        else:
            selected = handler.backtests._resolve_symbols(supplied)
            if len(selected) > 2000:
                raise Rejected(422, "Hosted backtests are limited to 2000 symbols")
            symbols = set(selected + [body.get("benchmark") or "SPY"])
            # Replay loads pre-start history too; bound actual input rows, not just window length.
            with handler.database.connect() as conn:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM market_bars WHERE symbol IN ({','.join('?' for _ in symbols)}) AND trading_date <= ?",
                    (*symbols, end.isoformat()),
                ).fetchone()[0]
            if count > 500000:
                raise Rejected(
                    422, "Hosted replay is limited to 500,000 cached bars; select fewer symbols"
                )
    if body.get("agent_online_research") not in (None, "", 0, "0", False):
        raise Rejected(422, "Online discovery is disabled in hosted mode; use cached research")


def dispatch(request, body, workspace, runtime):
    handler = HostedHandler(workspace, runtime)
    path = request.url.path
    if request.method == "GET":
        handler._handle_get(path, parse_qs(request.url.query))
    elif request.method == "POST":
        validate_work(path, body, handler)
        handler._handle_post(path, body)
    else:
        handler._handle_delete(path)
    return handler.response or JSONResponse({"error": "Not found"}, 404)


class ResponseSecurity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def secured(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.decode().lower() not in {h.lower() for h in HEADERS}
                ]
                message["headers"] = headers + [
                    (k.lower().encode(), v.encode()) for k, v in HEADERS.items()
                ]
            await send(message)

        await self.app(scope, receive, secured)


def create_app(settings=None, verifier=None):
    settings = settings or HostedSettings.from_env()
    os.umask(0o077)
    registry = Registry(settings.root, settings.issuer)
    verifier = verifier or AccessVerifier(settings)
    runtime = Runtime(registry)

    @asynccontextmanager
    async def lifespan(app):
        with (settings.root / ".server.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Hosted mode requires exactly one process per data root"
                ) from exc
            try:
                yield
            finally:
                await run_in_threadpool(runtime.close)

    async def endpoint(request: Request):
        request_id, user = uuid4().hex, None
        try:
            if request.headers.get("host") != urlsplit(settings.origin).netloc:
                raise Rejected(400, "Invalid host")
            if len(request.url.path) + len(request.url.query) > 4096:
                raise Rejected(414, "Request URL too long")
            token_headers = request.headers.getlist("cf-access-jwt-assertion")
            if len(token_headers) != 1:
                raise Rejected(401, "Sign in through the secure site address")
            subject = await run_in_threadpool(verifier.verify, token_headers[0])
            user = await run_in_threadpool(registry.get, subject)
            if user is None:
                raise Rejected(403, "Account not provisioned or disabled; contact the operator")
            mutation = request.method != "GET"
            runtime.admit(subject, mutation)
            body = {}
            if mutation:
                if (
                    request.headers.get("origin") != settings.origin
                    or request.headers.get("x-requested-with") != "AlgorithmicTrading"
                    or request.headers.get("sec-fetch-site") not in (None, "same-origin")
                ):
                    raise Rejected(403, "Cross-origin or unverified browser request")
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    raise Rejected(415, "JSON content type required")
                data = bytearray()
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > MAX_BODY:
                            raise Rejected(413, "Request exceeds 64 KB")
                        data.extend(chunk)
                body = json.loads(data or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
            path = request.url.path
            if path == "/api/session" and not mutation:
                response = JSONResponse(
                    {
                        "hosted": True,
                        "label": user["label"],
                        "workspace": user["workspace"],
                        "logout_url": "/cdn-cgi/access/logout",
                        "paper_only": True,
                    }
                )
            elif path == "/api/health" and not mutation:
                response = JSONResponse({"ok": True, "db": "Private workspace", "hosted": True})
            elif path.startswith("/api/"):
                workspace = runtime.workspace(user)
                if not runtime.request_slots.acquire(blocking=False):
                    raise Rejected(429, "Server busy; try again shortly")
                try:
                    response = await run_in_threadpool(dispatch, request, body, workspace, runtime)
                finally:
                    runtime.request_slots.release()
            elif (
                path in ("/", "/index.html", "/app.js", "/styles.css", "/lucide.min.js")
                and not mutation
            ):
                file = STATIC_DIR / ("index.html" if path == "/" else path.lstrip("/"))
                content = await run_in_threadpool(file.read_bytes)
                response = Response(content, media_type=mimetypes.guess_type(file.name)[0])
            else:
                raise Rejected(404, "Not found")
        except Rejected as exc:
            response = JSONResponse({"error": exc.message}, exc.status)
        except jwt.PyJWKClientConnectionError:
            response = JSONResponse({"error": "Identity verification temporarily unavailable"}, 503)
        except jwt.PyJWTError:
            response = JSONResponse({"error": "Session expired or invalid; sign in again"}, 401)
        except LookupError:
            response = JSONResponse({"error": "Resource not found"}, 404)
        except (ValueError, TypeError, OverflowError):
            response = JSONResponse({"error": "Invalid request; check the supplied values"}, 422)
        except TimeoutError:
            response = JSONResponse({"error": "Request timed out"}, 408)
        except Exception as exc:
            LOG.error(
                json.dumps(
                    {"event": "request_error", "request_id": request_id, "kind": type(exc).__name__}
                )
            )
            response = JSONResponse({"error": "Request failed; contact the operator"}, 500)
        response.headers["X-Request-ID"] = request_id
        if response.status_code == 429:
            response.headers["Retry-After"] = "60"
        if (
            request.method != "GET"
            or response.status_code >= 400
            or request.url.path.endswith("/export")
        ):
            LOG.warning(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "workspace": user["workspace"] if user else None,
                        "method": request.method,
                        "status": response.status_code,
                        "resource": "/".join(request.url.path.split("/")[:3]),
                    }
                )
            )
        return response

    app = Starlette(
        routes=[Route("/{path:path}", endpoint, methods=["GET", "POST", "DELETE"])],
        lifespan=lifespan,
    )
    app.add_middleware(ResponseSecurity)
    app.state.registry, app.state.runtime = registry, runtime
    return app
