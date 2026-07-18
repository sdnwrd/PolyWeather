"""Scheduler + entry point for the weather signal bot."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import schedule

import bias
import calibration
import config
from config import CITIES, D0_CUTOFF_LOCAL_HOUR, MAX_MARKET_PRICE, RUN_TIME, VETO_SPREAD_THRESHOLD
from forecast import (
    get_daily_high,
    get_day_max_temp,
    get_openmeteo_high,
    get_primary_forecast,
    get_veto_forecast,
)
import journal
from markets import fetch_prices, find_city_markets, inspect_event, refresh_market_quote
from notifier import send_signals
from signals import Signal, evaluate_markets, is_bracket_busted, sigma_for_horizon
import snapshots

log = logging.getLogger("weather-signal-bot")


SCAN_HORIZON_DAYS = 4  # scan today + next 3 days; market may not exist for D+3 yet


def apply_bias(city_name: str, days_ahead: int, raw_forecast: float) -> tuple[float, float]:
    """Return (corrected_forecast, correction). The correction is the estimated
    per-(city, horizon) forecast bias to subtract; 0.0 when no trustworthy
    estimate exists (see bias.get_bias).

    Disabled via config.BIAS_CORRECTION_ENABLED (2026-07-18): live data showed
    the correction fights the tail we bet, so we trade the RAW forecast."""
    if not config.BIAS_CORRECTION_ENABLED:
        return raw_forecast, 0.0
    correction = bias.get_bias(city_name, days_ahead)
    return raw_forecast - correction, correction


def _is_d0_too_late(city: dict, target: date) -> bool:
    """True if the city's local-time day relative to `target` is past
    actionable — the day's high is essentially locked and a forecast-based
    signal is just trading reality the market already knows. Two cases:
      - local date is already past target (local day rolled over)
      - local date == target AND local hour ≥ D0_CUTOFF_LOCAL_HOUR
    """
    tz_name = city.get("tz")
    if not tz_name:
        return False
    try:
        local_now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        return False
    local_date = local_now.date()
    if local_date > target:
        return True
    if local_date == target and local_now.hour >= D0_CUTOFF_LOCAL_HOUR:
        return True
    return False


def _scan_city_date(city: dict, target: date) -> list[Signal]:
    """Scan one (city, target_date) combo and return any qualifying signals."""
    name = city["name"]
    if _is_d0_too_late(city, target):
        log.info("%s %s: skipping D+0 — past local cutoff (%d:00)",
                 name, target, D0_CUTOFF_LOCAL_HOUR)
        return []
    forecast = get_primary_forecast(city, target)
    if forecast is None:
        log.warning("primary forecast unavailable for %s on %s", name, target)
        return []

    veto_forecast = get_veto_forecast(city, target)
    primary_label = "NDFD" if city.get("region") == "us" else "OM-best"
    veto_label = "OM-best" if city.get("region") == "us" else "OM-GFS"
    if veto_forecast is None:
        spread = None
        log.info("%s %s %s=%.1f°F, %s=n/a", name, target, primary_label, forecast, veto_label)
    else:
        spread = abs(forecast - veto_forecast)
        log.info(
            "%s %s %s=%.1f°F, %s=%.1f°F (spread %.1f°F)",
            name, target, primary_label, forecast, veto_label, veto_forecast, spread,
        )

    try:
        markets = find_city_markets(name, target)
    except Exception as e:
        log.warning("market discovery failed for %s %s: %s", name, target, e)
        return []
    if not markets:
        # Common at D+3 — Polymarket may not have created the event yet
        return []

    try:
        markets = fetch_prices(markets)
    except Exception as e:
        log.warning("price fetch failed for %s %s: %s", name, target, e)
        return []

    closed = [m for m in markets if m.accepting_orders is False]
    if closed:
        log.warning(
            "%s %s: %d/%d markets not accepting orders right now",
            name, target, len(closed), len(markets),
        )

    days_ahead = max(0, (target - date.today()).days)
    sigma_base = calibration.get_sigma(name)
    sigma = sigma_for_horizon(sigma_base, days_ahead)
    if days_ahead > 0:
        log.info(
            "%s %s: sigma=%.2f°F (base %.2f + horizon D+%d)",
            name, target, sigma, sigma_base, days_ahead,
        )
    # Bias-correct the forecast point before scoring brackets. `forecast` stays
    # the RAW primary value (used for the veto spread + recorded raw in the
    # journal); `eval_forecast` is what we actually bet on.
    eval_forecast, correction = apply_bias(name, days_ahead, forecast)
    if correction:
        log.info("%s %s: bias correction %+.1f°F (raw %.1f → %.1f)",
                 name, target, -correction, forecast, eval_forecast)
    candidates = evaluate_markets(markets, eval_forecast, sigma=sigma)
    refreshed_markets = [refresh_market_quote(c.market) for c in candidates]
    signals = (
        evaluate_markets(refreshed_markets, eval_forecast, sigma=sigma)
        if refreshed_markets else []
    )

    # D+0 reality check: pull current METAR once for the city and bust any
    # signal whose bracket is already exceeded by the observation. This is
    # the morning-scan version of the intraday cron — running it here means
    # the user never sees a signal that reality has already invalidated.
    observed = None
    if days_ahead == 0 and signals:
        # Day's MAX so far, not latest. A city past its peak (London at
        # 16:20 BST: latest 14°C, day's high 16°C) would falsely pass a
        # latest-reading check while having already busted the bracket.
        observed = get_day_max_temp(city)

    for s in signals:
        s.forecast_high_raw = forecast  # raw primary, pre-correction
        s.forecast_openmeteo = veto_forecast
        s.model_spread = spread
        s.vetoed = spread is not None and spread >= VETO_SPREAD_THRESHOLD
        s.metar_observed = observed
        if observed is not None:
            s.bracket_busted = is_bracket_busted(
                observed, s.market.bracket_low, s.market.bracket_high,
                city.get("region", "us"),
            )
    dropped = len(candidates) - len(signals)
    vetoed_count = sum(1 for s in signals if s.vetoed)
    busted_count = sum(1 for s in signals if s.bracket_busted)
    log.info(
        "%s %s: %d markets, %d candidates, %d signals "
        "(%d dropped on re-quote, %d vetoed, %d busted by METAR)",
        name, target, len(markets), len(candidates), len(signals),
        dropped, vetoed_count, busted_count,
    )
    snapshots.record_snapshot(
        city=city,
        target=target,
        scan_type="morning",
        primary_forecast=forecast,
        veto_forecast=veto_forecast,
        markets=markets,
        signals=signals,
    )
    return signals


def _scan() -> list[Signal]:
    today = date.today()
    horizons = [today + timedelta(days=i) for i in range(SCAN_HORIZON_DAYS)]
    all_signals: list[Signal] = []

    for city in CITIES:
        for target in horizons:
            try:
                all_signals.extend(_scan_city_date(city, target))
            except Exception as e:
                log.exception("scan of %s %s crashed: %s", city["name"], target, e)

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
    # and veto backtesting only. METAR-busted signals are excluded — they're
    # definitive losses we don't want polluting WIN/LOSS stats.
    journal_signals = [s for s in signals if not s.bracket_busted]
    try:
        new_rows = journal.log_signals(journal_signals, today)
        log.info("journal: %d new signals appended (excluded %d busted)",
                 new_rows, len(signals) - len(journal_signals))
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

    # Recompute per-city sigma now that fresh outcomes are in the journal.
    # Stays dormant per city until CALIBRATION_MIN (30) resolved samples.
    try:
        calibration.run_calibration()
    except Exception as e:
        log.exception("calibration crashed: %s", e)

    # Recompute per-(city, horizon) forecast bias from snapshots vs station
    # truth. Stays a no-op per bin until it clears the sample + dead-band gate.
    try:
        bias.build_bias_table()
    except Exception as e:
        log.exception("bias table build crashed: %s", e)

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
