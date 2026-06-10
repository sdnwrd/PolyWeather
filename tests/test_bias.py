"""R2/R5: per-city(+horizon) forecast bias, estimated from snapshots against
station-truth, gated by sample count and a 2xSE dead-band so we never steer on
noise. Pure-logic tests; the snapshot/observation I/O is a thin wrapper."""

import math

import bias


def test_summarize_reports_mean_se_n():
    s = bias.summarize([2.0, 2.0, 2.0, 2.0])
    assert s["n"] == 4
    assert s["mean"] == 2.0
    assert s["se"] == 0.0


def test_gated_returns_zero_below_min_samples():
    stat = {"mean": 2.0, "se": 0.1, "n": 5}
    assert bias._gated(stat, min_samples=15, deadband_se=2.0) == 0.0


def test_gated_returns_mean_when_strong_and_enough_samples():
    stat = {"mean": 2.0, "se": 0.3, "n": 30}  # |mean| >> 2*se
    assert bias._gated(stat, min_samples=15, deadband_se=2.0) == 2.0


def test_gated_returns_zero_inside_deadband():
    # mean 0.4, se 0.3 -> 2*se = 0.6 > 0.4 -> within noise band -> no correction
    stat = {"mean": 0.4, "se": 0.3, "n": 30}
    assert bias._gated(stat, min_samples=15, deadband_se=2.0) == 0.0


def test_resolve_prefers_per_lead_when_bin_qualifies():
    table = {
        "Paris": {
            "per_lead": {"0": {"mean": 1.8, "se": 0.3, "n": 30}},
            "pooled": {"mean": 1.0, "se": 0.2, "n": 120},
        }
    }
    got = bias.resolve_bias(table, "Paris", 0, min_samples=15, deadband_se=2.0)
    assert got == 1.8


def test_resolve_falls_back_to_pooled_when_lead_bin_sparse():
    table = {
        "Paris": {
            "per_lead": {"3": {"mean": 1.8, "se": 0.9, "n": 4}},  # too few
            "pooled": {"mean": 1.5, "se": 0.2, "n": 120},
        }
    }
    got = bias.resolve_bias(table, "Paris", 3, min_samples=15, deadband_se=2.0)
    assert got == 1.5


def test_resolve_returns_zero_for_unknown_city():
    assert bias.resolve_bias({}, "Nowhere", 0, min_samples=15, deadband_se=2.0) == 0.0


def test_errors_by_lead_uses_raw_forecast_minus_observed_deduped():
    # One target day, scanned on three prior days (leads 2,1,0) plus a same-day
    # re-scan at lead 0 (must dedup to one error per lead).
    doc = {
        "city": "Paris", "date": "2026-06-06",
        "snapshots": [
            {"ts": "2026-06-04T05:00:00+00:00", "primary_forecast": 70.0},  # lead 2
            {"ts": "2026-06-05T05:00:00+00:00", "primary_forecast": 72.0},  # lead 1
            {"ts": "2026-06-06T05:00:00+00:00", "primary_forecast": 74.0},  # lead 0
            {"ts": "2026-06-06T11:00:00+00:00", "primary_forecast": 75.0},  # lead 0 dup
        ],
    }
    errs = bias.errors_by_lead(doc, observed=73.0)
    # raw - observed; lead 0 deduped to the last scan that day (75.0)
    assert errs[2] == 70.0 - 73.0
    assert errs[1] == 72.0 - 73.0
    assert errs[0] == 75.0 - 73.0


def test_errors_by_lead_skips_missing_forecast():
    doc = {
        "city": "X", "date": "2026-06-06",
        "snapshots": [
            {"ts": "2026-06-05T05:00:00+00:00", "primary_forecast": None},
            {"ts": "2026-06-06T05:00:00+00:00", "primary_forecast": 74.0},
        ],
    }
    errs = bias.errors_by_lead(doc, observed=73.0)
    assert 1 not in errs
    assert errs[0] == 1.0
