# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

Everything runs in a single Docker container; there is no native dev workflow.

- Build & run (rebuilds image, recreates container): `docker compose up -d --build --force-recreate`
- Tail logs: `docker compose logs -f`
- Stop: `docker compose down`
- One-off run with current code (skips the daily loop, useful for iterating): edit `.env` to add `--disable-send` to `CROSSWORD_COMMAND_LINE_ARGUMENTS`, then `docker compose up --build` and inspect output PDFs in `./downloads/`.
- Re-run today's job without rebuilding: `docker exec crossword-sender ./download-crossword.sh $CROSSWORD_COMMAND_LINE_ARGUMENTS` (or `./download-cbc-news.sh`). The `.env` is bind-mounted, so edits take effect without rebuilding — but `main.sh` only re-sources it at the next daily tick.

There is no test suite or linter. CI (`.github/workflows/ci.yaml`) only validates that PR titles follow Conventional Commits and previews the next semver tag — commits/PRs must be Conventional Commits style.

## Architecture

This is a Dockerized cron-loop that produces two PDFs each morning (NYT crossword + CBC news/HA briefing) and ships them to a Kindle (via SMTP) and/or Telegram.

### Runtime shape

`main.sh` is the entrypoint and the only long-running process. It runs the two download scripts once at startup, then loops forever: sleep until `CROSSWORD_DAILY_SEND_TIME` (in `TZ`), `source .env` to pick up edits, run both scripts again. The container has `restart: unless-stopped` but the loop itself does the scheduling — there is no system cron.

### Two pipelines, sharing the same container

1. **Crossword pipeline** (`download-crossword.sh`):
   - Refreshes NYT session cookies in place against `https://a.nytimes.com/svc/nyt/data-layer` (writes back to `cookies.nyt.txt`).
   - For `--version newspaper`: fetches a single PDF from `/svc/crosswords/v2/puzzle/print/<MMMddyy>.pdf`.
   - For `games`/`big`/`southpaw`: looks up the `puzzle_id` from `/svc/crosswords/v3/puzzles.json`, downloads puzzle + answer PDFs, merges them with Ghostscript.
   - On a date range, each day's PDF path is appended to a manifest file and Ghostscript merges them all at the end (unless `--multiple-pdfs`).
   - `exiftool` rewrites author metadata to "The New York Times" before sending.

2. **News pipeline** (`download-cbc-news.sh`):
   - Calls `process_ha.py` → returns an HTML snippet containing a Gemini-generated Home Assistant briefing plus an inline SVG temperature chart built from HA history. Skipped gracefully if `HA_TOKEN`/`GEMINI_API_KEY` aren't set.
   - Calls `process_rss.py` with that snippet + the CBC RSS URL list → returns a full HTML document.
   - Calls `process_html.py` to strip `<img>` tags (Kindle/weasyprint friendliness).
   - `weasyprint` renders the final HTML to PDF.
   - `CBC_RSS_FEEDS` (comma-separated) overrides the default URL list; `CBC_OUTPUT_PATH` overrides the output dir.

### Delivery

Both pipelines reuse the same delivery helpers:
- **Kindle**: `mutt` with the config in `Muttrc`, which is templated from `CROSSWORD_SENDER_EMAIL_*` env vars at container startup. Skipped if `KINDLE_EMAIL_ADDRESS` is unset.
- **Telegram** (news only): `curl` POST to `sendDocument`. Skipped if `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are unset. Note: the news script ignores `--disable-send` for Telegram (per recent commits — Telegram always fires).
- The crossword script requires `KINDLE_EMAIL_ADDRESS`; the news script requires *either* Kindle *or* Telegram credentials.

### Container layout

- Base: Alpine + a Python venv at `/opt/venv` (weasyprint, bs4, lxml, feedparser, google-genai, python-dotenv, google-* auth libs).
- WORKDIR: `/crosswords`. Scripts live there; `tmp/` is used for intermediate HTML files.
- Volumes (from `docker-compose.yml`):
  - `${CROSSWORD_DOWNLOADS_PATH} → /crosswords/downloads` (output PDFs)
  - `${NYT_COOKIES_PATH} → /crosswords/cookies.nyt.txt` (mutated in place by the cookie refresh)
  - `./.env → /crosswords/.env` (so edits land in the container without rebuilding)
  - `./process_ha.py → /crosswords/process_ha.py` (live-edit the HA logic without rebuilding)
- `network_mode: host` so the container can reach a LAN Home Assistant URL.
- Runs as the unprivileged `crossword` user.

### Things that aren't obvious

- `setup_gmail_auth.py` is a standalone helper for generating a Gmail OAuth `token.json` — it isn't wired into the runtime container and isn't `COPY`'d in the Dockerfile. It's leftover/optional tooling.
- `StoneCrownOfCashel/` is unrelated static content (audio/PDF/midi). Don't touch unless asked.
- `cookies.nyt.txt` is rewritten on every run — a pulled fresh cookies file from your browser is the right move when auth breaks, not a code fix.
- The `--disable-send` flag prevents the crossword email send and the *Kindle* news send, but **not** the Telegram news send.
