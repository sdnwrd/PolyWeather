"""Signal journal: append every fired signal to a CSV, backfill the
observed daily high once the day has resolved, expose resolved rows for
sigma calibration.

This replaces the older `paper.py` paper-trading log. Since the user is
trading live (not on simulated stakes), there's no portfolio P&L math —
the journal exists only to record predictions vs outcomes so:
  (a) the strategy can self-calibrate sigma per (city, source) over time
  (b) the veto threshold can be backtested against real outcomes

Schema (data/signals.csv):
    date, city, station, question, bracket_low, bracket_high,
    market_price, forecast_high, model_true_prob, ev,
    forecast_openmeteo, model_spread, vetoed,
    actual_high, outcome, checked_at

`outcome` is one of: PENDING, WIN, LOSS, ERROR.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from config import CITIES, NWS_USER_AGENT
from signals import Signal

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "signals.csv"

FIELDS = [
    "date", "city", "station", "question",
    "bracket_low", "bracket_high",
    "market_price", "forecast_high", "model_true_prob", "ev",
    "forecast_openmeteo", "model_spread", "vetoed",
    "actual_high", "outcome", "checked_at",
]


def _city_station(city_name: str) -> str:
    for c in CITIES:
        if c["name"] == city_name:
            return c.get("station", "")
    return ""


def _ensure_csv() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(FIELDS)


def _row_key(row: dict) -> tuple:
    """Identity for dedup: same date+city+bracket = same signal."""
    return (row["date"], row["city"], row["bracket_low"], row["bracket_high"])


def log_signals(signals: list[Signal], today: date) -> int:
    """Append fired signals to the CSV. Returns count of new rows written.

    Always rewrites the whole file so a schema change (new columns added to
    FIELDS) migrates transparently — old rows pick up empty values for the
    new columns instead of desyncing the header.
    """
    _ensure_csv()
    with CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    existing: set[tuple] = {_row_key(r) for r in rows}

    new_rows = 0
    for s in signals:
        row = {
            "date": today.isoformat(),
            "city": s.market.city,
            "station": _city_station(s.market.city),
            "question": s.market.question,
            "bracket_low": s.market.bracket_low,
            "bracket_high": s.market.bracket_high,
            "market_price": round(s.market_price, 4),
            "forecast_high": round(s.forecast_high, 1),
            "model_true_prob": round(s.true_prob, 4),
            "ev": round(s.ev, 2),
            "forecast_openmeteo": (
                round(s.forecast_openmeteo, 1) if s.forecast_openmeteo is not None else ""
            ),
            "model_spread": (
                round(s.model_spread, 1) if s.model_spread is not None else ""
            ),
            "vetoed": "true" if s.vetoed else "false",
            "actual_high": "",
            "outcome": "PENDING",
            "checked_at": "",
        }
        if _row_key(row) in existing:
            continue
        rows.append(row)
        existing.add(_row_key(row))
        new_rows += 1

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return new_rows


# ---------- backfill: look up observed high from NWS for resolved days ----------

_OBS_URL = "https://api.weather.gov/stations/{station}/observations"
_OBS_TIMEOUT = 25


def _to_fahrenheit(value: float, unit_code: str) -> float:
    if "degC" in unit_code or "celsius" in unit_code.lower():
        return value * 9 / 5 + 32
    return value


def get_observed_high(station: str, target_date: date) -> Optional[float]:
    """Highest observed temperature at NWS station on target_date (UTC day).

    Caveat: Polymarket resolves on Wunderground's whole-degree readout for
    the local-day calendar, which differs from this NWS float by ≤1°F at
    bracket boundaries. Good enough for calibration; not perfect for true
    Polymarket-aligned WIN/LOSS on edge-case days.
    """
    if not station:
        return None
    start = f"{target_date.isoformat()}T00:00:00Z"
    end = f"{target_date.isoformat()}T23:59:59Z"
    resp = requests.get(
        _OBS_URL.format(station=station),
        params={"start": start, "end": end},
        headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"},
        timeout=_OBS_TIMEOUT,
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    temps: list[float] = []
    for feat in features:
        props = feat.get("properties", {}) or {}
        temp_obj = props.get("temperature") or {}
        value = temp_obj.get("value")
        if value is None:
            continue
        temps.append(_to_fahrenheit(float(value), temp_obj.get("unitCode") or ""))
    return max(temps) if temps else None


def _bracket_contains(low: float, high: float, value: float) -> bool:
    # Polymarket brackets are inclusive at both ends per market language
    return low <= value <= high


def backfill(today: Optional[date] = None) -> dict[str, int]:
    """Look up actual highs for all PENDING rows whose resolution date is in
    the past. Returns counts: {filled, still_pending, errors}.
    """
    today = today or date.today()
    if not CSV_PATH.exists():
        log.info("no signals.csv yet — nothing to backfill")
        return {"filled": 0, "still_pending": 0, "errors": 0}

    with CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    needs_lookup: dict[tuple[str, str], list[dict]] = {}
    counts = {"filled": 0, "still_pending": 0, "errors": 0}

    for row in rows:
        if row["outcome"] != "PENDING":
            continue
        try:
            row_date = date.fromisoformat(row["date"])
        except ValueError:
            counts["errors"] += 1
            continue
        now_utc = datetime.now(timezone.utc).date()
        if row_date >= now_utc:
            counts["still_pending"] += 1
            continue
        needs_lookup.setdefault((row["station"], row["date"]), []).append(row)

    cache: dict[tuple[str, str], Optional[float]] = {}
    for (station, date_str), group in needs_lookup.items():
        try:
            cache[(station, date_str)] = get_observed_high(
                station, date.fromisoformat(date_str)
            )
        except Exception as e:
            log.warning("obs lookup failed for %s %s: %s", station, date_str, e)
            cache[(station, date_str)] = None
            counts["errors"] += len(group)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        if row["outcome"] != "PENDING":
            continue
        key = (row["station"], row["date"])
        if key not in cache:
            continue
        actual = cache[key]
        if actual is None:
            continue
        try:
            lo = float(row["bracket_low"]) if row["bracket_low"] not in ("-inf", "") else float("-inf")
            hi = float(row["bracket_high"]) if row["bracket_high"] not in ("inf", "") else float("inf")
        except ValueError:
            counts["errors"] += 1
            continue
        row["actual_high"] = round(actual, 1)
        row["outcome"] = "WIN" if _bracket_contains(lo, hi, actual) else "LOSS"
        row["checked_at"] = now_iso
        counts["filled"] += 1

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return counts


def read_resolved() -> list[dict]:
    """Return all resolved (WIN/LOSS) rows for downstream callers like
    sigma calibration."""
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("outcome") in ("WIN", "LOSS")]


def short_status() -> str:
    """One-line journal status for diagnostic logging only — no P&L framing,
    since the user trades live. Just counts."""
    if not CSV_PATH.exists():
        return "(no signals logged yet)"
    with CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return "(no signals logged yet)"
    pending = sum(1 for r in rows if r["outcome"] == "PENDING")
    resolved = sum(1 for r in rows if r["outcome"] in ("WIN", "LOSS"))
    return f"journal: total={len(rows)}, resolved={resolved}, pending={pending}"


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--backfill", action="store_true", help="Fill in actual highs for resolved days")
    p.add_argument("--status", action="store_true", help="Print short journal status")
    args = p.parse_args()
    if args.backfill:
        counts = backfill()
        print(f"backfill: filled={counts['filled']}, still_pending={counts['still_pending']}, errors={counts['errors']}")
    if args.status or not args.backfill:
        print(short_status())
