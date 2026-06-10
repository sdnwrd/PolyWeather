"""One-time, throwaway analysis. NOT part of the bot.

Question it answers: is RainSignal's FORECAST_SIGMA = 2.0 (the D+0 base sigma
that drives every true_prob / EV >= 8x signal) honest, or is it overconfident?

Method (no money, no trade history, uses clean external data only):
  - FORECAST = Open-Meteo Historical Forecast API (archived short-lead forecast
    of daily max). This is the same model family RainSignal uses as the intl
    primary / US veto source.
  - TRUTH    = Open-Meteo ERA5 archive (reanalysis daily max) = ground truth.
  - error    = forecast - truth, per city per day.

We report per-city and pooled: bias (mean error), MAE, sigma (std of error),
and tail frequencies. Then we translate the real sigma into the only thing that
matters for the bot: the true probability mass that lands in a single
Polymarket bracket centered on the forecast -- i.e. the best-case win rate a
single-bracket bet could have even with a perfectly-centered, unbiased forecast.

CAVEATS (state these whenever citing the output):
  1. Truth = ERA5 grid reanalysis, NOT the METAR station Polymarket resolves on.
     Station-level error is usually a bit larger (siting, airport microclimate),
     so the real bot sigma is a FLOOR here, likely optimistic.
  2. Historical Forecast API returns short-lead (~D+0/D+1) forecasts. This
     validates the BASE sigma (2.0), not the horizon growth (0.7/day). D+1..D+3
     are strictly worse; this is the best case.
  3. US primary is NDFD (not tested here); Open-Meteo is the US veto + intl
     primary. Forecast skill is broadly comparable across modern models.
"""

from __future__ import annotations

import math
import statistics
import sys
import time
from datetime import date

import requests

sys.path.insert(0, ".")
from config import CITIES, FORECAST_SIGMA, MAX_MARKET_PRICE  # noqa: E402

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HISTFC_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# ERA5 has a ~5 day lag; pick a window that's safely settled and gives ~75 days.
START = "2026-03-15"
END = "2026-05-31"
BRACKET_WIDTH_F = 2.0  # typical Polymarket temperature bracket width


def _fetch_daily_max(url: str, lat: float, lon: float) -> dict[str, float]:
    params = {
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "start_date": START, "end_date": END,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    daily = resp.json().get("daily", {})
    times = daily.get("time", []) or []
    temps = daily.get("temperature_2m_max", []) or []
    return {t: v for t, v in zip(times, temps) if v is not None}


def _bracket_hit_prob(sigma: float, width: float = BRACKET_WIDTH_F) -> float:
    """P(|N(0, sigma)| < width/2) -- chance the actual lands in a width-wide
    bracket centered exactly on an unbiased forecast. Best-case single-bracket
    win rate."""
    if sigma <= 0:
        return 1.0
    z = (width / 2) / (sigma * math.sqrt(2))
    return math.erf(z)


def main() -> None:
    print(f"Backtest window: {START} .. {END}")
    print(f"Config FORECAST_SIGMA (D+0 base) = {FORECAST_SIGMA}°F\n")

    all_errors: list[float] = []
    rows: list[tuple] = []

    for city in CITIES:
        name, lat, lon = city["name"], city["lat"], city["lon"]
        try:
            truth = _fetch_daily_max(ARCHIVE_URL, lat, lon)
            time.sleep(0.4)
            fc = _fetch_daily_max(HISTFC_URL, lat, lon)
            time.sleep(0.4)
        except requests.RequestException as e:
            print(f"  {name:<14} FETCH ERROR: {e}")
            continue

        errs = [fc[d] - truth[d] for d in truth.keys() & fc.keys()]
        if len(errs) < 10:
            print(f"  {name:<14} too few paired days ({len(errs)}), skipping")
            continue

        sigma = statistics.pstdev(errs)
        if sigma < 0.1:
            print(f"  {name:<14} DEGENERATE (sigma={sigma:.2f}, forecast==archive) "
                  f"-- API artifact, excluded from pooled")
            continue

        all_errors.extend(errs)
        bias = statistics.fmean(errs)
        mae = statistics.fmean(abs(e) for e in errs)
        over2 = sum(1 for e in errs if abs(e) > 2) / len(errs)
        over3 = sum(1 for e in errs if abs(e) > 3) / len(errs)
        rows.append((name, len(errs), bias, mae, sigma, over2, over3))

    print(f"{'City':<14}{'N':>4}{'bias':>7}{'MAE':>7}{'sigma':>7}{'|e|>2':>7}{'|e|>3':>7}")
    print("-" * 52)
    for name, n, bias, mae, sigma, o2, o3 in rows:
        print(f"{name:<14}{n:>4}{bias:>+7.1f}{mae:>7.1f}{sigma:>7.1f}{o2:>6.0%}{o3:>6.0%}")

    if not all_errors:
        print("\nNo data. Aborting.")
        return

    pooled_bias = statistics.fmean(all_errors)
    pooled_mae = statistics.fmean(abs(e) for e in all_errors)
    pooled_sigma = statistics.pstdev(all_errors)
    mean_abs_bias = statistics.fmean(abs(r[2]) for r in rows)
    n = len(all_errors)

    print("-" * 52)
    print(f"{'POOLED':<14}{n:>4}{pooled_bias:>+7.1f}{pooled_mae:>7.1f}{pooled_sigma:>7.1f}"
          f"{sum(1 for e in all_errors if abs(e)>2)/n:>6.0%}"
          f"{sum(1 for e in all_errors if abs(e)>3)/n:>6.0%}")

    print("\n=== Interpretation ===")
    print(f"Config assumes sigma = {FORECAST_SIGMA}°F.  Real (pooled) sigma = {pooled_sigma:.1f}°F.")
    ratio = pooled_sigma / FORECAST_SIGMA
    print(f"Real sigma is {ratio:.2f}x the assumed value.")
    print(f"Mean |per-city bias| = {mean_abs_bias:.1f}°F (systematic miss, on ~2°F brackets).")

    p_cfg = _bracket_hit_prob(FORECAST_SIGMA)
    p_real = _bracket_hit_prob(pooled_sigma)
    print(f"\nBest-case single-bracket ({BRACKET_WIDTH_F:.0f}°F wide, perfectly centered) win rate:")
    print(f"  at assumed sigma {FORECAST_SIGMA}: {p_cfg:.0%}")
    print(f"  at real sigma {pooled_sigma:.1f}: {p_real:.0%}")
    print(f"\n  => true_prob the bot computes is inflated by ~{p_cfg/p_real:.2f}x vs reality"
          if p_real > 0 else "")
    print(f"\nBot's signal filter requires true_prob >= 0.05 AND price <= {MAX_MARKET_PRICE} "
          f"AND EV >= 8x.\nIf real true_prob is {p_cfg/p_real:.1f}x lower than the bot thinks, "
          f"the EV>=8x bar is being cleared on inflated math.")


if __name__ == "__main__":
    main()
