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
# 07:30 German local = 05:30 UTC (summer) / 06:30 UTC (winter) — leaves
# 5.5–6.5h of edge headroom. The schedule library uses local time, so
# this stays correct across the European DST switch automatically.
RUN_TIME = "07:30"

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
    # US (NDFD primary, Open-Meteo best_match veto)
    {"name": "Chicago",       "lat": 41.9786, "lon": -87.9048,  "station": "KORD", "region": "us"},
    {"name": "Miami",         "lat": 25.7932, "lon": -80.2906,  "station": "KMIA", "region": "us"},
    {"name": "Denver",        "lat": 39.7017, "lon": -104.7517, "station": "KBKF", "region": "us"},
    {"name": "Atlanta",       "lat": 33.6407, "lon": -84.4277,  "station": "KATL", "region": "us"},
    {"name": "Houston",       "lat": 29.6454, "lon": -95.2789,  "station": "KHOU", "region": "us"},
    {"name": "Los Angeles",   "lat": 33.9425, "lon": -118.4081, "station": "KLAX", "region": "us"},
    {"name": "San Francisco", "lat": 37.6189, "lon": -122.3750, "station": "KSFO", "region": "us"},
    {"name": "Austin",        "lat": 30.1944, "lon": -97.6700,  "station": "KAUS", "region": "us"},
    {"name": "New York",      "lat": 40.7794, "lon": -73.9692,  "station": "KNYC", "region": "us"},
    # International (Open-Meteo best_match primary, GFS veto)
    {"name": "Tokyo",         "lat": 35.5523, "lon": 139.7798,  "station": "RJTT", "region": "intl"},
    {"name": "London",        "lat": 51.4775, "lon": -0.4614,   "station": "EGLL", "region": "intl"},
    {"name": "Paris",         "lat": 49.0097, "lon": 2.5479,    "station": "LFPG", "region": "intl"},
    {"name": "Munich",        "lat": 48.3538, "lon": 11.7861,   "station": "EDDM", "region": "intl"},
    {"name": "Singapore",     "lat": 1.3592,  "lon": 103.9894,  "station": "WSSS", "region": "intl"},
    {"name": "Shanghai",      "lat": 31.1979, "lon": 121.3364,  "station": "ZSPD", "region": "intl"},
    {"name": "Toronto",       "lat": 43.6777, "lon": -79.6248,  "station": "CYYZ", "region": "intl"},
    {"name": "Hong Kong",     "lat": 22.3080, "lon": 113.9185,  "station": "VHHH", "region": "intl"},
]
