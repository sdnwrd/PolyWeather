"""Per-market JSON snapshot store.

Each (city, resolution_date) gets one growable JSON file at
`data/markets/{city_slug}_{YYYY-MM-DD}.json` that accumulates a list of
forecast + market-state snapshots over time — one per scan.

Why: the flat CSV journal only records the moment a signal fires. The
JSON snapshots capture the *evolution* across scans (morning forecast
vs. intraday METAR vs. mid-day re-scan) which is what we need to
backtest the veto, calibrate sigma per (city, source), and diagnose
busts ("did the forecast walk into reality before the market did?").
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from markets import Market
from signals import Signal

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
MARKETS_DIR = DATA_DIR / "markets"


def _slug(city_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", city_name.lower()).strip("-")


def _path(city_name: str, target: date) -> Path:
    return MARKETS_DIR / f"{_slug(city_name)}_{target.isoformat()}.json"


def _market_to_dict(m: Market) -> dict:
    return {
        "question": m.question,
        "bracket_low": m.bracket_low,
        "bracket_high": m.bracket_high,
        "bestBid": m.best_bid,
        "bestAsk": m.best_ask,
        "price": m.price,
        "volume": m.volume,
        "accepting_orders": m.accepting_orders,
        "market_id": m.market_id,
        "token_id": m.token_id,
    }


def _signal_to_dict(s: Signal) -> dict:
    return {
        "bracket_low": s.market.bracket_low,
        "bracket_high": s.market.bracket_high,
        "market_id": s.market.market_id,
        "market_price": s.market_price,
        "forecast_high": s.forecast_high,
        "true_prob": s.true_prob,
        "ev": s.ev,
        "forecast_openmeteo": s.forecast_openmeteo,
        "model_spread": s.model_spread,
        "vetoed": s.vetoed,
    }


def record_snapshot(
    city: dict,
    target: date,
    scan_type: str,
    primary_forecast: Optional[float],
    veto_forecast: Optional[float],
    markets: Iterable[Market],
    signals: Iterable[Signal],
    metar_observed: Optional[float] = None,
) -> None:
    """Append one snapshot to the (city, target) JSON file.

    `scan_type` is a free-form tag — current callers use "morning" (04 UTC
    daily cron) and "intraday" (mid-day METAR veto cron). `metar_observed`
    is the current observed temperature if available (METAR cron only).
    """
    try:
        MARKETS_DIR.mkdir(parents=True, exist_ok=True)
        path = _path(city["name"], target)
        if path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
        else:
            doc = {
                "city": city["name"],
                "date": target.isoformat(),
                "station": city.get("station", ""),
                "region": city.get("region", ""),
                "lat": city.get("lat"),
                "lon": city.get("lon"),
                "snapshots": [],
            }

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        spread = (
            abs(primary_forecast - veto_forecast)
            if primary_forecast is not None and veto_forecast is not None
            else None
        )
        primary_source = "ndfd" if city.get("region") == "us" else "openmeteo-best"
        veto_source = "openmeteo-best" if city.get("region") == "us" else "openmeteo-gfs"

        snap = {
            "ts": now,
            "scan_type": scan_type,
            "primary_forecast": primary_forecast,
            "primary_source": primary_source,
            "veto_forecast": veto_forecast,
            "veto_source": veto_source,
            "spread": spread,
            "metar_observed": metar_observed,
            "markets": [_market_to_dict(m) for m in markets],
            "signals_fired": [_signal_to_dict(s) for s in signals],
        }
        doc["snapshots"].append(snap)
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except Exception as e:
        # Snapshot writing is best-effort — never break the scan flow if
        # disk is full, permissions weird, etc.
        log.warning("snapshot write failed for %s %s: %s", city["name"], target, e)


def load_snapshots(city_name: str, target: date) -> Optional[dict]:
    path = _path(city_name, target)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("snapshot read failed for %s %s: %s", city_name, target, e)
        return None


def iter_all_snapshot_files() -> list[Path]:
    """List every snapshot file for analysis jobs (e.g. sigma calibration)."""
    if not MARKETS_DIR.exists():
        return []
    return sorted(MARKETS_DIR.glob("*.json"))
