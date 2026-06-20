#!/usr/bin/env bash

set -eo pipefail

SCRIPT_PATH="$(dirname "$(readlink -f "$0")")"

run_daily() {
  # Full daily set: crossword + news, plus a D&D page (sent even with no move).
  # D&D is optional/experimental: never let it take down crossword/news.
  ${SCRIPT_PATH}/download-crossword.sh ${CROSSWORD_COMMAND_LINE_ARGUMENTS}
  ${SCRIPT_PATH}/download-cbc-news.sh ${CROSSWORD_COMMAND_LINE_ARGUMENTS}
  ${SCRIPT_PATH}/download-dnd.sh ${CROSSWORD_COMMAND_LINE_ARGUMENTS} || true
}

#### BEGIN MAIN EXECUTION

POLL_INTERVAL="${DND_POLL_INTERVAL_SECONDS:-3600}"
DAILY_TIME="${CROSSWORD_DAILY_SEND_TIME:-08:00}"

# Run the full set once at startup.
run_daily
last_daily="$(date +%F)"

echo "D&D inbox polled every ${POLL_INTERVAL}s; crossword + news sent daily at ${DAILY_TIME} ${TZ}."

while true; do
  sleep "${POLL_INTERVAL}"

  # Re-read .env so edits (send time, args, poll interval) apply without a rebuild.
  source ${SCRIPT_PATH}/.env

  # Frequent D&D poll: fetch any reply while its Amazon share link is still fresh
  # (the links expire within a day) and advance the game. --poll only renders and
  # sends a page when a move was actually applied, so idle hours stay silent.
  ${SCRIPT_PATH}/download-dnd.sh --poll ${CROSSWORD_COMMAND_LINE_ARGUMENTS} || true

  # Daily crossword + news (and a fresh full D&D page) once per day at/after DAILY_TIME.
  today="$(date +%F)"
  now_hm="$(date +%H:%M)"
  if [ "${today}" != "${last_daily}" ] && [[ "${now_hm}" > "${DAILY_TIME}" || "${now_hm}" == "${DAILY_TIME}" ]]; then
    echo "Daily send time reached (${now_hm} ${TZ})."
    run_daily
    last_daily="${today}"
  fi
done
