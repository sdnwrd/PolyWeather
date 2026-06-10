"""Falsification sim for the "bias is the killer" claim (fable5 review §8 Q5).

One-time, throwaway. Reuses the fetch + city loop from
backtest_forecast_skill.py. Question: if we remove each city's systematic
forecast bias, does the forecast actually land on the right Polymarket bracket
more often, and does the Gaussian true_prob the bot bets on get better
calibrated? If de-biasing moves neither, bias is NOT the lever and we stop.

What this can and cannot do:
  - We have NO historical Polymarket prices, so we cannot simulate literal PnL
    or the exact cheap-tail bracket the bot would have bet. We test the
    MECHANISM instead:
      (A) center-bracket hit rate: does actual land in the 2°F bracket the
          forecast points at? (the bot anchors every bet to this point)
      (B) probability calibration: mean log-loss of the bracket that actually
          occurred, under the bot's Gaussian(forecast, sigma=2.0).
  - Bias is estimated LEAVE-ONE-OUT per city (each day de-biased using the mean
    error of all OTHER days), so we are not grading in-sample.
  - Truth = ERA5 grid (same caveat as the backtest: not the METAR resolution
    station). This tests the mechanism in principle, not production magnitudes.

If de-biasing improves (A) and (B) materially, bias is a real lever and the
fix is worth building. If not, drop it.
"""

from __future__ import annotations

import math
import statistics
import sys

sys.path.insert(0, ".")
from config import CITIES, FORECAST_SIGMA  # noqa: E402
from scripts.backtest_forecast_skill import _fetch_daily_max, ARCHIVE_URL, HISTFC_URL  # noqa: E402
import time  # noqa: E402

SIGMA = FORECAST_SIGMA  # 2.0; bot's D+0 base


def bracket_of(temp: float) -> int:
    """2°F Polymarket bin index on the whole-degree readout. round->int, then
    pair {80,81}->40, {82,83}->41, ... matches the bot's inclusive 2°F brackets."""
    return int(round(temp)) // 2


def gaussian_bracket_prob(center: float, bin_idx: int, sigma: float) -> float:
    """P(actual rounds into 2°F bin `bin_idx`) under N(center, sigma).
    Bin covers [2*bin_idx - 0.5, 2*bin_idx + 1.5) on the continuous axis
    (whole-degree rounding of a 2-wide pair)."""
    lo = 2 * bin_idx - 0.5
    hi = 2 * bin_idx + 1.5
    z_hi = (hi - center) / (sigma * math.sqrt(2))
    z_lo = (lo - center) / (sigma * math.sqrt(2))
    return 0.5 * (math.erf(z_hi) - math.erf(z_lo))


def main() -> None:
    print(f"Bias-impact falsification sim (LOO de-bias, sigma={SIGMA}, truth=ERA5)\n")

    tot_raw_hit = tot_deb_hit = 0
    tot_raw_ll = tot_deb_ll = 0.0
    n_total = 0
    rows = []

    for city in CITIES:
        name = city["name"]
        try:
            truth = _fetch_daily_max(ARCHIVE_URL, city["lat"], city["lon"])
            time.sleep(0.4)
            fc = _fetch_daily_max(HISTFC_URL, city["lat"], city["lon"])
            time.sleep(0.4)
        except Exception as e:
            print(f"  {name:<14} fetch error: {e}")
            continue

        days = sorted(truth.keys() & fc.keys())
        errs = [fc[d] - truth[d] for d in days]
        if len(days) < 15 or statistics.pstdev(errs) < 0.1:
            continue  # too few or degenerate (Singapore/Shanghai artifact)

        sum_err = sum(errs)
        n = len(days)
        raw_hit = deb_hit = 0
        raw_ll = deb_ll = 0.0

        for d, e in zip(days, errs):
            actual = truth[d]
            raw_fc = fc[d]
            # leave-one-out city bias for this day
            loo_bias = (sum_err - e) / (n - 1)
            deb_fc = raw_fc - loo_bias

            actual_bin = bracket_of(actual)

            # (A) center-bracket hit
            raw_hit += (bracket_of(raw_fc) == actual_bin)
            deb_hit += (bracket_of(deb_fc) == actual_bin)

            # (B) log-loss of the bracket that actually occurred
            p_raw = max(gaussian_bracket_prob(raw_fc, actual_bin, SIGMA), 1e-6)
            p_deb = max(gaussian_bracket_prob(deb_fc, actual_bin, SIGMA), 1e-6)
            raw_ll += -math.log(p_raw)
            deb_ll += -math.log(p_deb)

        rows.append((name, n, raw_hit / n, deb_hit / n, raw_ll / n, deb_ll / n,
                     statistics.fmean(errs)))
        tot_raw_hit += raw_hit
        tot_deb_hit += deb_hit
        tot_raw_ll += raw_ll
        tot_deb_ll += deb_ll
        n_total += n

    print(f"{'City':<14}{'N':>4}{'bias':>7}{'hit_raw':>9}{'hit_deb':>9}{'d_hit':>7}"
          f"{'LL_raw':>8}{'LL_deb':>8}")
    print("-" * 66)
    for name, n, hr, hd, lr, ld, bias in rows:
        print(f"{name:<14}{n:>4}{bias:>+7.1f}{hr:>8.0%}{hd:>8.0%}{(hd-hr):>+7.0%}"
              f"{lr:>8.2f}{ld:>8.2f}")
    print("-" * 66)
    print(f"{'POOLED':<14}{n_total:>4}{'':>7}{tot_raw_hit/n_total:>8.0%}"
          f"{tot_deb_hit/n_total:>8.0%}{(tot_deb_hit-tot_raw_hit)/n_total:>+7.0%}"
          f"{tot_raw_ll/n_total:>8.2f}{tot_deb_ll/n_total:>8.2f}")

    print("\n=== Verdict ===")
    dhit = (tot_deb_hit - tot_raw_hit) / n_total
    dll = (tot_raw_ll - tot_deb_ll) / n_total  # positive = de-bias lowers loss
    print(f"Center-bracket hit rate: {tot_raw_hit/n_total:.1%} raw "
          f"-> {tot_deb_hit/n_total:.1%} de-biased  ({dhit:+.1%})")
    print(f"Mean log-loss:           {tot_raw_ll/n_total:.3f} raw "
          f"-> {tot_deb_ll/n_total:.3f} de-biased  ({dll:+.3f} improvement)")
    if dhit >= 0.03 or dll >= 0.03:
        print("\n=> De-biasing materially improves bracket alignment / calibration.")
        print("   Bias IS a real lever. Building the fix is justified.")
    else:
        print("\n=> De-biasing barely moves the needle. Bias is NOT the killer.")
        print("   Stop here; do not build the correction.")
    print("\nCaveats: ERA5 truth (not METAR station); short-lead (D+0/D+1) only;")
    print("center-bracket proxy, not the actual cheap-tail bracket / real prices.")


if __name__ == "__main__":
    main()
