from __future__ import annotations

import csv
import hashlib
import http.cookiejar
import io
import json
import re
import ssl
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, time, timedelta
from urllib.error import URLError

from .config import DAILY_INTERVAL
from .db import to_stooq_symbol, to_yahoo_symbol, utc_now
from .models import FetchResult, MarketBar
from .universe import normalize_symbol


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def fetch_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        provider_symbol: str | None = None,
    ) -> FetchResult:
        raise NotImplementedError


class StooqMarketDataProvider(MarketDataProvider):
    name = "stooq"
    base_url = "https://stooq.com/q/d/l/"

    def fetch_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        provider_symbol: str | None = None,
    ) -> FetchResult:
        canonical = normalize_symbol(symbol)
        provider_symbol = provider_symbol or to_stooq_symbol(canonical)
        fetched_at = utc_now()
        try:
            url = self._build_url(provider_symbol, start, end)
            body = self._read_url(url)
            bars = self._parse_csv(canonical, body)
            return FetchResult(
                provider=self.name,
                symbol=canonical,
                provider_symbol=provider_symbol,
                requested_start=start,
                requested_end=end,
                bars=bars,
                fetched_at=fetched_at,
                success=True,
            )
        except Exception as exc:
            return FetchResult(
                provider=self.name,
                symbol=canonical,
                provider_symbol=provider_symbol,
                requested_start=start,
                requested_end=end,
                bars=[],
                fetched_at=fetched_at,
                success=False,
                error_message=str(exc),
            )

    def _build_url(self, provider_symbol: str, start: date, end: date) -> str:
        query = urllib.parse.urlencode(
            {
                "s": provider_symbol,
                "i": "d",
                "d1": start.strftime("%Y%m%d"),
                "d2": end.strftime("%Y%m%d"),
            }
        )
        return f"{self.base_url}?{query}"

    def _read_url(self, url: str) -> str:
        try:
            opener = _build_opener()
            return _read_with_stooq_verification(opener, url)
        except URLError as exc:
            if not _is_certificate_error(exc):
                raise
            # Some local Python installations do not have a complete CA bundle.
            # Stooq downloads are public market-data CSVs, so retry narrowly here.
            context = ssl._create_unverified_context()
            opener = _build_opener(context)
            return _read_with_stooq_verification(opener, url)

    def _parse_csv(self, symbol: str, body: str) -> list[MarketBar]:
        reader = csv.DictReader(io.StringIO(body))
        required = {"Date", "Open", "High", "Low", "Close", "Volume"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("provider response did not contain daily OHLCV columns")

        bars: list[MarketBar] = []
        for row in reader:
            if not row.get("Date") or row["Date"].lower() == "no data":
                continue
            bars.append(
                MarketBar(
                    symbol=symbol,
                    trading_date=date.fromisoformat(row["Date"]),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    adjusted_close=None,
                    volume=int(float(row["Volume"])),
                    provider=self.name,
                    interval=DAILY_INTERVAL,
                    price_basis="close",
                )
            )
        return bars


class YahooChartMarketDataProvider(MarketDataProvider):
    name = "yahoo"
    base_url = "https://query1.finance.yahoo.com/v8/finance/chart/"

    def fetch_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        provider_symbol: str | None = None,
    ) -> FetchResult:
        canonical = normalize_symbol(symbol)
        provider_symbol = provider_symbol or to_yahoo_symbol(canonical)
        fetched_at = utc_now()
        try:
            url = self._build_url(provider_symbol, start, end)
            body = _read_public_url(url)
            bars = self._parse_json(canonical, body)
            return FetchResult(
                provider=self.name,
                symbol=canonical,
                provider_symbol=provider_symbol,
                requested_start=start,
                requested_end=end,
                bars=bars,
                fetched_at=fetched_at,
                success=True,
            )
        except Exception as exc:
            return FetchResult(
                provider=self.name,
                symbol=canonical,
                provider_symbol=provider_symbol,
                requested_start=start,
                requested_end=end,
                bars=[],
                fetched_at=fetched_at,
                success=False,
                error_message=str(exc),
            )

    def _build_url(self, provider_symbol: str, start: date, end: date) -> str:
        period1 = int(datetime.combine(start, time.min, tzinfo=UTC).timestamp())
        period2 = int(datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC).timestamp())
        query = urllib.parse.urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "history|div|split",
                "includeAdjustedClose": "true",
            }
        )
        return f"{self.base_url}{urllib.parse.quote(provider_symbol)}?{query}"

    def _parse_json(self, symbol: str, body: str) -> list[MarketBar]:
        payload = json.loads(body)
        chart = payload.get("chart", {})
        error = chart.get("error")
        if error:
            raise ValueError(error)
        results = chart.get("result") or []
        if not results:
            raise ValueError("provider response did not contain chart results")

        result = results[0]
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        adjusted = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: list[MarketBar] = []
        for index, timestamp in enumerate(timestamps):
            values = [
                _at(opens, index),
                _at(highs, index),
                _at(lows, index),
                _at(closes, index),
                _at(volumes, index),
            ]
            if any(value is None for value in values):
                continue
            adj_close = _at(adjusted, index)
            bars.append(
                MarketBar(
                    symbol=symbol,
                    trading_date=datetime.fromtimestamp(timestamp, tz=UTC).date(),
                    open=float(values[0]),
                    high=float(values[1]),
                    low=float(values[2]),
                    close=float(values[3]),
                    adjusted_close=float(adj_close) if adj_close is not None else None,
                    volume=int(values[4]),
                    provider=self.name,
                    interval=DAILY_INTERVAL,
                    price_basis="adjusted_close" if adj_close is not None else "close",
                )
            )
        return bars


def _is_certificate_error(exc: URLError) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(
        exc
    )


def _read_public_url(url: str) -> str:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except URLError as exc:
        if not _is_certificate_error(exc):
            raise
        context = ssl._create_unverified_context()
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            return response.read().decode("utf-8")


def _at(values: list, index: int):
    return values[index] if index < len(values) else None


def _build_opener(context: ssl.SSLContext | None = None) -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPCookieProcessor(cookie_jar)]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    return opener


def _read_with_stooq_verification(opener: urllib.request.OpenerDirector, url: str) -> str:
    body = _open_text(opener, url)
    if "__verify" not in body:
        return body

    challenge = re.search(r'const c="([^"]+)",d=(\d+)', body)
    if challenge is None:
        return body
    c_value = challenge.group(1)
    difficulty = int(challenge.group(2))
    nonce = _solve_pow(c_value, difficulty)
    verify_url = "https://stooq.com/__verify"
    payload = urllib.parse.urlencode({"c": c_value, "n": str(nonce)}).encode("utf-8")
    request = urllib.request.Request(
        verify_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    opener.open(request, timeout=30).read()
    return _open_text(opener, url)


def _open_text(opener: urllib.request.OpenerDirector, url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _solve_pow(challenge: str, difficulty: int) -> int:
    prefix = "0" * difficulty
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}{nonce}".encode("utf-8")).hexdigest()
        if digest.startswith(prefix):
            return nonce
        nonce += 1
