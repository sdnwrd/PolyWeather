"""NWS + Open-Meteo daily-high forecast fetching, plus current-day METAR
observations used by the morning scan's bust check."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from config import NWS_USER_AGENT

log = logging.getLogger(__name__)

_GRIDPOINT_CACHE: dict[tuple[float, float], dict] = {}

_NWS_BASE = "https://api.weather.gov"
_NWS_OBS_URL = "https://api.weather.gov/stations/{station}/observations"
_OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
_AVWX_METAR_URL = "https://aviationweather.gov/api/data/metar"
_TIMEOUT = 15


def _to_fahrenheit(value: float, unit_code: str) -> float:
    if "degC" in unit_code or "celsius" in unit_code.lower():
        return value * 9 / 5 + 32
    return value


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
    lat: float,
    lon: float,
    target_date: Optional[date] = None,
    model: str = "best_match",
) -> Optional[float]:
    """Return an Open-Meteo daily-max forecast in °F for `target_date` (local
    timezone of the city) using `model` (e.g. 'best_match', 'gfs_seamless',
    'ecmwf_ifs025'). Returns None on any error so a missing reading never
    blocks the primary signal flow.
    """
    target_date = target_date or date.today()
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "models": model,
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
    }
    try:
        resp = requests.get(_OPEN_METEO_BASE, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning(
            "Open-Meteo[%s] fetch failed for (%s, %s): %s", model, lat, lon, e
        )
        return None

    daily = data.get("daily") or {}
    temps = daily.get("temperature_2m_max") or []
    if not temps or temps[0] is None:
        log.warning(
            "Open-Meteo[%s] returned no daily max for (%s, %s) on %s",
            model, lat, lon, target_date,
        )
        return None
    return float(temps[0])


def get_primary_forecast(city: dict, target_date: Optional[date] = None) -> Optional[float]:
    """Primary forecast for a city — NDFD for US, Open-Meteo best_match for
    international (NDFD is US-only)."""
    target_date = target_date or date.today()
    if city.get("region") == "us":
        try:
            return get_daily_high(city["lat"], city["lon"], target_date)
        except Exception as e:
            log.warning("NDFD primary forecast failed for %s: %s", city["name"], e)
            return None
    return get_openmeteo_high(city["lat"], city["lon"], target_date, model="best_match")


def get_veto_forecast(city: dict, target_date: Optional[date] = None) -> Optional[float]:
    """Second source for the spread-based veto. For US cities, Open-Meteo
    best_match (genuinely different provider from NDFD). For international,
    GFS via Open-Meteo (different model than best_match, which is
    ECMWF-weighted)."""
    target_date = target_date or date.today()
    if city.get("region") == "us":
        return get_openmeteo_high(city["lat"], city["lon"], target_date, model="best_match")
    return get_openmeteo_high(city["lat"], city["lon"], target_date, model="gfs_seamless")


def get_observed_high(city: dict, target_date: date) -> Optional[float]:
    """Observed daily max for journal backfill. NWS observations endpoint for
    US, Open-Meteo historical-forecast archive (observations sourced from
    nearby stations) for international."""
    if city.get("region") == "us":
        # Imported lazily to avoid a circular import at module load time —
        # journal imports forecast indirectly via main.
        from journal import get_observed_high as _nws_obs
        try:
            return _nws_obs(city.get("station", ""), target_date)
        except Exception as e:
            log.warning("NWS observation fetch failed for %s on %s: %s",
                        city["name"], target_date, e)
            return None

    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
    }
    try:
        resp = requests.get(
            "https://historical-forecast-api.open-meteo.com/v1/forecast",
            params=params,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Open-Meteo archive fetch failed for %s on %s: %s",
                    city["name"], target_date, e)
        return None
    daily = data.get("daily") or {}
    temps = daily.get("temperature_2m_max") or []
    if not temps or temps[0] is None:
        return None
    return float(temps[0])


# ---------- Current-day METAR for signal-fire bust check ----------

def _local_today_utc_window(city: dict) -> Optional[tuple[datetime, datetime]]:
    """(start_utc, end_utc) bracketing the city's current local-day so far."""
    tz_name = city.get("tz")
    if not tz_name:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None
    local_now = datetime.now(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (local_midnight.astimezone(timezone.utc), local_now.astimezone(timezone.utc))


def get_day_max_temp(city: dict) -> Optional[float]:
    """Highest observed METAR temperature so far today at the city's station,
    in °F. Mirrors what Wunderground/Polymarket use for resolution. None on
    any error so a missing reading never blocks the signal flow.

    US: NWS observations endpoint, windowed to city's local-day in UTC.
    Intl: aviationweather.gov with hours=24, filtered to local-day.
    """
    station = city.get("station", "")
    if not station:
        return None

    window = _local_today_utc_window(city)
    if window is None:
        return None
    start_utc, end_utc = window

    if city.get("region") == "us":
        try:
            resp = requests.get(
                _NWS_OBS_URL.format(station=station),
                params={
                    "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            features = resp.json().get("features", []) or []
            temps_f: list[float] = []
            for feat in features:
                props = feat.get("properties", {}) or {}
                temp_obj = props.get("temperature") or {}
                value = temp_obj.get("value")
                if value is None:
                    continue
                temps_f.append(_to_fahrenheit(float(value), temp_obj.get("unitCode") or ""))
            return max(temps_f) if temps_f else None
        except (requests.RequestException, ValueError) as e:
            log.warning("NWS day-max fetch failed for %s: %s", station, e)
            return None

    try:
        resp = requests.get(
            _AVWX_METAR_URL,
            params={"ids": station, "format": "json", "hours": 24},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            return None
        temps_f: list[float] = []
        for m in data:
            obs_epoch = m.get("obsTime")
            temp_c = m.get("temp")
            if obs_epoch is None or temp_c is None:
                continue
            obs_dt = datetime.fromtimestamp(obs_epoch, tz=timezone.utc)
            if not (start_utc <= obs_dt <= end_utc):
                continue
            temps_f.append(float(temp_c) * 9 / 5 + 32)
        return max(temps_f) if temps_f else None
    except (requests.RequestException, ValueError) as e:
        log.warning("aviationweather day-max fetch failed for %s: %s", station, e)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from config import CITIES

    for c in CITIES:
        try:
            high = get_daily_high(c["lat"], c["lon"])
            print(f"{c['name']:<14} forecast high: {high:.0f}°F")
        except Exception as e:
            print(f"{c['name']:<14} ERROR: {e}")
