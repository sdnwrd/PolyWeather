"""Per-city sigma calibration from journal history.

The signal model assumes forecast error is normal with sigma=FORECAST_SIGMA
(default 2°F). But real forecast skill varies by city — coastal LA has
different error characteristics than continental Chicago. Once enough
resolved signals accumulate per city, we replace the global sigma with a
calibrated per-city MAE.

The calibration is *dormant* by design until CALIBRATION_MIN samples
exist for a given city — until then, every city uses FORECAST_SIGMA.
This avoids overfitting noise from the first handful of resolved bets.

Output: `data/calibration.json` with shape:
    {
        "Chicago": {"sigma": 2.4, "n": 35, "updated_at": "..."},
        ...
    }
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import FORECAST_SIGMA
import journal

log = logging.getLogger(__name__)

CALIBRATION_FILE = Path(__file__).parent / "data" / "calibration.json"
CALIBRATION_MIN = 30   # need this many resolved samples per city before trusting MAE
_CACHE: Optional[dict] = None


def _load() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not CALIBRATION_FILE.exists():
        _CACHE = {}
        return _CACHE
    try:
        _CACHE = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("calibration file unreadable: %s — falling back to defaults", e)
        _CACHE = {}
    return _CACHE


def get_sigma(city_name: str) -> float:
    """Active sigma for a city — calibrated if enough data, else default."""
    cal = _load()
    entry = cal.get(city_name)
    if entry and entry.get("n", 0) >= CALIBRATION_MIN:
        return float(entry["sigma"])
    return FORECAST_SIGMA


def run_calibration() -> dict:
    """Recompute per-city sigma from the journal's resolved rows. Writes
    `data/calibration.json` and returns the new mapping. Cities with fewer
    than CALIBRATION_MIN resolved samples are recorded but not active.
    """
    global _CACHE
    resolved = journal.read_resolved()
    errors: dict[str, list[float]] = defaultdict(list)

    for row in resolved:
        city = row.get("city")
        try:
            forecast = float(row["forecast_high"])
            actual = float(row["actual_high"])
        except (TypeError, ValueError):
            continue
        if not city:
            continue
        errors[city].append(abs(forecast - actual))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_cal: dict = {}
    updated_lines: list[str] = []
    for city, errs in errors.items():
        if not errs:
            continue
        # MAE under a normal model approximates sigma * sqrt(2/pi) ≈ 0.798*sigma.
        # So sigma ≈ MAE / 0.798. We use that as the calibrated sigma.
        mae = sum(errs) / len(errs)
        sigma = round(mae / 0.7978845608, 3)
        old_entry = _load().get(city, {})
        new_cal[city] = {"sigma": sigma, "n": len(errs), "updated_at": now}
        old_sigma = old_entry.get("sigma")
        if old_sigma is None or abs(sigma - float(old_sigma)) > 0.05:
            active = "active" if len(errs) >= CALIBRATION_MIN else "dormant"
            updated_lines.append(
                f"{city}: sigma={sigma:.2f} n={len(errs)} ({active})"
            )

    try:
        CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION_FILE.write_text(json.dumps(new_cal, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("calibration write failed: %s", e)

    _CACHE = new_cal
    if updated_lines:
        log.info("calibration updated: %s", "; ".join(updated_lines))
    return new_cal


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cal = run_calibration()
    print(json.dumps(cal, indent=2))
