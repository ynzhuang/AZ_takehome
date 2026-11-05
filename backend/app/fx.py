"""ECB reference exchange rates (design §2.3).

ECB quotes units of foreign currency per 1 EUR, so EUR amount = amount / rate.
When the ECB published no rate for a date (weekends, TARGET holidays), the most
recent prior published rate is used, looking back at most MAX_LOOKBACK_DAYS.
Rates are never guessed: if no rate is found, FxRateMissing is raised.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

MAX_LOOKBACK_DAYS = 7  # longest normal gap (Easter weekend) is 4 days


class FxRateMissing(Exception):
    def __init__(self, currency: str, on: date):
        super().__init__(f"No ECB rate for {currency} on or up to {MAX_LOOKBACK_DAYS} days before {on.isoformat()}")
        self.currency = currency
        self.on = on


@dataclass(frozen=True)
class FxRate:
    currency: str
    rate: float  # units of currency per 1 EUR
    rate_date: date  # ECB publication date actually used
    requested_date: date

    @property
    def is_fallback(self) -> bool:
        return self.rate_date != self.requested_date


class FxTable:
    def __init__(self, rates: dict[str, dict[date, float]], version: str, source: str):
        self._rates = rates
        self.version = version
        self.source = source

    @classmethod
    def from_ecb_csv(cls, path: Path) -> "FxTable":
        rates: dict[str, dict[date, float]] = {}
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row.get("CURRENCY_DENOM") != "EUR" or not row.get("OBS_VALUE"):
                    continue
                rates.setdefault(row["CURRENCY"], {})[date.fromisoformat(row["TIME_PERIOD"])] = float(row["OBS_VALUE"])
        if not rates:
            raise ValueError(f"No EUR reference rates found in {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        return cls(rates, version=f"{path.name}@{digest}", source="ECB euro foreign exchange reference rates")

    @property
    def currencies(self) -> set[str]:
        return set(self._rates)

    def lookup(self, currency: str, on: date) -> FxRate:
        if currency == "EUR":
            return FxRate("EUR", 1.0, on, on)
        series = self._rates.get(currency)
        if series is None:
            raise FxRateMissing(currency, on)
        for back in range(MAX_LOOKBACK_DAYS + 1):
            d = on - timedelta(days=back)
            if d in series:
                return FxRate(currency, series[d], d, on)
        raise FxRateMissing(currency, on)

    def rate_range(self, currency: str) -> tuple[FxRate, FxRate]:
        """Lowest and highest published rate for a currency over the whole table."""
        series = self._rates.get(currency)
        if not series:
            raise FxRateMissing(currency, date.today())
        lo = min(series.items(), key=lambda kv: kv[1])
        hi = max(series.items(), key=lambda kv: kv[1])
        return FxRate(currency, lo[1], lo[0], lo[0]), FxRate(currency, hi[1], hi[0], hi[0])

    def coverage(self, currency: str) -> tuple[date, date]:
        series = self._rates[currency]
        return min(series), max(series)


def to_eur(amount: float, rate: FxRate) -> float:
    return round(amount / rate.rate, 2)
