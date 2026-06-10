"""R5/§5: when the forecast was bias-corrected, the Telegram block shows both
the corrected point (what was bet) and the raw point (for transparency)."""

from datetime import date

import notifier
from markets import Market
from signals import Signal


def _chicago_signal(corrected, raw):
    m = Market(city="Chicago", question="80-81F", bracket_low=80.0, bracket_high=81.0,
               resolution_date=date(2026, 6, 10), token_id="t", slug="s", price=0.1)
    s = Signal(market=m, forecast_high=corrected, true_prob=0.2, market_price=0.1, ev=20.0)
    s.forecast_high_raw = raw
    return s


def test_block_shows_raw_when_corrected():
    s = _chicago_signal(corrected=80.0, raw=82.0)
    block = notifier._city_date_block("Chicago", date(2026, 6, 10), [s])
    assert "80°F" in block
    assert "raw 82°F" in block


def test_block_omits_raw_when_no_correction():
    s = _chicago_signal(corrected=82.0, raw=82.0)
    block = notifier._city_date_block("Chicago", date(2026, 6, 10), [s])
    assert "raw" not in block
