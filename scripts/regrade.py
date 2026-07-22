"""One-time re-grade of data/signals.csv after the half-open bracket fix.

Recomputes `outcome` from the already-stored `actual_high` (no re-fetch) using
the corrected _bracket_contains. Reports every flip and the before/after
win-rate on non-vetoed rows. Pass --write to persist; default is dry-run.
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from journal import CSV_PATH, FIELDS, _bracket_contains


def _f(s):
    if s in ("-inf", ""):
        return float("-inf")
    if s == "inf":
        return float("inf")
    return float(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    with CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    flips = []
    for r in rows:
        if r.get("outcome") not in ("WIN", "LOSS"):
            continue
        if r.get("actual_high") in (None, ""):
            continue
        lo, hi = _f(r["bracket_low"]), _f(r["bracket_high"])
        actual = float(r["actual_high"])
        new = "WIN" if _bracket_contains(lo, hi, actual) else "LOSS"
        if new != r["outcome"]:
            flips.append((r, r["outcome"], new))
            r["outcome"] = new

    def winrate(pred):
        res = [r for r in rows if r.get("outcome") in ("WIN", "LOSS") and pred(r)]
        wins = sum(1 for r in rows if r.get("outcome") == "WIN" and pred(r))
        return wins, len(res)

    print(f"total rows: {len(rows)}")
    print(f"flips: {len(flips)}")
    for r, old, new in flips:
        print(f"  {old}->{new}  {r['target_date']} {r['city']} "
              f"[{r['bracket_low']},{r['bracket_high']}) actual={r['actual_high']} "
              f"vetoed={r['vetoed']}  {r['question']}")

    nv = lambda r: r.get("vetoed") == "false"
    w, n = winrate(nv)
    print(f"\nAFTER re-grade, non-vetoed resolved: {w} wins / {n} = "
          f"{100*w/n:.1f}%" if n else "no resolved")

    if args.write and flips:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(rows)
        print(f"\nWROTE {len(flips)} corrections to {CSV_PATH}")
    elif not args.write:
        print("\n(dry-run — pass --write to persist)")


if __name__ == "__main__":
    main()
