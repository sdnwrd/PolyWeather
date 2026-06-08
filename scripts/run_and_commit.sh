#!/usr/bin/env bash
# Render Cron entry point (morning scan, 04 UTC): sync to latest main, run
# the scan, commit any updated state back to GitHub. The container is
# ephemeral, so committing back to the repo is how signals.csv, the per-
# market JSON snapshots, and calibration.json survive between runs.
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN env var is required}"
: "${GH_REPO:?GH_REPO env var is required (e.g. sdnwrd/rainsignal)}"

REPO_URL="https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git"

git config --global user.name  "rainsignal-bot"
git config --global user.email "rainsignal-bot@users.noreply.github.com"

# Render's build image does not preserve the origin remote, so (re)create it
# with the token-authenticated URL — set-url alone fails with "No such remote".
git remote remove origin 2>/dev/null || true
git remote add origin "${REPO_URL}"
git fetch origin main
git reset --hard origin/main

python main.py --now

# Stage everything under data/ that might have changed: signals.csv (journal),
# data/markets/*.json (per-market snapshots), data/calibration.json (per-city
# sigma). git add is a no-op if files are unchanged.
mkdir -p data/markets
git add -A data/

if ! git diff --cached --quiet; then
  git commit -m "log: morning scan $(date -u +%Y-%m-%d)"
  git push origin HEAD:main
  echo "pushed updated state"
else
  echo "no state changes to commit"
fi
