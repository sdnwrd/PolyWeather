#!/usr/bin/env bash
# Render Cron entry point (intraday METAR veto, 18 UTC): sync to latest main,
# run the intraday veto check, commit any updated snapshot state back. The
# intraday job doesn't change signals.csv; it only appends a snapshot per
# (city, today) JSON file when METAR data is captured.
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN env var is required}"
: "${GH_REPO:?GH_REPO env var is required (e.g. sdnwrd/rainsignal)}"

REPO_URL="https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git"

git config --global user.name  "rainsignal-bot"
git config --global user.email "rainsignal-bot@users.noreply.github.com"

git remote remove origin 2>/dev/null || true
git remote add origin "${REPO_URL}"
git fetch origin main
git reset --hard origin/main

python -m intraday

mkdir -p data/markets
git add -A data/

if git diff --cached --quiet; then
  echo "no state changes to commit"
  exit 0
fi

git commit -m "log: intraday veto $(date -u +%Y-%m-%d)"

for attempt in 1 2 3; do
  if git pull --rebase --autostash origin main; then
    if git push origin HEAD:main; then
      echo "pushed updated intraday state (attempt $attempt)"
      exit 0
    fi
    echo "push rejected on attempt $attempt — retrying"
  else
    echo "rebase failed on attempt $attempt — aborting"
    git rebase --abort 2>/dev/null || true
    exit 1
  fi
done

echo "push failed after 3 attempts"
exit 1
