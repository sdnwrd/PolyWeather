"""Per-city(+horizon) forecast bias correction, estimated from snapshots.

Why this exists: a backtest (scripts/backtest_forecast_skill.py) found the
base sigma is honest (~2°F) but several cities carry a systematic *bias* of
1-2°F (Paris +1.6, Houston +2.1, Tokyo -1.5, ...). On ~2°F-wide brackets a
centering bias makes the bot bet the bracket adjacent to the right one — fatal
for a tail-betting strategy, since bias (unlike noise) does not average out.

Design (plan §8 R2/R4/R5):
  - Estimated from `snapshots.py` (which records the RAW primary forecast on
    every scan, fired or not), NOT the fired-signal journal (selection-biased).
  - Truth = station observation via forecast.get_observed_high (R1 fix).
  - error = raw_forecast - observed, binned per (city, days_ahead) with a
    pooled-per-city fallback.
  - Applied only behind a gate: n >= BIAS_MIN_SAMPLES AND |mean| >= 2*SE
    (dead-band), else no correction. No hard-coded seed.

Output: data/bias.json
    {"Paris": {"per_lead": {"0": {"mean":1.6,"se":0.3,"n":30}, ...},
               "pooled": {"mean":1.5,"se":0.2,"n":120}}, ...}
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from config import BIAS_DEADBAND_SE, BIAS_MIN_SAMPLES, CITIES

log = logging.getLogger(__name__)

BIAS_FILE = Path(__file__).parent / "data" / "bias.json"
_CACHE: Optional[dict] = None


# ---------- pure logic ----------

def summarize(errors: list[float]) -> dict:
    """Mean, standard error of the mean, and n for a list of errors."""
    n = len(errors)
    if n == 0:
        return {"mean": 0.0, "se": 0.0, "n": 0}
    mean = statistics.fmean(errors)
    se = statistics.stdev(errors) / math.sqrt(n) if n >= 2 else 0.0
    return {"mean": round(mean, 4), "se": round(se, 4), "n": n}


def _gated(stat: dict, min_samples: int, deadband_se: float) -> float:
    """The correction to apply for one stat bin: 0 unless it has enough
    samples AND clears the dead-band (|mean| >= deadband_se * SE)."""
    if stat["n"] < min_samples:
        return 0.0
    if abs(stat["mean"]) < deadband_se * stat["se"]:
        return 0.0
    return stat["mean"]


def resolve_bias(
    table: dict,
    city: str,
    days_ahead: int,
    *,
    min_samples: int = BIAS_MIN_SAMPLES,
    deadband_se: float = BIAS_DEADBAND_SE,
) -> float:
    """Bias to SUBTRACT from the raw forecast for (city, days_ahead).

    Prefer the per-lead bin when it has enough samples (gated); else fall back
    to the pooled per-city estimate (gated); else no correction.
    """
    entry = table.get(city)
    if not entry:
        return 0.0
    lead_stat = (entry.get("per_lead") or {}).get(str(days_ahead))
    if lead_stat and lead_stat["n"] >= min_samples:
        return _gated(lead_stat, min_samples, deadband_se)
    pooled = entry.get("pooled")
    if pooled and pooled["n"] >= min_samples:
        return _gated(pooled, min_samples, deadband_se)
    return 0.0


def errors_by_lead(doc: dict, observed: float) -> dict[int, float]:
    """From one (city, target) snapshot doc + the observed station max, return
    {days_ahead: raw_forecast - observed}, deduped to one error per lead
    (later scans on the same day win)."""
    target = date.fromisoformat(doc["date"])
    latest: dict[int, tuple[datetime, float]] = {}
    for snap in doc.get("snapshots", []):
        fc = snap.get("primary_forecast")
        if fc is None:
            continue
        try:
            ts = datetime.fromisoformat(snap["ts"])
        except (KeyError, ValueError):
            continue
        lead = (target - ts.date()).days
        if lead < 0:
            continue
        prev = latest.get(lead)
        if prev is None or ts >= prev[0]:
            latest[lead] = (ts, float(fc) - observed)
    return {lead: err for lead, (ts, err) in latest.items()}


# ---------- I/O orchestration ----------

def _city_by_name(name: str) -> Optional[dict]:
    for c in CITIES:
        if c["name"] == name:
            return c
    return None


def build_bias_table(today: Optional[date] = None) -> dict:
    """Scan all snapshot files, fetch station-truth for each resolved target,
    accumulate raw-forecast errors per (city, lead), and write data/bias.json."""
    import snapshots  # local import: snapshots imports signals/markets
    from forecast import get_observed_high

    global _CACHE
    today = today or date.today()
    per_lead: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    pooled: dict[str, list[float]] = defaultdict(list)

    for path in snapshots.iter_all_snapshot_files():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("bias: unreadable snapshot %s: %s", path, e)
            continue
        city_name = doc.get("city")
        try:
            target = date.fromisoformat(doc.get("date", ""))
        except ValueError:
            continue
        if target >= today:
            continue  # not yet resolved
        city = _city_by_name(city_name)
        if city is None:
            continue
        try:
            observed = get_observed_high(city, target)
        except Exception as e:
            log.warning("bias: observation lookup failed for %s %s: %s",
                        city_name, target, e)
            observed = None
        if observed is None:
            continue
        for lead, err in errors_by_lead(doc, observed).items():
            per_lead[city_name][lead].append(err)
            pooled[city_name].append(err)

    table: dict = {}
    for city_name, leads in per_lead.items():
        table[city_name] = {
            "per_lead": {str(lead): summarize(errs) for lead, errs in leads.items()},
            "pooled": summarize(pooled[city_name]),
        }

    try:
        BIAS_FILE.parent.mkdir(parents=True, exist_ok=True)
        BIAS_FILE.write_text(json.dumps(table, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("bias: write failed: %s", e)

    _CACHE = table
    active = [
        f"{c} D+{lead}={s['mean']:+.1f}(n{s['n']})"
        for c, e in table.items()
        for lead, s in e["per_lead"].items()
        if s["n"] >= BIAS_MIN_SAMPLES and abs(s["mean"]) >= BIAS_DEADBAND_SE * s["se"]
    ]
    if active:
        log.info("bias corrections active: %s", "; ".join(active))
    return table


def _load() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not BIAS_FILE.exists():
        _CACHE = {}
        return _CACHE
    try:
        _CACHE = json.loads(BIAS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("bias file unreadable: %s — no correction", e)
        _CACHE = {}
    return _CACHE


def get_bias(city_name: str, days_ahead: int) -> float:
    """Active bias to subtract from the raw forecast for (city, days_ahead).
    0.0 when no trustworthy estimate exists."""
    return resolve_bias(_load(), city_name, days_ahead)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(json.dumps(build_bias_table(), indent=2))
