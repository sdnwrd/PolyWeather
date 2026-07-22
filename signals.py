"""Edge calculation: forecast → true-prob → EV vs market price."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import scipy.stats as stats

from config import (
    EV_THRESHOLD,
    FORECAST_SIGMA,
    HORIZON_SIGMA_GROWTH,
    MAX_BID_ASK_SPREAD,
    MAX_MARKET_PRICE,
    MIN_MARKET_VOLUME,
    MIN_TRUE_PROB,
)
from markets import Market


def sigma_for_horizon(base_sigma: float, days_ahead: int) -> float:
    """Horizon-adjusted sigma. base_sigma is the per-city calibrated value
    (or FORECAST_SIGMA if uncalibrated); we widen it by HORIZON_SIGMA_GROWTH
    for each day past today, reflecting the well-documented growth of NWS
    forecast MAE with lead time. days_ahead is clamped at zero (D+0)."""
    return base_sigma + max(0, days_ahead) * HORIZON_SIGMA_GROWTH


@dataclass
class Signal:
    market: Market
    forecast_high: float  # bias-corrected forecast point used for true_prob
    true_prob: float
    market_price: float
    ev: float
    # Raw primary forecast before bias correction — bias is always re-estimated
    # from the raw value, so we keep it for the journal (plan §8 R5).
    forecast_high_raw: Optional[float] = None
    forecast_openmeteo: Optional[float] = None
    model_spread: Optional[float] = None
    vetoed: bool = False  # set by main._is_vetoed: True = do NOT trade (disagreement < MIN_DISAGREEMENT_SPREAD, or veto model unavailable)
    # D+0 reality check: latest METAR temp at scan time, and whether observed
    # has already exceeded the bracket (definitive loss, don't trade).
    metar_observed: Optional[float] = None
    bracket_busted: bool = False

    @property
    def implied_prob(self) -> float:
        return self.market_price

    @property
    def kelly_fraction(self) -> float:
        """Kelly-criterion optimal stake fraction — the principled 'is this
        bet worth it' rating. Combines EV and true_prob in one number:
            f* = p − (1−p)/b, where b = (1/price) − 1
        Higher = more attractive. Capped at 0 for negative-edge bets.
        Used flat ($1) trading still benefits from this as a *ranking*
        signal, even if you don't size by it.
        """
        return kelly_fraction(self.true_prob, self.market_price)

    @property
    def rating(self) -> str:
        """At-a-glance star rating mapped from kelly_fraction."""
        return kelly_to_stars(self.kelly_fraction)


def kelly_fraction(true_prob: float, market_price: float) -> float:
    """Kelly optimal stake fraction for a binary YES bet. Returns 0 on any
    degenerate input or non-positive edge."""
    if not (0 < market_price < 1):
        return 0.0
    if not (0 < true_prob < 1):
        return 0.0
    b = (1.0 / market_price) - 1.0
    f = true_prob - (1.0 - true_prob) / b
    return max(0.0, f)


def kelly_to_stars(kelly: float) -> str:
    """Map Kelly fraction to a 5-star summary. Tuned so a typical bot
    signal (Kelly ~0.05-0.10) hits 3 stars and exceptional ones (Kelly
    ≥0.25) get 5."""
    if kelly >= 0.25:
        return "★★★★★"
    if kelly >= 0.15:
        return "★★★★☆"
    if kelly >= 0.07:
        return "★★★☆☆"
    if kelly >= 0.03:
        return "★★☆☆☆"
    return "★☆☆☆☆"


def is_bracket_busted(observed_f: float, bracket_low_f: float, bracket_high_f: float,
                      region: str) -> bool:
    """True if a current observation has already exceeded the bracket, so the
    day's max can only be further away from it. Unit-aware: intl markets
    resolve on whole-°C readings, US on whole-°F."""
    if region == "intl":
        observed_c = (observed_f - 32) * 5 / 9
        # For an intl 1°C bin, bracket_low_F corresponds to the °C label
        # (e.g. 15°C bin → bracket_low_F = 59.0 = exactly 15.0°C).
        bracket_label_c = round((bracket_low_f - 32) * 5 / 9)
        return round(observed_c) > bracket_label_c
    return round(observed_f) > round(bracket_high_f)


def estimate_true_probability(
    forecast_high: float,
    bracket_low: float,
    bracket_high: float,
    sigma: float = FORECAST_SIGMA,
) -> float:
    dist = stats.norm(loc=forecast_high, scale=sigma)
    return float(dist.cdf(bracket_high) - dist.cdf(bracket_low))


def expected_value(true_prob: float, market_price: float) -> float:
    if market_price <= 0:
        return 0.0
    return true_prob / market_price


def _bracket_distance(bracket_low: float, bracket_high: float, forecast: float) -> float:
    """Distance from forecast to the nearest edge of the bracket (0 if inside)."""
    if bracket_low <= forecast <= bracket_high:
        return 0.0
    return min(abs(forecast - bracket_low), abs(forecast - bracket_high))


def evaluate_markets(
    markets: list[Market],
    forecast_high: float,
    *,
    ev_threshold: float = EV_THRESHOLD,
    max_price: float = MAX_MARKET_PRICE,
    min_true_prob: float = MIN_TRUE_PROB,
    adjacent_window: float = 3.0,
    sigma: float = FORECAST_SIGMA,
) -> list[Signal]:
    """Score every market against the forecast and return the qualifying signals.

    `adjacent_window` widens consideration to the 2–3 adjacent brackets around
    the forecast — any bracket whose nearest edge is within `adjacent_window`
    of the forecast counts as "in scope" for the hedge strategy.

    `sigma` overrides FORECAST_SIGMA — callers can pass a per-city calibrated
    value from `calibration.get_sigma(city_name)`.
    """
    signals: list[Signal] = []
    for m in markets:
        if m.price is None:
            continue
        # Skip markets whose book is closed — no point alerting on something
        # the user can't actually trade. `None` means the field was missing,
        # which we treat as "unknown, allow through" rather than block.
        if m.accepting_orders is False:
            continue
        # Liquidity guard: avoid signaling on markets where the scanned price
        # is essentially imaginary (single stale ask, no actual depth).
        if m.volume is not None and m.volume < MIN_MARKET_VOLUME:
            continue
        if (
            m.best_bid is not None and m.best_ask is not None
            and (m.best_ask - m.best_bid) > MAX_BID_ASK_SPREAD
        ):
            continue
        if m.price > max_price:
            continue
        true_prob = estimate_true_probability(forecast_high, m.bracket_low, m.bracket_high, sigma=sigma)
        if true_prob < min_true_prob:
            continue
        dist = _bracket_distance(m.bracket_low, m.bracket_high, forecast_high)
        if dist > adjacent_window:
            continue
        ev = expected_value(true_prob, m.price)
        if ev < ev_threshold:
            continue
        signals.append(
            Signal(
                market=m,
                forecast_high=forecast_high,
                true_prob=true_prob,
                market_price=m.price,
                ev=ev,
            )
        )
    # strongest edges first
    signals.sort(key=lambda s: s.ev, reverse=True)
    return signals
