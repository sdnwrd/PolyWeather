"""Telegram delivery of signals + daily summaries."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date
from typing import Iterable

import requests

from config import CITIES, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from signals import Signal


def _source_labels(city_name: str) -> tuple[str, str]:
    """Return (primary_label, veto_label) for the given city — depends on
    which forecast routing main.py used for it."""
    for c in CITIES:
        if c["name"] == city_name:
            if c.get("region") == "us":
                return "NDFD", "Open-Meteo"
            return "OM-best", "OM-GFS"
    return "primary", "veto"

log = logging.getLogger(__name__)

_TELEGRAM_BASE = "https://api.telegram.org"
_TIMEOUT = 15


def _fmt_temp(value: float) -> str:
    if value == float("inf"):
        return "∞"
    if value == float("-inf"):
        return "−∞"
    return f"{value:.0f}"


def _format_signal_line(s: Signal) -> str:
    lo, hi = _fmt_temp(s.market.bracket_low), _fmt_temp(s.market.bracket_high)
    price_cents = s.market_price * 100
    implied = s.implied_prob * 100
    lines = [
        f"  Bracket: {lo}–{hi}°F",
        f"  Market price: {price_cents:.1f}¢ ({implied:.1f}% implied)",
        f"  Est. true prob: {s.true_prob:.0%}",
        f"  Expected value: {s.ev:.1f}x",
    ]
    if s.market.end_time:
        lines.append(f"  Trading closes: {s.market.end_time.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"  <a href=\"{s.market.url}\">Open market on Polymarket</a>")
    return "\n".join(lines)


def _city_block(city: str, items: list[Signal], today: date) -> str:
    head = items[0]
    primary_label, veto_label = _source_labels(city)
    if head.forecast_openmeteo is None:
        forecast_line = f"{primary_label}: {head.forecast_high:.0f}°F ({veto_label}: n/a)"
    else:
        spread = head.model_spread or 0.0
        forecast_line = (
            f"{primary_label}: {head.forecast_high:.0f}°F  |  "
            f"{veto_label}: {head.forecast_openmeteo:.0f}°F  |  "
            f"spread: {spread:.1f}°F"
        )
    header = (
        f"<b>{city}, {today.isoformat()}</b>\n"
        f"{forecast_line}"
    )
    station = head.market.station_hint
    if station:
        header += f"\nResolves at: {station}"
    body = "\n\n".join(_format_signal_line(s) for s in items)
    return f"{header}\n\n{body}"


def _group_by_city(signals: list[Signal]) -> dict[str, list[Signal]]:
    grouped: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        grouped[s.market.city].append(s)
    return grouped


def build_signals_message(signals: list[Signal], today: date) -> str:
    traded = [s for s in signals if not s.vetoed]
    vetoed = [s for s in signals if s.vetoed]

    sections: list[str] = []

    if traded:
        traded_blocks = [
            _city_block(c, items, today)
            for c, items in _group_by_city(traded).items()
        ]
        sections.append(
            "⚡ <b>TRADE THESE</b> (passed Open-Meteo veto)\n\n"
            + "\n\n———\n\n".join(traded_blocks)
        )
    else:
        sections.append("⚡ <b>TRADE THESE</b>\n\n(none — all signals vetoed or no signals fired)")

    if vetoed:
        vetoed_blocks = [
            _city_block(c, items, today)
            for c, items in _group_by_city(vetoed).items()
        ]
        sections.append(
            "🚫 <b>VETOED</b> (would have fired without Open-Meteo veto)\n\n"
            + "\n\n———\n\n".join(vetoed_blocks)
        )

    return "\n\n═══════════════\n\n".join(sections)


def build_empty_message(today: date, cities_checked: int) -> str:
    return (
        f"📊 Daily scan complete — {today.isoformat()}\n"
        f"Cities checked: {cities_checked}\n"
        f"Signals found: 0"
    )


def _post(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram token or chat_id missing; cannot send")
        return False
    url = f"{_TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=_TIMEOUT)
    # Telegram returns 200 with {ok: false} for some logical errors; surface
    # the body either way so the cron logs show exactly what happened.
    chat_tail = str(TELEGRAM_CHAT_ID)[-4:]
    if not resp.ok:
        log.error("Telegram HTTP %s (chat …%s): %s", resp.status_code, chat_tail, resp.text)
        resp.raise_for_status()
    body = resp.json() if resp.content else {}
    if not body.get("ok", False):
        log.error("Telegram returned ok=false (chat …%s): %s", chat_tail, body)
        return False
    log.info("Telegram delivered (chat …%s, message_id=%s)", chat_tail, body.get("result", {}).get("message_id"))
    return True


def send(text: str) -> bool:
    """Send `text`, retrying once after 10s on failure."""
    try:
        return _post(text)
    except requests.RequestException as e:
        log.warning("Telegram send failed: %s — retrying in 10s", e)
        time.sleep(10)
        try:
            return _post(text)
        except requests.RequestException as e2:
            log.error("Telegram send failed on retry: %s", e2)
            return False


def send_signals(signals: list[Signal], today: date, cities_checked: int) -> bool:
    if signals:
        return send(build_signals_message(signals, today))
    return send(build_empty_message(today, cities_checked))
