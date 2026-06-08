"""Intraday METAR veto.

Runs as a second daily cron (~18 UTC) — well after sunrise everywhere
but before US-East-coast peak. Pulls the latest METAR observation for
every city that fired a non-vetoed signal this morning, and if observed
temp is already outside (above) the signal's bracket, sends a follow-up
"INTRADAY VETO" Telegram so the user can close / stop tracking.

This catches the case where the forecast was wrong and reality has
already moved past the bracket before the day's high is even reached.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from config import CITIES, NWS_USER_AGENT
import journal
import notifier
import snapshots

log = logging.getLogger(__name__)

_NWS_LATEST_URL = "https://api.weather.gov/stations/{station}/observations/latest"
_NWS_OBS_URL = "https://api.weather.gov/stations/{station}/observations"
_AVWX_METAR_URL = "https://aviationweather.gov/api/data/metar"
_TIMEOUT = 15


def _city_by_name(name: str) -> Optional[dict]:
    for c in CITIES:
        if c["name"] == name:
            return c
    return None


def _to_fahrenheit(value: float, unit_code: str) -> float:
    if "degC" in unit_code or "celsius" in unit_code.lower():
        return value * 9 / 5 + 32
    return value


def _local_today_utc_window(city: dict) -> Optional[tuple[datetime, datetime]]:
    """Return (start_utc, end_utc) bracketing the city's current local-day
    so far. None if timezone is missing or unresolvable."""
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
    """Return the highest observed METAR temperature SO FAR TODAY at the
    city's station, in °F — the city's local-day max, which is what
    Wunderground/Polymarket use for resolution. Returns None on any error.

    The fix vs. latest-METAR: a city past its peak (e.g. London at 16:20
    BST after a 16°C high at 14:00) will report a cooler current temp.
    Using only the latest reading would miss that the bracket has already
    been busted.
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

    # International — pull last 24h of METARs and filter to local-today
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


def get_latest_metar_temp(city: dict) -> Optional[float]:
    """Back-compat alias — kept for callers that genuinely want the latest
    single reading (e.g. snapshot recording). Most veto/bust logic should
    use get_day_max_temp instead, which reflects what Wunderground will
    resolve on."""
    station = city.get("station", "")
    if not station:
        return None

    if city.get("region") == "us":
        try:
            resp = requests.get(
                _NWS_LATEST_URL.format(station=station),
                headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            props = resp.json().get("properties") or {}
            temp_obj = props.get("temperature") or {}
            value = temp_obj.get("value")
            if value is None:
                return None
            return _to_fahrenheit(float(value), temp_obj.get("unitCode") or "")
        except (requests.RequestException, ValueError) as e:
            log.warning("NWS latest fetch failed for %s: %s", station, e)
            return None

    try:
        resp = requests.get(
            _AVWX_METAR_URL,
            params={"ids": station, "format": "json"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            return None
        temp_c = data[0].get("temp")
        if temp_c is None:
            return None
        return float(temp_c) * 9 / 5 + 32
    except (requests.RequestException, ValueError) as e:
        log.warning("aviationweather METAR fetch failed for %s: %s", station, e)
        return None


def _todays_active_signals(today: date) -> list[dict]:
    """Journal rows for today's signals that fired (vetoed=false), still PENDING."""
    if not journal.CSV_PATH.exists():
        return []
    with journal.CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    today_iso = today.isoformat()
    return [
        r for r in rows
        if r.get("date") == today_iso
        and r.get("vetoed") != "true"
        and r.get("outcome") == "PENDING"
    ]


def _bracket_busted(observed: float, lo: float, hi: float) -> bool:
    """A signal is 'intraday-busted' if observed temp has already exceeded
    the bracket's high end. Below the low end doesn't yet bust (temps still
    rising toward the day's max), but above the high end means the high will
    only get further away."""
    return observed > hi


def check_intraday_veto(now: Optional[datetime] = None) -> int:
    """Returns count of intraday-veto alerts sent."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    active = _todays_active_signals(today)
    if not active:
        log.info("intraday: no active non-vetoed signals for %s", today)
        return 0

    # Group by city — one METAR call per city, not per signal
    by_city: dict[str, list[dict]] = {}
    for r in active:
        by_city.setdefault(r["city"], []).append(r)

    busts: list[dict] = []
    for city_name, rows in by_city.items():
        city = _city_by_name(city_name)
        if not city:
            continue
        observed = get_day_max_temp(city)
        if observed is None:
            log.info("intraday: no METAR for %s", city_name)
            continue
        log.info("intraday: %s day-max=%.1f°F", city_name, observed)
        # Best-effort snapshot append so the JSON history has the intraday data
        snapshots.record_snapshot(
            city=city, target=today, scan_type="intraday",
            primary_forecast=None, veto_forecast=None,
            markets=[], signals=[], metar_observed=observed,
        )
        for row in rows:
            try:
                lo = float(row["bracket_low"])
                hi = float(row["bracket_high"])
            except (TypeError, ValueError):
                continue
            if _bracket_busted(observed, lo, hi):
                busts.append({
                    "city": city_name,
                    "bracket": f"{int(lo)}-{int(hi)}°F",
                    "observed": observed,
                    "forecast": row.get("forecast_high", "?"),
                    "market_price_cents": float(row.get("market_price", 0)) * 100,
                })

    if not busts:
        log.info("intraday: %d active signals checked, no busts", len(active))
        return 0

    msg = _build_intraday_message(busts, today)
    sent = notifier.send(msg)
    log.info("intraday: %d busts, telegram sent=%s", len(busts), sent)
    return len(busts)


def _build_intraday_message(busts: list[dict], today: date) -> str:
    header = (
        f"🚨 <b>INTRADAY VETO</b> — {today.isoformat()}\n"
        f"Observed temperature is already above the bracket. "
        f"These positions will not resolve in your favor:\n"
    )
    lines = []
    for b in busts:
        lines.append(
            f"\n<b>{b['city']}</b>  ({b['bracket']})\n"
            f"  Observed now: {b['observed']:.1f}°F\n"
            f"  Morning forecast: {b['forecast']}°F\n"
            f"  Bought at: {b['market_price_cents']:.1f}¢"
        )
    return header + "".join(lines)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    check_intraday_veto()
