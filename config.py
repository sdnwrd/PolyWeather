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

# NWS daily-max forecast skill degrades roughly linearly with horizon — D+0
# MAE ~2-3°F, D+3 MAE ~4-5°F. Without accounting for this we'd treat D+3
# forecasts as confidently as D+0, inflating true_prob estimates on far-out
# horizons. Effective sigma = base + HORIZON_SIGMA_GROWTH * days_ahead.
HORIZON_SIGMA_GROWTH = 0.7  # °F per day past today

# If |NDFD - Open-Meteo| forecast spread ≥ this, the city's signals are
# flagged as vetoed — shown for visibility but not actionable.
VETO_SPREAD_THRESHOLD = 3.0

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
# Render Cron fires the morning scan at 06:00 UTC (08:00 CEST). This local-
# time RUN_TIME is only used by the legacy `schedule` loop in main.py for
# manual local runs — kept for parity.
RUN_TIME = "08:00"

# Lat/lon are the airport stations Polymarket actually resolves on (per the
# market `description` / `resolutionSource`). The downtown coordinates would
# disagree with the resolution source, sometimes by several degrees (esp. LA).
#
# `region` controls forecast routing: "us" cities use NWS NDFD as primary
# (with Open-Meteo as the veto cross-check); international cities use
# Open-Meteo's best_match as primary (with GFS as the veto cross-check),
# since NWS only covers US points. Dallas/Seattle stay excluded by request
# (known active trader on those markets).
CITIES = [
    # US (NDFD primary, Open-Meteo best_match veto, °F display on Polymarket)
    {"name": "Chicago",       "lat": 41.9786, "lon": -87.9048,  "station": "KORD", "region": "us",   "tz": "America/Chicago"},
    {"name": "Miami",         "lat": 25.7932, "lon": -80.2906,  "station": "KMIA", "region": "us",   "tz": "America/New_York"},
    {"name": "Denver",        "lat": 39.7017, "lon": -104.7517, "station": "KBKF", "region": "us",   "tz": "America/Denver"},
    {"name": "Atlanta",       "lat": 33.6407, "lon": -84.4277,  "station": "KATL", "region": "us",   "tz": "America/New_York"},
    {"name": "Houston",       "lat": 29.6454, "lon": -95.2789,  "station": "KHOU", "region": "us",   "tz": "America/Chicago"},
    {"name": "Los Angeles",   "lat": 33.9425, "lon": -118.4081, "station": "KLAX", "region": "us",   "tz": "America/Los_Angeles"},
    {"name": "San Francisco", "lat": 37.6189, "lon": -122.3750, "station": "KSFO", "region": "us",   "tz": "America/Los_Angeles"},
    {"name": "Austin",        "lat": 30.1944, "lon": -97.6700,  "station": "KAUS", "region": "us",   "tz": "America/Chicago"},
    # NYC market resolves on LaGuardia, not Central Park — confirmed via
    # Polymarket description: "LaGuardia Airport Station".
    {"name": "New York",      "lat": 40.7773, "lon": -73.8726,  "station": "KLGA", "region": "us",   "tz": "America/New_York"},
    # International (Open-Meteo best_match primary, GFS veto, °C display on Polymarket)
    {"name": "Tokyo",         "lat": 35.5523, "lon": 139.7798,  "station": "RJTT", "region": "intl", "tz": "Asia/Tokyo"},
    # London market resolves on London City Airport (EGLC), NOT Heathrow.
    # EGLC is 30km east of EGLL and runs a different microclimate.
    {"name": "London",        "lat": 51.5053, "lon": 0.0553,    "station": "EGLC", "region": "intl", "tz": "Europe/London"},
    # Paris market resolves on Le Bourget (LFPB), NOT Charles-de-Gaulle.
    {"name": "Paris",         "lat": 48.9694, "lon": 2.4414,    "station": "LFPB", "region": "intl", "tz": "Europe/Paris"},
    {"name": "Munich",        "lat": 48.3538, "lon": 11.7861,   "station": "EDDM", "region": "intl", "tz": "Europe/Berlin"},
    {"name": "Singapore",     "lat": 1.3592,  "lon": 103.9894,  "station": "WSSS", "region": "intl", "tz": "Asia/Singapore"},
    # Shanghai resolves on Pudong (ZSPD), east coast. Our previous coords
    # (31.1979, 121.3364) were actually Hongqiao (ZSSS), 30km west and ~4°C
    # warmer due to inland location. Use the actual ZSPD coords.
    {"name": "Shanghai",      "lat": 31.1443, "lon": 121.8083,  "station": "ZSPD", "region": "intl", "tz": "Asia/Shanghai"},
    {"name": "Toronto",       "lat": 43.6777, "lon": -79.6248,  "station": "CYYZ", "region": "intl", "tz": "America/Toronto"},
    # Hong Kong dropped: Polymarket resolves on the Hong Kong Observatory
    # (urban King's Park station), not the airport (VHHH). HKO isn't a
    # standard METAR station, so we can't reliably get the day's max for
    # the bust check or backfill. Re-add only if/when we wire up an HKO
    # data source.
]

# Skip D+0 for any city whose local time is at or past this hour — the
# daily max occurs around 14-16:00 local, so by 17:00 the day is essentially
# locked in. Trading a D+0 bracket against a forecast that's now irrelevant
# is just gambling on whether the market has caught up yet.
D0_CUTOFF_LOCAL_HOUR = 17
