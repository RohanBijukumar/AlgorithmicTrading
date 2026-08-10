"""Read each price series once; expose only bars at or before the simulation clock."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date

from .db import Database


class MarketWindow:
    def __init__(self, db: Database, symbols: list[str], end: date):
        from .nn_models import sector_for_symbol

        self.source = db
        self.sectors = {
            row["symbol"]: sector_for_symbol(row["symbol"], row["sector"])
            for row in db.list_symbols(active_only=False)
        }
        self.end = end
        self.frontier = end
        self.series = {}
        self.dates = {}
        self.calendars = {}
        self.contiguous_starts = {}
        for symbol in symbols:
            self.reload(symbol)

    def __getattr__(self, name):
        return getattr(self.source, name)

    def reload(self, symbol):
        bars = self.source.get_bars(symbol, date.min, self.end)
        self.series[symbol] = bars
        self.dates[symbol] = [bar.trading_date for bar in bars]
        self.calendars.clear()
        self.contiguous_starts.clear()

    def contiguous_bars(self, symbol, current):
        bars = self.get_bars(symbol, date.min, current)
        if not bars or bars[-1].trading_date != current:
            return []
        if symbol not in self.contiguous_starts:
            sessions = self.calendars.get("reference")
            if sessions is None:
                sessions = {
                    d: i
                    for i, d in enumerate(
                        sorted({d for dates in self.dates.values() for d in dates})
                    )
                }
                self.calendars["reference"] = sessions
            starts = []
            beginning = 0
            previous = None
            for i, day in enumerate(self.dates[symbol]):
                index = sessions[day]
                if previous is not None and index != previous + 1:
                    beginning = i
                starts.append(beginning)
                previous = index
            self.contiguous_starts[symbol] = starts
        return bars[self.contiguous_starts[symbol][len(bars) - 1] :]

    def get_bars(self, symbol, start, end, **kwargs):
        if symbol not in self.series:
            self.reload(symbol)
        dates = self.dates[symbol]
        return self.series[symbol][
            bisect_left(dates, start) : bisect_right(dates, min(end, self.frontier))
        ]

    def get_bar_on_or_before(self, symbol, target_date, **kwargs):
        if symbol not in self.series:
            self.reload(symbol)
        index = bisect_right(self.dates[symbol], min(target_date, self.frontier)) - 1
        return self.series[symbol][index] if index >= 0 else None

    def trading_dates(self, start, end, symbols=None, **kwargs):
        key = tuple(sorted(symbols or self.series))
        if key not in self.calendars:
            for symbol in key:
                if symbol not in self.series:
                    self.reload(symbol)
            self.calendars[key] = sorted({d for s in key for d in self.dates[s]})
        dates = self.calendars[key]
        return dates[bisect_left(dates, start) : bisect_right(dates, min(end, self.frontier))]
