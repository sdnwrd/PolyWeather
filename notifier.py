"""Telegram delivery of signals + daily summaries."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date
from typing import Iterable

import requests

from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from signals import Signal

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


def build_signals_message(signals: list[Signal], today: date) -> str:
    by_city: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        by_city[s.market.city].append(s)

    blocks: list[str] = []
    for city, items in by_city.items():
        forecast = items[0].forecast_high
        header = (
            f"⚡ <b>Signal — {city}, {today.isoformat()}</b>\n"
            f"NWS forecast high: {forecast:.0f}°F"
        )
        station = items[0].market.station_hint
        if station:
            header += f"\nResolves at: {station}"
        body = "\n\n".join(_format_signal_line(s) for s in items)
        footer = (
            "\n\n<i>⚠️ Verify the resolution source matches your forecast — "
            "the market may resolve on a specific station/window that differs "
            "from the lat/lon you forecast.</i>"
        )
        blocks.append(f"{header}\n\n{body}{footer}")

    return "\n\n———\n\n".join(blocks)


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
    resp.raise_for_status()
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
