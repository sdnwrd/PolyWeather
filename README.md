# weather-signal-bot

Monitors Polymarket temperature-bracket markets for 8 US cities, compares them
to the NWS daily-high forecast, and pings a Telegram chat when a bracket looks
mispriced. **It never trades — it only signals.**

## How it works

1. **07:30 local** the scheduler runs `main.py` → `run()`.
2. For each city: fetch the predicted daily high from the NWS hourly forecast.
3. Search Polymarket's Gamma API for `<city> temperature` markets resolving today.
4. Pull the live YES price for every matching bracket from the CLOB.
5. Score each bracket with a normal distribution centred on the forecast (σ = 2°F).
6. Send a Telegram message for every bracket where
   `EV ≥ 8x`, `price ≤ 15¢`, and `true_prob ≥ 5%` — grouped by city.
7. If no signals fire, send a one-line "scan complete" summary.

## Setup

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Create a Telegram bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot`, follow the prompts, and copy the **HTTP API token** it gives you.
3. Start a chat with your new bot (send it any message — `hi` is fine).

### 3. Get your personal chat ID

1. Message **@userinfobot** on Telegram. It replies with your numeric ID.
2. Alternatively: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser after
   messaging your bot — `result[0].message.chat.id` is your chat ID.

### 4. Configure `.env`

```bash
cp .env.example .env
```

Then edit `.env`:

```dotenv
TELEGRAM_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=987654321
NWS_USER_AGENT=weather-signal-bot/1.0 (you@example.com)
```

NWS requires a descriptive `User-Agent` with a contact email — they will block
requests that omit it.

### 5. Run

```bash
# scheduled mode — runs daily at 07:30 local
python main.py

# one-shot scan right now (for testing)
python main.py --now
```

You can also exercise the API modules directly:

```bash
python forecast.py   # prints today's forecast high per city
python markets.py    # prints discovered Polymarket brackets + prices per city
```

## Paper trading + backfill

Every signal that fires gets appended to `data/signals.csv` with the model's
predicted true probability, the market price, and the EV. The next morning, the
backfill step looks up the actual recorded high at the resolution station (NWS
observation for KORD, KMIA, KBKF, KATL, KHOU, KLAX, KSFO, KAUS) and marks each
row as `WIN` or `LOSS`. After 4–8 weeks you'll have a real dataset to answer
"does this model have edge?" before committing capital.

Run manually:

```bash
python paper.py --backfill   # fill in actual highs for resolved days
python paper.py --summary    # print win-rate + avg unit ROI
```

The daily `python main.py --now` automatically appends new signals AND runs
backfill on resolved rows — no separate step needed.

## Hosting on GitHub Actions (recommended, free)

The repo includes `.github/workflows/daily-scan.yml` which runs the bot once a
day at **05:30 UTC** (= 07:30 CEST in summer, 06:30 CET in winter) and commits
the updated `data/signals.csv` back to the repo.

Steps:

1. Push this repo to GitHub.
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `NWS_USER_AGENT` (e.g. `weather-signal-bot/1.0 (you@example.com)`)
3. **Settings → Actions → General → Workflow permissions → "Read and write permissions"** (so the job can commit the CSV back).
4. Done. The first run kicks off at the next 05:30 UTC. You can also trigger it
   manually any time via **Actions → daily-scan → Run workflow**.

Each run takes ~30 seconds; with the GitHub free tier (2000 private-repo
minutes/month) you'll use <20 minutes/month. The repo doubles as a
browsable, version-controlled record of every signal.

## Hosting on Render (paid alternative)

Render Cron Jobs work too, but cost ~$1/month per job (no free tier for cron).
If you want to use it anyway: create a **Cron Job** service, point it at this
repo, set the schedule to `30 5 * * *`, command `python main.py --now`, add the
same three env vars, and attach a small persistent disk for `data/signals.csv`.
GitHub Actions is simpler and free, so prefer that unless you have a reason.

## Tuning

All thresholds live in `config.py`:

| Knob | Default | Meaning |
|------|---------|---------|
| `EV_THRESHOLD` | `8.0` | Minimum `true_prob / market_price` ratio to fire a signal |
| `MAX_MARKET_PRICE` | `0.15` | Skip brackets priced above 15¢ |
| `MIN_TRUE_PROB` | `0.05` | Skip brackets with <5% modelled probability |
| `FORECAST_SIGMA` | `2.0` | Std-dev of the forecast-error distribution, in °F |
| `RUN_TIME` | `"07:30"` | Daily run time (local) |

The scorer also restricts itself to brackets within 3°F of the forecast — this
implements the "cover 2–3 adjacent brackets as a hedge" strategy.
