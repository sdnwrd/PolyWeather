"""R1: intl observed daily-high must come from real station METAR, not the
Open-Meteo model's own archived forecast. Tests the pure windowing/aggregation
helpers and the intl routing of get_observed_high."""

from datetime import date, datetime, timezone

import forecast


def _epoch(y, mo, d, h):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp())


def test_local_day_window_utc_for_paris_june():
    # Paris is UTC+2 (CEST) in June. Local 2026-06-06 spans
    # 2026-06-05 22:00Z .. 2026-06-06 22:00Z.
    city = {"tz": "Europe/Paris"}
    start, end = forecast._local_day_window_utc(city, date(2026, 6, 6))
    assert start == datetime(2026, 6, 5, 22, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 6, 22, tzinfo=timezone.utc)


def test_metar_max_f_in_window_filters_and_converts():
    start = datetime(2026, 6, 5, 22, tzinfo=timezone.utc)
    end = datetime(2026, 6, 6, 22, tzinfo=timezone.utc)
    records = [
        {"obsTime": _epoch(2026, 6, 5, 21), "temp": 30.0},   # before window, ignore
        {"obsTime": _epoch(2026, 6, 6, 12), "temp": 20.0},   # 68F
        {"obsTime": _epoch(2026, 6, 6, 14), "temp": 25.0},   # 77F  <- max in-window
        {"obsTime": _epoch(2026, 6, 6, 23), "temp": 40.0},   # after window, ignore
        {"obsTime": _epoch(2026, 6, 6, 15), "temp": None},   # missing temp, skip
    ]
    assert forecast._metar_max_f_in_window(records, start, end) == 77.0


def test_metar_max_f_in_window_none_when_no_obs_in_window():
    start = datetime(2026, 6, 5, 22, tzinfo=timezone.utc)
    end = datetime(2026, 6, 6, 22, tzinfo=timezone.utc)
    records = [{"obsTime": _epoch(2026, 6, 1, 12), "temp": 25.0}]
    assert forecast._metar_max_f_in_window(records, start, end) is None


def test_get_observed_high_intl_uses_metar_not_openmeteo(monkeypatch):
    city = {"name": "Paris", "region": "intl", "station": "LFPB",
            "tz": "Europe/Paris", "lat": 48.97, "lon": 2.44}

    called = {}

    def fake_fetch(station, start_utc, end_utc):
        called["station"] = station
        return 77.0

    monkeypatch.setattr(forecast, "_fetch_metar_day_max", fake_fetch)
    # If the intl path ever calls Open-Meteo archive, fail loudly.
    monkeypatch.setattr(
        forecast.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not hit HTTP archive")),
    )

    result = forecast.get_observed_high(city, date(2026, 6, 6))
    assert result == 77.0
    assert called["station"] == "LFPB"
