"""Polymarket temperature market discovery + price lookup.

Polymarket structures these as **events** (one per city-day) containing
**markets** (one per bracket). The right endpoint for discovery is
`/public-search` — `/markets?search=` silently ignores its query string.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

import requests

log = logging.getLogger(__name__)

_GAMMA = "https://gamma-api.polymarket.com"
_TIMEOUT = 20

# Hyphen, hyphen-bullet, figure dash, en, em, horizontal bar, minus
_DASH_CHARS = r"\-‐-―−"

# International cities use Celsius single-value brackets ("be 19°C on…") with
# 1°C wide implicit bins; US uses 1°F wide explicit ranges. Parser captures
# both the number(s) AND the unit so we can convert °C → °F before returning.
_UNIT = r"(?P<unit>[FCfc])"

# "62-63°F", "62–63°F", "62 to 63°F", "between 62 and 63°F"
_RANGE_RE = re.compile(
    rf"(?P<lo>-?\d{{1,3}}(?:\.\d+)?)\s*(?:°?[FC])?\s*"
    rf"(?:[{_DASH_CHARS}]+|to|and)\s*"
    rf"(?P<hi>-?\d{{1,3}}(?:\.\d+)?)\s*°?\s*{_UNIT}?",
    re.IGNORECASE,
)
_ABOVE_PREFIX_RE = re.compile(
    rf"(?:above|over|≥|>=|>\s*=)\s*(?P<v>-?\d{{1,3}}(?:\.\d+)?)\s*°?\s*{_UNIT}?",
    re.IGNORECASE,
)
_ABOVE_SUFFIX_RE = re.compile(
    rf"(?P<v>-?\d{{1,3}}(?:\.\d+)?)\s*°?\s*{_UNIT}?\s*or\s+(?:higher|above|more)",
    re.IGNORECASE,
)
_BELOW_PREFIX_RE = re.compile(
    rf"(?:below|under|≤|<=|<\s*=)\s*(?P<v>-?\d{{1,3}}(?:\.\d+)?)\s*°?\s*{_UNIT}?",
    re.IGNORECASE,
)
_BELOW_SUFFIX_RE = re.compile(
    rf"(?P<v>-?\d{{1,3}}(?:\.\d+)?)\s*°?\s*{_UNIT}?\s*or\s+(?:below|lower|less)",
    re.IGNORECASE,
)
# Explicit "exactly 90°F" form — distinct from the implicit single-value below
_EXACT_RE = re.compile(
    rf"(?:exactly|equal to)\s*(?P<v>-?\d{{1,3}}(?:\.\d+)?)\s*°?\s*{_UNIT}?",
    re.IGNORECASE,
)
# Implicit single-value bracket — international format: "be 19°C on June 8"
_BE_SINGLE_RE = re.compile(
    rf"\bbe\s+(?P<v>-?\d{{1,3}}(?:\.\d+)?)\s*°?\s*{_UNIT}\s+on\b",
    re.IGNORECASE,
)


def _c_to_f(value: float) -> float:
    return value * 9 / 5 + 32


def _normalize_unit(unit: Optional[str], default_unit: str = "F") -> str:
    """Lower-case the unit; default to F if missing (US convention)."""
    if not unit:
        return default_unit.upper()
    return unit.upper()


@dataclass
class Market:
    city: str
    question: str
    bracket_low: float
    bracket_high: float
    resolution_date: Optional[date]
    token_id: str
    slug: str
    price: Optional[float] = None
    description: str = ""
    resolution_source: str = ""
    end_time: Optional[datetime] = None  # nominal resolution timestamp (NOT trading close)
    accepting_orders: Optional[bool] = None  # true source of "is the book open right now?"
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    volume: Optional[float] = None
    market_id: Optional[str] = None  # for the bestAsk re-fetch right before fire

    @property
    def url(self) -> str:
        return f"https://polymarket.com/event/{self.slug}"

    @property
    def station_hint(self) -> str:
        """Cheap extract of the station name from the description text."""
        if not self.description:
            return ""
        # e.g. "...recorded at the Los Angeles International Airport Station..."
        m = re.search(r"recorded at the ([^.]+?Station)", self.description)
        if m:
            return m.group(1).strip()
        return ""


@dataclass
class EventInfo:
    """Event-level metadata for resolution-criteria inspection."""
    city: str
    title: str
    description: str
    resolution_source: str
    end_time: Optional[datetime]
    station_hint: str
    market_count: int
    volume: Optional[float]


def parse_bracket(text: str) -> Optional[tuple[float, float]]:
    """Parse a temperature bracket from a question/title string, returning the
    bracket in °F. Handles both US format (1°F ranges in F) and international
    format (1°C single-value bins in C — converted to °F here).
    """
    if not text:
        return None

    m = _RANGE_RE.search(text)
    if m:
        unit = _normalize_unit(m.group("unit"))
        a, b = float(m.group("lo")), float(m.group("hi"))
        if unit == "C":
            a, b = _c_to_f(a), _c_to_f(b)
        return (min(a, b), max(a, b))

    for rx in (_ABOVE_SUFFIX_RE, _ABOVE_PREFIX_RE):
        m = rx.search(text)
        if m:
            unit = _normalize_unit(m.group("unit"))
            v = float(m.group("v"))
            if unit == "C":
                v = _c_to_f(v)
            return (v, float("inf"))

    for rx in (_BELOW_SUFFIX_RE, _BELOW_PREFIX_RE):
        m = rx.search(text)
        if m:
            unit = _normalize_unit(m.group("unit"))
            v = float(m.group("v"))
            if unit == "C":
                v = _c_to_f(v)
            return (float("-inf"), v)

    m = _EXACT_RE.search(text)
    if m:
        unit = _normalize_unit(m.group("unit"))
        v = float(m.group("v"))
        if unit == "C":
            return (_c_to_f(v), _c_to_f(v + 1))
        return (v, v + 1)

    # Implicit single-value bracket — "be 19°C on June 8" form. International
    # markets use this for every bin. Bracket width is 1 unit (whole-degree
    # resolution on the resolution source); convert to °F if needed.
    m = _BE_SINGLE_RE.search(text)
    if m:
        unit = _normalize_unit(m.group("unit"))
        v = float(m.group("v"))
        if unit == "C":
            return (_c_to_f(v), _c_to_f(v + 1))
        return (v, v + 1)

    return None


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.split("T")[0])
    except (ValueError, AttributeError):
        return None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _safe_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _station_from_description(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"recorded at the ([^.]+?Station)", text)
    return m.group(1).strip() if m else ""


def _maybe_json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def _extract_yes_index(market: dict) -> int:
    outcomes = _maybe_json_list(market.get("outcomes"))
    if outcomes:
        for i, o in enumerate(outcomes):
            if isinstance(o, str) and o.strip().lower() == "yes":
                return i
    return 0  # Polymarket convention: YES is index 0


def _extract_yes_token_id(market: dict, yes_idx: int) -> Optional[str]:
    ids = _maybe_json_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
    if not ids or yes_idx >= len(ids):
        return None
    return str(ids[yes_idx])


def _extract_yes_price(market: dict, yes_idx: int) -> Optional[float]:
    """Use bestAsk (what you'd pay to buy YES) → fall back to outcomePrices → lastTrade."""
    best_ask = market.get("bestAsk")
    if yes_idx == 0 and best_ask is not None:
        try:
            return float(best_ask)
        except (TypeError, ValueError):
            pass

    prices = _maybe_json_list(market.get("outcomePrices"))
    if prices and yes_idx < len(prices):
        try:
            return float(prices[yes_idx])
        except (TypeError, ValueError):
            pass

    last = market.get("lastTradePrice")
    if last is not None:
        try:
            return float(last)
        except (TypeError, ValueError):
            pass
    return None


def _city_aliases(city: str) -> list[str]:
    """Polymarket uses 'NYC' but NWS uses 'New York'. Map known aliases."""
    aliases = {
        "New York": ["new york", "nyc"],
        "Los Angeles": ["los angeles", "la"],
        "San Francisco": ["san francisco", "sf"],
    }
    return aliases.get(city, [city.lower()])


def _search_events(city: str) -> list[dict]:
    resp = requests.get(
        f"{_GAMMA}/public-search",
        params={"q": f"highest temperature {city}", "limit": 100},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("events", [])


def find_city_markets(city: str, target_date: Optional[date] = None) -> list[Market]:
    """Return parsed Polymarket temperature brackets for `city` on `target_date`.

    If `target_date` is None, today's event is selected automatically.
    Prices come back inline from the search response — no extra CLOB hit needed.
    """
    target = target_date or date.today()
    events = _search_events(city)
    aliases = _city_aliases(city)

    chosen_event: Optional[dict] = None
    for e in events:
        title = (e.get("title") or "").lower()
        if "highest temperature" not in title:
            continue
        if not any(a in title for a in aliases):
            continue
        end_date = _parse_iso_date(e.get("endDate"))
        if end_date == target:
            chosen_event = e
            break

    if not chosen_event:
        return []

    out: list[Market] = []
    event_slug = chosen_event.get("slug") or ""
    res_date = _parse_iso_date(chosen_event.get("endDate"))
    event_end = _parse_iso_datetime(chosen_event.get("endDate"))
    event_desc = chosen_event.get("description") or ""
    event_src = chosen_event.get("resolutionSource") or ""

    for m in chosen_event.get("markets", []):
        question = m.get("question") or ""
        bracket = parse_bracket(question)
        if bracket is None:
            continue
        yes_idx = _extract_yes_index(m)
        token_id = _extract_yes_token_id(m, yes_idx)
        if not token_id:
            continue
        price = _extract_yes_price(m, yes_idx)
        # Markets may carry their own description/source; fall back to event's
        m_desc = m.get("description") or event_desc
        m_src = m.get("resolutionSource") or event_src
        m_end = _parse_iso_datetime(m.get("endDate")) or event_end
        accepting = m.get("acceptingOrders")
        if not isinstance(accepting, bool):
            accepting = None
        best_bid = _safe_float(m.get("bestBid"))
        best_ask = _safe_float(m.get("bestAsk"))
        volume = _safe_float(m.get("volume"))
        mid = m.get("id")
        market_id = str(mid) if mid is not None else None
        out.append(
            Market(
                city=city,
                question=question,
                bracket_low=bracket[0],
                bracket_high=bracket[1],
                resolution_date=res_date,
                token_id=token_id,
                slug=event_slug,
                price=price,
                description=m_desc,
                resolution_source=m_src,
                end_time=m_end,
                accepting_orders=accepting,
                best_bid=best_bid,
                best_ask=best_ask,
                volume=volume,
                market_id=market_id,
            )
        )
    return out


def inspect_event(city: str, target_date: Optional[date] = None) -> Optional[EventInfo]:
    """Return event-level metadata so you can spot resolution-criteria mismatches."""
    target = target_date or date.today()
    events = _search_events(city)
    aliases = _city_aliases(city)
    for e in events:
        title = (e.get("title") or "").lower()
        if "highest temperature" not in title:
            continue
        if not any(a in title for a in aliases):
            continue
        if _parse_iso_date(e.get("endDate")) != target:
            continue
        desc = e.get("description") or ""
        return EventInfo(
            city=city,
            title=e.get("title") or "",
            description=desc,
            resolution_source=e.get("resolutionSource") or "",
            end_time=_parse_iso_datetime(e.get("endDate")),
            station_hint=_station_from_description(desc),
            market_count=len(e.get("markets") or []),
            volume=e.get("volume"),
        )
    return None


def fetch_prices(markets: Iterable[Market]) -> list[Market]:
    """No-op now — prices are already inline from find_city_markets. Kept for API compat."""
    return list(markets)


def refresh_market_quote(market: Market) -> Market:
    """Re-pull bestBid/bestAsk/volume for this single market right before we
    fire a signal. Stale prices from the bulk search can drift several cents
    over the seconds between scan and Telegram, so re-confirm the entry.

    Mutates and returns the same Market instance. Silently no-ops on error so
    a transient network blip never blocks an otherwise valid signal — caller
    sees the pre-existing best_ask/best_bid.
    """
    if not market.market_id:
        return market
    try:
        resp = requests.get(
            f"{_GAMMA}/markets/{market.market_id}",
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("bestAsk re-fetch failed for %s: %s", market.market_id, e)
        return market

    new_ask = _safe_float(data.get("bestAsk"))
    new_bid = _safe_float(data.get("bestBid"))
    new_vol = _safe_float(data.get("volume"))
    if new_ask is not None:
        market.best_ask = new_ask
        market.price = new_ask  # price tracks what you'd actually pay to buy YES
    if new_bid is not None:
        market.best_bid = new_bid
    if new_vol is not None:
        market.volume = new_vol
    return market


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from config import CITIES

    for c in CITIES:
        try:
            markets = find_city_markets(c["name"])
        except Exception as e:
            print(f"\n=== {c['name']} ERROR: {e}")
            continue
        print(f"\n=== {c['name']} ({len(markets)} brackets) ===")
        for m in markets:
            lo = "-inf" if m.bracket_low == float("-inf") else f"{m.bracket_low:.0f}"
            hi = "+inf" if m.bracket_high == float("inf") else f"{m.bracket_high:.0f}"
            price = f"{m.price:.3f}" if m.price is not None else "n/a"
            print(f"  [{lo}-{hi}F] {price}  {m.question[:75]}")
