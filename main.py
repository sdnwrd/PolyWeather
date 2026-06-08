"""Scheduler + entry point for the weather signal bot."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timezone

import schedule

from config import CITIES, MAX_MARKET_PRICE, RUN_TIME, VETO_SPREAD_THRESHOLD
from forecast import get_daily_high, get_openmeteo_high
import journal
from markets import fetch_prices, find_city_markets, inspect_event, refresh_market_quote
from notifier import send_signals
from signals import Signal, evaluate_markets

log = logging.getLogger("weather-signal-bot")


def _scan() -> list[Signal]:
    today = date.today()
    all_signals: list[Signal] = []

    for city in CITIES:
        name = city["name"]
        try:
            forecast = get_daily_high(city["lat"], city["lon"], today)
        except Exception as e:
            log.warning("forecast fetch failed for %s: %s", name, e)
            continue

        om_forecast = get_openmeteo_high(city["lat"], city["lon"], today)
        if om_forecast is None:
            spread = None
            log.info("%s NDFD=%.1f°F, Open-Meteo=n/a", name, forecast)
        else:
            spread = abs(forecast - om_forecast)
            log.info(
                "%s NDFD=%.1f°F, Open-Meteo=%.1f°F (spread %.1f°F)",
                name, forecast, om_forecast, spread,
            )

        try:
            markets = find_city_markets(name, today)
        except Exception as e:
            log.warning("market discovery failed for %s: %s", name, e)
            continue
        if not markets:
            log.info("no markets found for %s", name)
            continue

        try:
            markets = fetch_prices(markets)
        except Exception as e:
            log.warning("price fetch failed for %s: %s", name, e)
            continue

        closed = [m for m in markets if m.accepting_orders is False]
        if closed:
            log.warning(
                "%s: %d/%d markets not accepting orders right now",
                name, len(closed), len(markets),
            )

        # First pass: cheap pre-filter using bulk-search prices. Anything that
        # qualifies gets a fresh bestAsk re-pull, then is re-evaluated — this
        # catches the seconds of price drift between scan and signal fire.
        candidates = evaluate_markets(markets, forecast)
        refreshed_markets = []
        for cand in candidates:
            refreshed_markets.append(refresh_market_quote(cand.market))
        signals = evaluate_markets(refreshed_markets, forecast) if refreshed_markets else []
        # Attach multi-model context to every fired signal so the Telegram
        # message + journal log can show both forecasts and the veto state.
        for s in signals:
            s.forecast_openmeteo = om_forecast
            s.model_spread = spread
            s.vetoed = spread is not None and spread >= VETO_SPREAD_THRESHOLD
        dropped = len(candidates) - len(signals)
        vetoed_count = sum(1 for s in signals if s.vetoed)
        log.info(
            "%s: %d markets, %d candidates, %d signals (%d dropped on re-quote, %d vetoed)",
            name, len(markets), len(candidates), len(signals), dropped, vetoed_count,
        )
        all_signals.extend(signals)

    return all_signals


def inspect() -> None:
    """Print resolution criteria for every city's current event."""
    today = date.today()
    print(f"=== Resolution-criteria inspection ({today.isoformat()}) ===\n")
    for city in CITIES:
        name = city["name"]
        try:
            info = inspect_event(name, today)
        except Exception as e:
            print(f"[{name}] ERROR: {e}\n")
            continue
        if not info:
            print(f"[{name}] no matching event today\n")
            continue
        try:
            forecast = get_daily_high(city["lat"], city["lon"], today)
            forecast_str = f"{forecast:.0f}°F"
        except Exception as e:
            forecast_str = f"forecast-error: {e}"

        end_str = info.end_time.strftime("%Y-%m-%d %H:%M UTC") if info.end_time else "?"
        now = datetime.now(timezone.utc)
        trading_state = (
            "OPEN" if info.end_time and info.end_time > now
            else "CLOSED" if info.end_time
            else "?"
        )

        print(f"[{name}]")
        print(f"  Event:           {info.title}")
        print(f"  Brackets:        {info.market_count}")
        print(f"  Volume:          {info.volume}")
        print(f"  Trading closes:  {end_str} ({trading_state})")
        print(f"  Station hint:    {info.station_hint or '(none extracted — read description)'}")
        print(f"  Resolution src:  {info.resolution_source or '(none)'}")
        print(f"  NWS forecast at config lat/lon: {forecast_str}")
        desc = info.description.replace("\n", " ").strip()
        if len(desc) > 400:
            desc = desc[:400] + "..."
        print(f"  Description:     {desc}")
        print()


def run() -> None:
    today = date.today()
    log.info("=== daily scan starting (%s) ===", today.isoformat())
    try:
        signals = _scan()
    except Exception as e:
        log.exception("scan crashed: %s", e)
        return

    # Journal log + backfill yesterday's pending rows on the same run. No
    # paper-portfolio P&L — user trades live; this is for sigma calibration
    # and veto backtesting only.
    try:
        new_rows = journal.log_signals(signals, today)
        log.info("journal: %d new signals appended", new_rows)
    except Exception as e:
        log.exception("journal logging failed: %s", e)
    try:
        counts = journal.backfill()
        log.info(
            "backfill: filled=%d, still_pending=%d, errors=%d",
            counts["filled"], counts["still_pending"], counts["errors"],
        )
        log.info("status — %s", journal.short_status())
    except Exception as e:
        log.exception("backfill failed: %s", e)

    try:
        send_signals(signals, today, len(CITIES))
    except Exception as e:
        log.exception("notifier crashed: %s", e)
    log.info("=== daily scan complete: %d signals ===", len(signals))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Weather prediction-market signal bot")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Run a single scan immediately and exit (for testing).",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Print resolution criteria (station, source URL, close time, "
             "NWS forecast at your lat/lon) for every city's current event.",
    )
    args = parser.parse_args()

    _setup_logging()

    if args.inspect:
        inspect()
        return

    if args.now:
        run()
        return

    schedule.every().day.at(RUN_TIME).do(run)
    log.info("Scheduler armed for daily run at %s local time", RUN_TIME)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
