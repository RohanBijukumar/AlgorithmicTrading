"""Portable saved reports used by both interfaces."""

import csv
import io
import json
from dataclasses import asdict
from datetime import date

from .analytics import enrich_curve
from .portfolio import PortfolioService


def run_record(row, compact=False):
    record = dict(row)
    record["parameters"] = json.loads(record.pop("parameters_json"))
    record["result_summary"] = json.loads(record.pop("result_summary_json") or "{}")
    if compact:
        for key in ("benchmark_points", "attribution", "monthly_returns"):
            record["result_summary"].pop(key, None)
    return record


def backtest_report(db, run_id):
    row = db.get_backtest_run(run_id)
    if row is None:
        raise LookupError(f"backtest {run_id} not found")
    run = run_record(row)
    summary = run["result_summary"]
    name = summary.get("portfolio_name")
    portfolio = db.get_portfolio(name) if name else None
    points, trades, research = [], [], []
    if portfolio:
        saved = db.get_snapshots(portfolio["id"])
        if saved:
            points = [{**p, "date": p["valuation_date"]} for p in saved]
        else:
            points = [
                {**asdict(p), "date": str(p.valuation_date)}
                for p in PortfolioService(db).value_history(
                    name,
                    date.fromisoformat(run["end_date"]),
                    date.fromisoformat(run["start_date"]),
                    100000,
                )
            ]
        trades = [asdict(t) for t in db.get_trades(portfolio["id"])]
        research = [dict(r) for r in db.list_research_decisions(portfolio["id"])]
    starting = run["parameters"].get("starting_cash", summary.get("start_value", 0))
    curve = enrich_curve(points, starting)
    benchmark = {p["date"]: p["total_value"] for p in summary.get("benchmark_points", [])}
    for point in curve:
        point["benchmark_value"] = benchmark.get(point["date"])
    return {"run": run, "equity": curve, "trades": trades, "research": research}


def export_csv(report):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["date", "cash", "market_value", "total_value", "return", "drawdown", "benchmark_value"]
    )
    for point in report["equity"]:
        writer.writerow(
            [
                point.get(key)
                for key in (
                    "date",
                    "cash",
                    "market_value",
                    "total_value",
                    "return",
                    "drawdown",
                    "benchmark_value",
                )
            ]
        )
    return output.getvalue()
