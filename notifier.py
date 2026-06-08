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


def _city_date_block(city: str, target: date, items: list[Signal]) -> str:
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
        f"<b>{city}, {target.isoformat()}</b>\n"
        f"{forecast_line}"
    )
    station = head.market.station_hint
    if station:
        header += f"\nResolves at: {station}"
    body = "\n\n".join(_format_signal_line(s) for s in items)
    return f"{header}\n\n{body}"


def _group_by_city_date(signals: list[Signal]) -> "dict[tuple[str, date], list[Signal]]":
    grouped: dict[tuple[str, date], list[Signal]] = defaultdict(list)
    for s in signals:
        key = (s.market.city, s.market.resolution_date or date.today())
        grouped[key].append(s)
    # Sort by date asc, then city alpha — keeps multi-day output readable
    return dict(sorted(grouped.items(), key=lambda kv: (kv[0][1], kv[0][0])))


def build_signals_message(signals: list[Signal], today: date) -> str:
    traded = [s for s in signals if not s.vetoed]
    vetoed = [s for s in signals if s.vetoed]

    sections: list[str] = []

    if traded:
        traded_blocks = [
            _city_date_block(c, d, items)
            for (c, d), items in _group_by_city_date(traded).items()
        ]
        sections.append(
            "⚡ <b>TRADE THESE</b> (passed Open-Meteo veto)\n\n"
            + "\n\n———\n\n".join(traded_blocks)
        )
    else:
        sections.append("⚡ <b>TRADE THESE</b>\n\n(none — all signals vetoed or no signals fired)")

    if vetoed:
        vetoed_blocks = [
            _city_date_block(c, d, items)
            for (c, d), items in _group_by_city_date(vetoed).items()
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


# Telegram hard limit is 4096 chars per message. We chunk well under that so
# HTML tags can never get split mid-tag — split happens on block separators.
_TELEGRAM_CHUNK_LIMIT = 3800


def _chunk_message(text: str, limit: int = _TELEGRAM_CHUNK_LIMIT) -> list[str]:
    """Split a long HTML message into chunks ≤ limit chars.

    Splits on block separators (city dividers, then double newlines, then
    single newlines) to avoid cutting HTML tags. Worst case falls back to
    hard-slice on character boundaries to never exceed limit.
    """
    if len(text) <= limit:
        return [text]

    separators = ["\n\n═══════════════\n\n", "\n\n———\n\n", "\n\n", "\n"]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = -1
        for sep in separators:
            idx = remaining.rfind(sep, 0, limit)
            if idx > 0:
                cut = idx + len(sep)
                break
        if cut <= 0:
            # No separator found in window; hard slice.
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_signals(signals: list[Signal], today: date, cities_checked: int) -> bool:
    """Build the daily-signals message and send it. Splits into multiple
    Telegram messages when the combined size exceeds 3800 chars (Telegram's
    4096-char hard limit). Returns True only if every chunk delivered."""
    if not signals:
        return send(build_empty_message(today, cities_checked))

    full = build_signals_message(signals, today)
    chunks = _chunk_message(full)
    if len(chunks) > 1:
        log.info("signals message split into %d Telegram chunks (%d chars total)",
                 len(chunks), len(full))
    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        header = f"<i>(part {i}/{len(chunks)})</i>\n\n" if len(chunks) > 1 else ""
        if not send(header + chunk):
            all_ok = False
    return all_ok
