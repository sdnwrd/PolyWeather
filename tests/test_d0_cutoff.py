"""D+0 is skipped once a city is at/after peak-onset (D0_CUTOFF_LOCAL_HOUR) at
scan time. Scan fires 05:00 UTC: London 06:00, Paris 07:00, Tokyo 14:00 local."""

from datetime import date, datetime, timezone

import main
from config import D0_CUTOFF_LOCAL_HOUR, SCAN_HORIZON_DAYS

SCAN = datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc)  # cron scan time
TODAY = date(2026, 7, 15)
TOMORROW = date(2026, 7, 16)
LONDON = {"tz": "Europe/London"}
PARIS = {"tz": "Europe/Paris"}
TOKYO = {"tz": "Asia/Tokyo"}


def test_config_values():
    assert SCAN_HORIZON_DAYS == 2
    assert D0_CUTOFF_LOCAL_HOUR == 14


def test_tokyo_d0_skipped_at_scan():
    # 05:00 UTC == 14:00 JST == cutoff -> skip Tokyo D+0
    assert main._is_d0_too_late(TOKYO, TODAY, now_utc=SCAN) is True


def test_tokyo_d1_kept():
    assert main._is_d0_too_late(TOKYO, TOMORROW, now_utc=SCAN) is False


def test_london_and_paris_d0_kept():
    assert main._is_d0_too_late(LONDON, TODAY, now_utc=SCAN) is False
    assert main._is_d0_too_late(PARIS, TODAY, now_utc=SCAN) is False
