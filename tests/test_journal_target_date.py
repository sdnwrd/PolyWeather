"""R3: the journal must key on the signal's resolution (target) date, not the
scan date. A D+0 and D+1 signal scanned the same morning for the same bracket
must both be recorded and must each backfill against their own target day."""

import csv
from datetime import date

import pytest

import journal
from markets import Market
from signals import Signal


def _market(resolution_date, low=80.0, high=81.0, city="Chicago"):
    return Market(
        city=city,
        question=f"Will the high be {low:.0f}-{high:.0f}F",
        bracket_low=low,
        bracket_high=high,
        resolution_date=resolution_date,
        token_id="tok",
        slug="slug",
        price=0.10,
    )


def _signal(resolution_date, low=80.0, high=81.0, city="Chicago", forecast=80.5):
    m = _market(resolution_date, low, high, city)
    return Signal(market=m, forecast_high=forecast, true_prob=0.2,
                  market_price=0.10, ev=20.0)


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "CSV_PATH", tmp_path / "signals.csv")
    return tmp_path / "signals.csv"


def _rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_logs_target_date_and_days_ahead(tmp_journal):
    scan_day = date(2026, 6, 10)
    sig = _signal(resolution_date=date(2026, 6, 12))  # D+2
    journal.log_signals([sig], scan_day)

    rows = _rows(tmp_journal)
    assert len(rows) == 1
    assert rows[0]["target_date"] == "2026-06-12"
    assert rows[0]["days_ahead"] == "2"


def test_same_bracket_different_horizons_both_kept(tmp_journal):
    scan_day = date(2026, 6, 10)
    d0 = _signal(resolution_date=date(2026, 6, 10))  # D+0
    d1 = _signal(resolution_date=date(2026, 6, 11))  # D+1, identical bracket
    journal.log_signals([d0, d1], scan_day)

    rows = _rows(tmp_journal)
    targets = sorted(r["target_date"] for r in rows)
    assert targets == ["2026-06-10", "2026-06-11"], (
        "D+0 and D+1 signals for the same bracket must not collide in dedup"
    )


def test_backfill_resolves_against_target_date_not_scan_date(tmp_journal, monkeypatch):
    # Scanned several days ago for a target that has since resolved. The
    # observation lookup must use the TARGET date, not the scan date.
    scan_day = date(2026, 6, 4)
    # Use a city that's still in CITIES (backfill skips unknown cities).
    sig = _signal(resolution_date=date(2026, 6, 6), low=80.0, high=81.0, city="Paris")
    journal.log_signals([sig], scan_day)

    seen_dates = []

    def fake_obs(city, target_date):
        seen_dates.append(target_date)
        return 80.5  # inside the 80-81 bracket → WIN

    import forecast
    monkeypatch.setattr(forecast, "get_observed_high", fake_obs)

    journal.backfill(today=date(2026, 6, 10))

    assert seen_dates == [date(2026, 6, 6)], "must look up the target day"
    rows = _rows(tmp_journal)
    assert rows[0]["actual_high"] == "80.5"
    assert rows[0]["outcome"] == "WIN"
