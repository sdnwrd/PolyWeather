"""NWS daily-high forecast fetching."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from config import NWS_USER_AGENT

log = logging.getLogger(__name__)

_GRIDPOINT_CACHE: dict[tuple[float, float], dict] = {}

_NWS_BASE = "https://api.weather.gov"
_TIMEOUT = 15


def _headers() -> dict[str, str]:
    return {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json",
    }


def _get_gridpoint(lat: float, lon: float) -> dict:
    key = (round(lat, 4), round(lon, 4))
    if key in _GRIDPOINT_CACHE:
        return _GRIDPOINT_CACHE[key]

    url = f"{_NWS_BASE}/points/{lat},{lon}"
    resp = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    props = resp.json()["properties"]
    grid = {
        "gridId": props["gridId"],
        "gridX": props["gridX"],
        "gridY": props["gridY"],
    }
    _GRIDPOINT_CACHE[key] = grid
    return grid


def _hourly_periods(grid: dict) -> list[dict]:
    url = (
        f"{_NWS_BASE}/gridpoints/{grid['gridId']}/"
        f"{grid['gridX']},{grid['gridY']}/forecast/hourly"
    )
    resp = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["properties"]["periods"]


def get_daily_high(lat: float, lon: float, target_date: Optional[date] = None) -> float:
    """Return today's (or `target_date`'s) predicted daily high in °F."""
    target_date = target_date or date.today()
    grid = _get_gridpoint(lat, lon)
    periods = _hourly_periods(grid)

    today_temps: list[float] = []
    for p in periods:
        start = datetime.fromisoformat(p["startTime"])
        if start.date() != target_date:
            continue
        # NWS hourly forecast returns °F by default for US points
        unit = p.get("temperatureUnit", "F")
        temp = float(p["temperature"])
        if unit == "C":
            temp = temp * 9 / 5 + 32
        today_temps.append(temp)

    if not today_temps:
        raise ValueError(f"No hourly periods found for {target_date}")

    return max(today_temps)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from config import CITIES

    for c in CITIES:
        try:
            high = get_daily_high(c["lat"], c["lon"])
            print(f"{c['name']:<14} forecast high: {high:.0f}°F")
        except Exception as e:
            print(f"{c['name']:<14} ERROR: {e}")
