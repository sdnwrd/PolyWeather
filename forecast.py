"""NWS + Open-Meteo daily-high forecast fetching."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from config import NWS_USER_AGENT

log = logging.getLogger(__name__)

_GRIDPOINT_CACHE: dict[tuple[float, float], dict] = {}

_NWS_BASE = "https://api.weather.gov"
_OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
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


def get_openmeteo_high(
    lat: float, lon: float, target_date: Optional[date] = None
) -> Optional[float]:
    """Return Open-Meteo's `best_match` blended-ensemble daily max in °F for
    `target_date` (local timezone of the city). Returns None on any error so
    a missing Open-Meteo reading never blocks the NDFD-driven signal flow.
    """
    target_date = target_date or date.today()
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "models": "best_match",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
    }
    try:
        resp = requests.get(_OPEN_METEO_BASE, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Open-Meteo fetch failed for (%s, %s): %s", lat, lon, e)
        return None

    daily = data.get("daily") or {}
    temps = daily.get("temperature_2m_max") or []
    if not temps or temps[0] is None:
        log.warning("Open-Meteo returned no daily max for (%s, %s) on %s", lat, lon, target_date)
        return None
    return float(temps[0])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from config import CITIES

    for c in CITIES:
        try:
            high = get_daily_high(c["lat"], c["lon"])
            print(f"{c['name']:<14} forecast high: {high:.0f}°F")
        except Exception as e:
            print(f"{c['name']:<14} ERROR: {e}")
