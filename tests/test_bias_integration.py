"""R2/R5 wiring: main corrects the forecast before evaluating, and the journal
records both the raw and the bias-corrected forecast point."""

import csv
from datetime import date

import pytest

import config
import journal
import main
from markets import Market
from signals import Signal


def test_apply_bias_subtracts_estimated_bias(monkeypatch):
    # The subtract path only runs when the correction is enabled (disabled by
    # default since 2026-07-18 — see config.BIAS_CORRECTION_ENABLED).
    monkeypatch.setattr(config, "BIAS_CORRECTION_ENABLED", True)
    monkeypatch.setattr(main.bias, "get_bias", lambda c, d: 1.6)
    corrected, correction = main.apply_bias("Paris", 0, 74.0)
    assert correction == 1.6
    assert corrected == pytest.approx(72.4)


def test_apply_bias_disabled_returns_raw(monkeypatch):
    # When disabled, apply_bias is a pure passthrough and never consults the
    # bias table — we trade the raw forecast.
    monkeypatch.setattr(config, "BIAS_CORRECTION_ENABLED", False)

    def _boom(*a, **k):
        raise AssertionError("get_bias must not be called when disabled")

    monkeypatch.setattr(main.bias, "get_bias", _boom)
    corrected, correction = main.apply_bias("Paris", 0, 82.6)
    assert correction == 0.0
    assert corrected == 82.6


def test_apply_bias_noop_when_no_estimate(monkeypatch):
    monkeypatch.setattr(main.bias, "get_bias", lambda c, d: 0.0)
    corrected, correction = main.apply_bias("Denver", 2, 70.0)
    assert correction == 0.0
    assert corrected == 70.0


def test_journal_logs_raw_and_corrected_forecast(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "CSV_PATH", tmp_path / "signals.csv")
    m = Market(
        city="Paris", question="be 23C", bracket_low=73.4, bracket_high=75.2,
        resolution_date=date(2026, 6, 10), token_id="t", slug="s", price=0.1,
    )
    sig = Signal(market=m, forecast_high=72.4, true_prob=0.2, market_price=0.1, ev=20.0)
    sig.forecast_high_raw = 74.0  # raw before bias correction

    journal.log_signals([sig], date(2026, 6, 10))

    with (tmp_path / "signals.csv").open(newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["forecast_high"] == "72.4"      # corrected (what was bet)
    assert row["forecast_high_raw"] == "74.0"  # raw (what bias is measured from)
