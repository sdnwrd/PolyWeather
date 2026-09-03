# PolyWeather

Monitors Polymarket temperature-bracket markets for 8 US cities, compares them
to the NWS daily-high forecast, and pings a Telegram chat when a bracket looks
mispriced. **It never trades — it only signals.**

## The idea

A market price is an implied probability. To judge it you need an independent probability for the
same event. The US National Weather Service publishes a free forecast, so the question is whether
the prices drift away from it.

The forecast is a single number, not an interval, so it gets treated as the mean of a normal
distribution. That is what makes a temperature bracket integrable into a probability at all. The
fixed standard deviation is a simplification, and an honest one: I have not calibrated it, so I do
not claim to have.

Three filters, not one. A high expected value alone is meaningless — it also appears at prices near
zero, where any deviation looks enormous. Only expected value, absolute price and a minimum
probability together separate signal from noise. The filters were more work than the maths.

**It never trades.** Automating that would mean trusting money to a model whose calibration I cannot
demonstrate.

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
NWS_USER_AGENT=polyweather/1.0 (you@example.com)
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

## Hosting on Render (recommended)

Render Cron Jobs fire punctually at the configured UTC time — GitHub Actions
cron drifts 1-3h, which loses the early-bird edge before US trading desks
wake up. The repo ships a `render.yaml` Blueprint that wires everything up
for **$1/month**.

The cron is set to **04:00 UTC** (= 06:00 CEST / 05:00 CET) — after the
00 UTC GFS model run lands in NWS forecasts (~03:00 UTC) and before the
US wakes up. Markets close at 12:00 UTC, so this leaves ~8h headroom.

Render Cron containers are ephemeral (no persistent disk), so the wrapper
script `scripts/run_and_commit.sh` syncs `data/signals.csv` back to the
GitHub repo after each run. The repo is the database.

Steps:

1. Push this repo to GitHub.
2. Create a **fine-grained Personal Access Token** at
   `https://github.com/settings/personal-access-tokens/new`:
   - Resource owner: your user
   - Repository access: **Only select repositories → rainsignal**
   - Repository permissions → **Contents: Read and write**
   - Copy the token — you'll paste it in step 4.
3. Sign up at `render.com` with your GitHub account.
4. **New + → Blueprint** → select the `rainsignal` repo. Render reads
   `render.yaml` and pre-creates the cron job. It will prompt for the four
   secrets (`sync: false` in render.yaml):
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `NWS_USER_AGENT` (e.g. `weather-signal-bot/1.0 (you@example.com)`)
   - `GH_TOKEN` (the fine-grained PAT from step 2)
5. Deploy. The first build runs immediately; the cron fires daily at 04:00 UTC.
   You can also trigger it on demand via **Render dashboard → Trigger run**.

Each run takes ~30s. The `signals.csv` commit-back triggers a Render rebuild,
so tomorrow's cron starts from the freshest CSV automatically.

## Hosting on GitHub Actions (free fallback)

`.github/workflows/daily-scan.yml` is kept for **manual one-off runs** from
the GitHub UI (**Actions → manual-scan → Run workflow**). The scheduled
trigger has been removed because GitHub Actions cron drifts 1-3h, which is
unacceptable for time-sensitive trading signals.

If you don't care about the early-bird edge (e.g. you're only validating
that the model works at all), re-enable the schedule by adding
`schedule: - cron: "30 5 * * *"` back to the workflow's `on:` block, and
make sure the same three secrets are set under
**Settings → Secrets and variables → Actions**.

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
