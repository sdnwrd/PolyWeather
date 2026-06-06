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

# Markets close at 12:00 UTC = 14:00 CEST / 13:00 CET.
# 07:30 German local = 05:30 UTC (summer) / 06:30 UTC (winter) — leaves
# 5.5–6.5h headroom before close. The schedule library uses local time, so
# this stays correct across the European DST switch automatically.
RUN_TIME = "07:30"

# Lat/lon are the airport stations Polymarket actually resolves on (per the
# market `description` / `resolutionSource`). The downtown coordinates would
# disagree with the resolution source, sometimes by several degrees (esp. LA).
CITIES = [
    {"name": "Chicago",       "lat": 41.9786, "lon": -87.9048,  "station": "KORD"},
    {"name": "Miami",         "lat": 25.7932, "lon": -80.2906,  "station": "KMIA"},
    {"name": "Denver",        "lat": 39.7017, "lon": -104.7517, "station": "KBKF"},
    {"name": "Atlanta",       "lat": 33.6407, "lon": -84.4277,  "station": "KATL"},
    {"name": "Houston",       "lat": 29.6454, "lon": -95.2789,  "station": "KHOU"},
    {"name": "Los Angeles",   "lat": 33.9425, "lon": -118.4081, "station": "KLAX"},
    {"name": "San Francisco", "lat": 37.6189, "lon": -122.3750, "station": "KSFO"},
    {"name": "Austin",        "lat": 30.1944, "lon": -97.6700,  "station": "KAUS"},
]
