"""Ad-hoc P&L explorer for the model-disagreement (veto) thread.

All resolved rows, sliced many ways. ASCII only (Windows cp1252 console).
$1-flat P&L: a WIN pays (1/price - 1), a LOSS pays -1. 'exp' = sum of prices
= wins you'd expect if the market were fairly priced (edge = wins > exp).
'noBest' = P&L with the single most profitable win removed (concentration check).
"""

import csv
from pathlib import Path

ROWS = list(csv.DictReader(open(Path(__file__).parent.parent / "data/signals.csv", encoding="utf-8")))
R = [r for r in ROWS if r["outcome"] in ("WIN", "LOSS")]
# Current live basket (2026-07-22): >=5F strategy trades these.
BASKET = {"London", "Paris", "Tokyo"}


def sp(r):
    try:
        return float(r["model_spread"])
    except (ValueError, TypeError):
        return -1.0  # missing spread -> its own bucket


def net(r):
    p = float(r["market_price"])
    if p <= 0:
        return 0.0
    return (1.0 / p - 1.0) if r["outcome"] == "WIN" else -1.0


def line(label, bets):
    if not bets:
        print(f"  {label:28s}  (none)")
        return
    wins = [r for r in bets if r["outcome"] == "WIN"]
    pnl = sum(net(r) for r in bets)
    exp = sum(float(r["market_price"]) for r in bets if float(r["market_price"]) > 0)
    best = max((net(r) for r in wins), default=0.0)
    avg_p = 100 * exp / len(bets)
    wr = 100 * len(wins) / len(bets)
    print(f"  {label:28s}  {len(wins):2d}W/{len(bets):3d}={wr:4.1f}%  "
          f"avgP={avg_p:4.2f}%  exp={exp:4.1f}  PnL={pnl:+8.2f}  noBest={pnl-best:+8.2f}")


def section(title, bets):
    print(f"\n=== {title} (n={len(bets)}) ===")
    print("  spread bands:")
    line("all", bets)
    line("<3F (traded/low-disagree)", [r for r in bets if 0 <= sp(r) < 3])
    line("[3,5)F", [r for r in bets if 3 <= sp(r) < 5])
    line(">=5F (extreme disagree)", [r for r in bets if sp(r) >= 5])
    line(">=3F (all vetoed band)", [r for r in bets if sp(r) >= 3])


# ---- global spread bands
section("ALL DATA, all horizons, all cities", R)

# ---- by horizon
for h in ["0", "1", "2"]:
    section(f"HORIZON D+{h}", [r for r in R if r["days_ahead"] == h])

# ---- current basket only, all horizons
section("Current basket (London/Paris/Tokyo), all horizons", [r for r in R if r["city"] in BASKET])

# ---- current live config: basket + D+0/D+1 (Tokyo D+1 only handled by cutoff live)
section("Current basket, D+0&D+1", [r for r in R if r["days_ahead"] in ("0", "1") and r["city"] in BASKET])

# ---- >=5F extreme-disagreement, split by city
print("\n=== >=5F EXTREME-DISAGREEMENT by city (all horizons) ===")
hi = [r for r in R if sp(r) >= 5]
for c in sorted({r["city"] for r in hi}):
    line(c, [r for r in hi if r["city"] == c])

# ---- >=5F, split by horizon
print("\n=== >=5F EXTREME-DISAGREEMENT by horizon ===")
for h in ["0", "1", "2"]:
    line(f"D+{h}", [r for r in hi if r["days_ahead"] == h])

# ---- every >=5F bet, listed
print("\n=== every >=5F bet (raw) ===")
for r in sorted(hi, key=lambda r: (r["target_date"], r["city"])):
    print(f"  {r['outcome']:4} {r['target_date']} {r['city']:9s} D+{r['days_ahead']} "
          f"spread={float(r['model_spread']):4.1f}F price={r['market_price']:>5} "
          f"vetoed={r['vetoed']:5} net={net(r):+7.1f}  {r['question'][:42]}")
