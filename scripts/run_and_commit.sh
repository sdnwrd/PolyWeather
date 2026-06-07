#!/usr/bin/env bash
# Render Cron entry point: sync to latest main, run the scan, commit the
# updated paper-trade log back to GitHub. The container is ephemeral, so
# committing back to the repo is how signals.csv survives between runs.
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN env var is required}"
: "${GH_REPO:?GH_REPO env var is required (e.g. sdnwrd/rainsignal)}"

REPO_URL="https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git"

git config --global user.name  "rainsignal-bot"
git config --global user.email "rainsignal-bot@users.noreply.github.com"

# The container starts from the image built at last deploy. If commits landed
# on main since then (e.g. yesterday's CSV push), fetch + hard-reset to pick
# them up. Any local changes in the container are throwaway by design.
git remote set-url origin "${REPO_URL}"
git fetch origin main
git reset --hard origin/main

python main.py --now

if [[ -n "$(git status --porcelain data/signals.csv 2>/dev/null)" ]]; then
  git add data/signals.csv
  git commit -m "log: signals + backfill $(date -u +%Y-%m-%d)"
  git push origin HEAD:main
  echo "pushed updated signals.csv"
else
  echo "no signals.csv changes to commit"
fi
