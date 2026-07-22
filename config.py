"""Configuration for the weather signal bot."""

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

NWS_USER_AGENT = os.getenv(
    "NWS_USER_AGENT",
    "weather-signal-bot/1.0 (contact@example.com)",
)

EV_THRESHOLD = 8.0
MAX_MARKET_PRICE = 0.15
MIN_TRUE_PROB = 0.05
FORECAST_SIGMA = 2.0

# Per-city(+horizon) forecast bias correction (see docs/plans bias-correction).
# The morning forecast point is shifted by the estimated bias before true_prob
# is computed, but ONLY when an estimate is trustworthy: at least
# BIAS_MIN_SAMPLES resolved snapshots AND the estimate clears a dead-band of
# BIAS_DEADBAND_SE standard errors (so we never steer on noise). No hard-coded
# seed — estimates come only from live station-truth snapshots (plan §8 R4).
#
# DISABLED 2026-07-18: live METAR data (5 weeks, 53 corrected bets) showed the
# correction moved the forecast AWAY from the actual 38/53 times (72%) and hurt
# win rate (6.4% off → 5.7% on). Root cause: bias is estimated over ALL days,
# but we only BET the tail (heatwaves); subtracting the seasonal mean on a
# hot-tail day pulls the forecast toward center, away from the tail we trade.
# The ERA5 spring falsification sim didn't catch this (grid truth, wrong season,
# no tail selection). The bias TABLE is still built for diagnostics, but the
# correction is not applied while this flag is False. Re-enable only after a
# tail-aware redesign (see docs/plans). Keep raw forecast = the real edge.
BIAS_CORRECTION_ENABLED = False
BIAS_MIN_SAMPLES = 15
BIAS_DEADBAND_SE = 2.0

# NWS daily-max forecast skill degrades roughly linearly with horizon — D+0
# MAE ~2-3°F, D+3 MAE ~4-5°F. Without accounting for this we'd treat D+3
# forecasts as confidently as D+0, inflating true_prob estimates on far-out
# horizons. Effective sigma = base + HORIZON_SIGMA_GROWTH * days_ahead.
HORIZON_SIGMA_GROWTH = 0.7  # °F per day past today

# Minimum |primary - veto| forecast disagreement required to TRADE a bracket.
# Strategy (2026-07-22): the edge lives in extreme model disagreement — corrected
# live data shows winrate ~2% at <3°F, 0% at [3,5)°F, ~10.5% at >=5°F. Below this
# threshold (and when the veto model is unavailable) we do not trade.
MIN_DISAGREEMENT_SPREAD = 5.0

# Liquidity guards — skip markets that can't be filled cleanly at the
# scanned price. Borrowed from weatherbet's MIN_VOLUME/MAX_SLIPPAGE.
MIN_MARKET_VOLUME = 100.0   # USD volume floor on the bracket market
MAX_BID_ASK_SPREAD = 0.05   # in dollars; skip if bestAsk - bestBid > this

# Polymarket's `endDate` (12:00 UTC) is the nominal resolution timestamp,
# not the trading deadline — order books typically stay open until late
# local evening, when the day's recorded high is locked in. The strategic
# edge window, though, ends around 12:00 UTC when US prop desks wake up
# and start correcting overnight mispricings. So earlier is better.
#
# Render Cron fires the morning scan at 05:00 UTC (07:00 CEST). This local-
# time RUN_TIME is only used by the legacy `schedule` loop in main.py for
# manual local runs — kept for parity.
RUN_TIME = "07:00"

# Lat/lon are the airport stations Polymarket actually resolves on (per the
# market `description` / `resolutionSource`). The downtown coordinates would
# disagree with the resolution source, sometimes by several degrees (esp. LA).
#
# `region` controls forecast routing: "us" cities use NWS NDFD as primary
# (with Open-Meteo as the veto cross-check); international cities use
# Open-Meteo's best_match as primary (with GFS as the veto cross-check),
# since NWS only covers US points. Dallas/Seattle stay excluded by request
# (known active trader on those markets).
# Trimmed 2026-07-18 to the international thin-market tail basket. Over 5 live
# weeks (Jun 11 – Jul 18, 131 non-vetoed resolved bets) the only cities that
# produced wins were Paris (5), Shanghai / London / Singapore (1 each). Every
# US city plus Munich, Tokyo and Toronto went 0-for and were pure drag — the
# edge lives in low-liquidity intl tail markets, not US NDFD markets. Cut list
# preserved below in case we revisit. All survivors are region "intl"
# (Open-Meteo best_match primary, GFS veto, °C display on Polymarket).
CITIES = [
    # London market resolves on London City Airport (EGLC), NOT Heathrow.
    # EGLC is 30km east of EGLL and runs a different microclimate.
    {"name": "London",        "lat": 51.5053, "lon": 0.0553,    "station": "EGLC", "region": "intl", "tz": "Europe/London"},
    # Paris market resolves on Le Bourget (LFPB), NOT Charles-de-Gaulle.
    {"name": "Paris",         "lat": 48.9694, "lon": 2.4414,    "station": "LFPB", "region": "intl", "tz": "Europe/Paris"},
    {"name": "Singapore",     "lat": 1.3592,  "lon": 103.9894,  "station": "WSSS", "region": "intl", "tz": "Asia/Singapore"},
    # Shanghai resolves on Pudong (ZSPD), east coast. Our previous coords
    # (31.1979, 121.3364) were actually Hongqiao (ZSSS), 30km west and ~4°C
    # warmer due to inland location. Use the actual ZSPD coords.
    {"name": "Shanghai",      "lat": 31.1443, "lon": 121.8083,  "station": "ZSPD", "region": "intl", "tz": "Asia/Shanghai"},
    # --- Cut 2026-07-18 (0 wins / pure drag over 5 weeks) — re-add if revisited:
    # US (NDFD primary): Chicago KORD, Miami KMIA, Denver KBKF, Atlanta KATL,
    #   Houston KHOU, Los Angeles KLAX, San Francisco KSFO, Austin KAUS,
    #   New York KLGA.
    # Intl: Tokyo RJTT, Munich EDDM, Toronto CYYZ.
    # Hong Kong stays dropped separately (resolves on HKO King's Park, not a
    # standard METAR station — no reliable day-max source).
]

# How many calendar days ahead to scan (D+0 = today, D+1 = tomorrow, ...).
# Raised to 2 (2026-07-22): high-disagreement strategy needs D+0 AND D+1.
# Tokyo D+0 is gated separately by D0_CUTOFF_LOCAL_HOUR.
SCAN_HORIZON_DAYS = 2  # D+0 + D+1 (>=5F strategy; Tokyo D+0 gated by cutoff)

# Skip D+0 for any city whose local time is at or past this hour — the
# daily max occurs around 14-16:00 local, so by 17:00 the day is essentially
# locked in. Trading a D+0 bracket against a forecast that's now irrelevant
# is just gambling on whether the market has caught up yet.
#
# Lowered to 14 (2026-07-22): user trades at scan time (05:00 UTC), so a city
# already at peak-onset (~14:00 local) at scan has no forecast lead. Skips
# Tokyo D+0 (14:00 JST) while keeping London (06:00) and Paris (07:00).
D0_CUTOFF_LOCAL_HOUR = 14
