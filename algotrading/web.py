from __future__ import annotations

import json
import mimetypes
import threading
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .backtest import BacktestService
from .config import DEFAULT_DB_PATH
from .db import Database
from .engine import preflight
from .jobs import JobStore
from .market import MarketService
from .portfolio import PortfolioService
from .providers import YahooChartMarketDataProvider
from .reporting import backtest_report, export_csv, run_record
from .simulation import BacktestCancelled
from .strategy_config import PARAMETERS

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def run_server(
    db_path: Path | str = DEFAULT_DB_PATH, host: str = "127.0.0.1", port: int = 8000
) -> None:
    db = Database(db_path)
    db.initialize()

    class Handler(AppHandler):
        database = db

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving AlgorithmicTrading UI at http://{host}:{port}")
    print(f"Using database {Path(db_path)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down UI server")
    finally:
        server.server_close()


class AppHandler(BaseHTTPRequestHandler):
    database: Database
    backtest_jobs = JobStore(max_active=1)
    market_sync_jobs = JobStore(max_active=1)

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}")

    @property
    def market(self) -> MarketService:
        return MarketService(self.database, YahooChartMarketDataProvider())

    @property
    def portfolios(self) -> PortfolioService:
        return PortfolioService(self.database)

    @property
    def backtests(self) -> BacktestService:
        return BacktestService(self.database)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_get(parsed.path, parse_qs(parsed.query))
            else:
                self._serve_static(parsed.path)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        try:
            self._check_origin()
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self._handle_post(parsed.path, self._body())
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        try:
            self._check_origin()
            parsed = urlparse(self.path)
            self._handle_delete(parsed.path)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _handle_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/health":
            self._json({"ok": True, "db": str(self.database.path)})
        elif path == "/api/universe":
            rows = self.database.list_symbols()
            self._json({"symbols": [_row(row) for row in rows]})
        elif path == "/api/market/coverage":
            self._json({"symbols": self.database.coverage_report()})
        elif path == "/api/market/caps":
            from .market_caps import MarketCapService

            self._json(MarketCapService(self.database).status())
        elif path == "/api/overview":
            coverage = self.database.coverage_report()
            covered = [r for r in coverage if r["bar_count"]]
            runs = self.database.list_backtest_runs(5)
            with self.database.connect() as conn:
                run_count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
                portfolios = conn.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0]
            self._json(
                {
                    "symbols": len(coverage),
                    "covered_symbols": len(covered),
                    "bars": sum(r["bar_count"] for r in covered),
                    "portfolios": portfolios,
                    "runs": run_count,
                    "first_date": min((r["first_date"] for r in covered), default=None),
                    "last_date": max((r["last_date"] for r in covered), default=None),
                    "recent_runs": [run_record(r, compact=True) for r in runs],
                }
            )
        elif path == "/api/market/bars":
            symbol = _one(query, "symbol")
            start = _date(_one(query, "from"))
            end = _date(_one(query, "to"))
            self._json({"bars": [_bar(bar) for bar in self.market.bars(symbol, start, end)]})
        elif path == "/api/market/price":
            symbol = _one(query, "symbol")
            target = _date(_one(query, "date"))
            exact = _one(query, "exact", "false").lower() == "true"
            self._json({"bar": _bar(self.market.price(symbol, target, allow_previous=not exact))})
        elif path.startswith("/api/market-sync-jobs/"):
            self._json(
                {
                    "job": self.market_sync_jobs.snapshot(
                        path.rsplit("/", 1)[1], int(_one(query, "after", "0"))
                    )
                }
            )
        elif path == "/api/portfolios":
            self._json({"portfolios": [_row(row) for row in self.database.list_portfolios()]})
        elif path.startswith("/api/portfolios/"):
            self._get_portfolio(path, query)
        elif path == "/api/backtests/strategies":
            self._json(
                {"strategies": self.backtests.strategies(), "catalog": self.backtests.catalog()}
            )
        elif path == "/api/backtests":
            rows = self.database.list_backtest_runs(
                max(1, min(500, _int(_one(query, "limit", "100"))))
            )
            self._json({"backtests": [run_record(row, compact=True) for row in rows]})
        elif path.startswith("/api/backtest-jobs/"):
            self._json(
                {
                    "job": self.backtest_jobs.snapshot(
                        path.rsplit("/", 1)[1], int(_one(query, "after", "0"))
                    )
                }
            )
        elif path.startswith("/api/backtests/") and path.endswith("/report"):
            self._json(backtest_report(self.database, int(path.split("/")[3])))
        elif path.startswith("/api/backtests/") and path.endswith("/export"):
            report = backtest_report(self.database, int(path.split("/")[3]))
            csv_format = _one(query, "format", "json") == "csv"
            data = export_csv(report) if csv_format else json.dumps(report, default=str, indent=2)
            self._download(
                data,
                "text/csv" if csv_format else "application/json",
                f"backtest-{report['run']['id']}.{'csv' if csv_format else 'json'}",
            )
        elif path.startswith("/api/backtests/"):
            run_id = int(path.rsplit("/", 1)[1])
            row = self.database.get_backtest_run(run_id)
            if row is None:
                self._json({"error": "backtest not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json({"backtest": _backtest_row(row)})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _handle_post(self, path: str, body: dict) -> None:
        if path == "/api/market/caps/refresh":
            from .market_caps import MarketCapService

            self._json(MarketCapService(self.database).refresh())
        elif path == "/api/universe/expand":
            from .market_caps import MarketCapService

            count = _shares(body.get("count", 1500))
            if not 1 <= count <= 10000:
                raise ValueError("Expansion count must be 1-10000")
            self._json(MarketCapService(self.database).expand(count))
        elif path == "/api/universe":
            symbol = self.market.add_symbol(
                body["symbol"],
                provider_symbol=body.get("provider_symbol") or None,
                asset_name=body.get("asset_name") or None,
                asset_type=body.get("asset_type") or "equity",
            )
            self._json({"symbol": symbol})
        elif path == "/api/market/sync":
            symbols = (
                self.market.list_symbols() if body.get("all") else list(body.get("symbols") or [])
            )
            if not symbols:
                raise ValueError("select symbols to sync")
            if _date(body["from"]) > _date(body["to"]):
                raise ValueError("start date must be on or before end date")
            symbols = self.backtests._resolve_symbols(symbols)
            job_id = self.market_sync_jobs.create(total=len(symbols))
            threading.Thread(
                target=self._run_market_sync_job, args=(job_id, symbols, body), daemon=True
            ).start()
            self._json({"job_id": job_id, "total": len(symbols)}, HTTPStatus.ACCEPTED)
        elif path == "/api/portfolios":
            portfolio_id = self.portfolios.create(body["name"], float(body["cash"]))
            self._json({"id": portfolio_id})
        elif path.startswith("/api/portfolios/") and path.endswith("/trades"):
            name = unquote(path.split("/")[3])
            side = body["side"]
            if side == "buy":
                trade_id = self.portfolios.buy(
                    name, body["symbol"], _shares(body["shares"]), _date(body["date"])
                )
            elif side == "sell":
                trade_id = self.portfolios.sell(
                    name, body["symbol"], _shares(body["shares"]), _date(body["date"])
                )
            else:
                raise ValueError("side must be buy or sell")
            self._json({"id": trade_id})
        elif path in ("/api/backtests", "/api/backtests/preflight"):
            params = {k: v for k, v in body.items() if k in PARAMETERS and v not in (None, "")}
            check = preflight(
                self.backtests,
                body["strategy"],
                list(body.get("symbols") or []),
                _date(body["from"]),
                _date(body["to"]),
                float(body.get("cash", 100000)),
                params,
            )
            if path.endswith("/preflight"):
                self._json(check)
                return
            if not check["ready"]:
                raise ValueError(
                    "No data for the selected symbols and dates. Sync market data first."
                )
            job_id = self.backtest_jobs.create()
            threading.Thread(
                target=self._run_backtest_job, args=(job_id, body, params), daemon=True
            ).start()
            self._json({"job_id": job_id}, HTTPStatus.ACCEPTED)
        elif path.startswith("/api/backtest-jobs/") and path.endswith("/cancel"):
            self.backtest_jobs.cancel(path.split("/")[3])
            self._json({"ok": True})
        elif path.startswith("/api/market-sync-jobs/") and path.endswith("/cancel"):
            self.market_sync_jobs.cancel(path.split("/")[3])
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _run_market_sync_job(self, job_id: str, symbols: list[str], body: dict) -> None:
        market = self.market
        try:
            result = market.sync(
                symbols,
                _date(body["from"]),
                _date(body["to"]),
                force=bool(body.get("force")),
                progress=lambda e: self.market_sync_jobs.emit(job_id, e),
                continue_on_error=True,
                cancelled=lambda: self.market_sync_jobs.cancelled(job_id),
            )
            self.market_sync_jobs.update(
                job_id, status="completed", result=result, errors=market.errors
            )
        except BacktestCancelled:
            self.market_sync_jobs.update(job_id, status="cancelled")
        except Exception as exc:
            self.market_sync_jobs.update(job_id, status="failed", error=str(exc))

    def _run_backtest_job(self, job_id: str, body: dict, params: dict) -> None:
        try:
            result = self.backtests.run(
                body["strategy"],
                list(body.get("symbols") or []),
                _date(body["from"]),
                _date(body["to"]),
                float(body.get("cash", 100000)),
                params,
                body.get("benchmark") or "SPY",
                progress=lambda e: self.backtest_jobs.emit(job_id, e),
                cancelled=lambda: self.backtest_jobs.cancelled(job_id),
            )
            self.backtest_jobs.update(
                job_id,
                status="completed",
                result={
                    "run_id": result.run_id,
                    "portfolio_name": result.portfolio_name,
                    "trades_executed": result.trades_executed,
                    "metrics": result.metrics,
                    "benchmark": result.benchmark,
                },
            )
        except BacktestCancelled:
            self.backtest_jobs.update(job_id, status="cancelled")
        except Exception as exc:
            self.backtest_jobs.update(job_id, status="failed", error=str(exc))

    def _handle_delete(self, path: str) -> None:
        if path.startswith("/api/universe/"):
            symbol = self.market.remove_symbol(unquote(path.rsplit("/", 1)[1]))
            self._json({"symbol": symbol})
        elif path.startswith("/api/portfolios/"):
            name = unquote(path.rsplit("/", 1)[1])
            if not self.database.delete_portfolio(name):
                self._json({"error": "portfolio not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json({"portfolio": name})
        elif path.startswith("/api/backtests/"):
            run_id = int(path.rsplit("/", 1)[1])
            if not self.database.delete_backtest_run(run_id):
                self._json({"error": "backtest not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json({"id": run_id})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _get_portfolio(self, path: str, query: dict[str, list[str]]) -> None:
        parts = path.split("/")
        name = unquote(parts[3])
        if len(parts) == 4:
            self._json({"portfolio": _portfolio_state(self.portfolios.state(name))})
        elif len(parts) == 5 and parts[4] == "holdings":
            through = _maybe_date(_one(query, "date", ""))
            self._json({"state": _portfolio_state(self.portfolios.state(name, through=through))})
        elif len(parts) == 5 and parts[4] == "value":
            valuation = self.portfolios.value(name, _date(_one(query, "date")))
            self._json({"valuation": _valuation(valuation)})
        elif len(parts) == 5 and parts[4] == "value-history":
            end = _date(_one(query, "to"))
            start = _maybe_date(_one(query, "from", ""))
            max_points = _int(_one(query, "max_points", "260"))
            points = self.portfolios.value_history(
                name, end=end, start=start, max_points=max_points
            )
            self._json({"points": [_value_point(point) for point in points]})
        elif len(parts) == 5 and parts[4] == "history":
            state = self.portfolios.state(name)
            self._json(
                {
                    "trades": [
                        _trade(trade) for trade in self.database.get_trades(state.portfolio_id)
                    ]
                }
            )
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1048576:
            raise ValueError("request body exceeds 1 MB")
        if length == 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def _check_origin(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            raise ValueError("cross-origin writes are not allowed")

    def _download(self, content, mime, filename):
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if (
            not target.is_relative_to(STATIC_DIR.resolve())
            or not target.exists()
            or target.is_dir()
        ):
            target = STATIC_DIR / "index.html"
        content = target.read_bytes()
        mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def _one(query: dict[str, list[str]], key: str, default: str | None = None) -> str:
    values = query.get(key)
    if not values:
        if default is None:
            raise ValueError(f"missing query parameter: {key}")
        return default
    return values[0]


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _maybe_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def _int(value: str) -> int:
    return int(value)


def _row(row) -> dict:
    return dict(row)


def _bar(bar) -> dict:
    price = bar.adjusted_close if bar.adjusted_close is not None else bar.close
    return {
        "symbol": bar.symbol,
        "date": bar.trading_date.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "adjusted_close": bar.adjusted_close,
        "price": price,
        "volume": bar.volume,
        "provider": bar.provider,
        "basis": bar.price_basis,
    }


def _portfolio_state(state) -> dict:
    return {
        "portfolio_id": state.portfolio_id,
        "name": state.name,
        "cash": state.cash,
        "realized_pnl": state.realized_pnl,
        "holdings": [
            {"symbol": symbol, "shares": position.shares, "average_cost": position.average_cost}
            for symbol, position in sorted(state.holdings.items())
        ],
    }


def _valuation(valuation) -> dict:
    payload = {
        **_portfolio_state(valuation.state),
        "valuation_date": valuation.valuation_date.isoformat(),
        "market_value": valuation.market_value,
        "total_value": valuation.total_value,
        "unrealized_pnl": valuation.unrealized_pnl,
    }
    payload["holdings"] = [
        {
            **holding,
            "market_value": valuation.holding_values.get(holding["symbol"], 0.0),
        }
        for holding in payload["holdings"]
    ]
    return payload


def _value_point(point) -> dict:
    return {
        "date": point.valuation_date.isoformat(),
        "cash": point.cash,
        "market_value": point.market_value,
        "total_value": point.total_value,
        "realized_pnl": point.realized_pnl,
        "unrealized_pnl": point.unrealized_pnl,
    }


def _trade(trade) -> dict:
    return {
        "id": trade.id,
        "portfolio_id": trade.portfolio_id,
        "symbol": trade.symbol,
        "side": trade.side,
        "shares": trade.shares,
        "execution_date": trade.execution_date.isoformat(),
        "execution_price": trade.execution_price,
        "cash_impact": trade.cash_impact,
        "fees": trade.fees,
        "slippage": trade.slippage,
        "created_at": trade.created_at.isoformat(),
    }


def _backtest_row(row) -> dict:
    payload = dict(row)
    payload["parameters"] = json.loads(payload.pop("parameters_json"))
    summary = payload.pop("result_summary_json")
    payload["result_summary"] = json.loads(summary) if summary else None
    return payload


def _shares(value):
    number = float(value)
    if isinstance(value, bool) or not number.is_integer() or number <= 0:
        raise ValueError("shares must be a positive integer")
    return int(number)
