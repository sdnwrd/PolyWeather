"""Edge calculation: forecast → true-prob → EV vs market price."""

from __future__ import annotations

from dataclasses import dataclass

import scipy.stats as stats

from config import EV_THRESHOLD, FORECAST_SIGMA, MAX_MARKET_PRICE, MIN_TRUE_PROB
from markets import Market


@dataclass
class Signal:
    market: Market
    forecast_high: float
    true_prob: float
    market_price: float
    ev: float

    @property
    def implied_prob(self) -> float:
        return self.market_price


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
) -> list[Signal]:
    """Score every market against the forecast and return the qualifying signals.

    `adjacent_window` widens consideration to the 2–3 adjacent brackets around
    the forecast — any bracket whose nearest edge is within `adjacent_window`
    of the forecast counts as "in scope" for the hedge strategy.
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
        if m.price > max_price:
            continue
        true_prob = estimate_true_probability(forecast_high, m.bracket_low, m.bracket_high)
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
