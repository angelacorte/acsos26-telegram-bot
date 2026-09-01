# TELEGRAM BOT FOR ACSOS 2026

Telegram bot and optional LLM assistant for [ACSOS 2026](https://2026.acsos.org/).

The Kotlin bot is the always-on Telegram process. The Python service is optional and
serves `/ask` through a fast, source-grounded Ollama model (with an optional Deep
Agents mode) when configured.

## Bot commands

- `/help` - list the available commands.
- `/about` - show conference dates, location, and website.
- `/site` - show the ACSOS 2026 website.
- `/links` - show the ACSOS Linktree page.
- `/group` - show the Telegram group invite link configured with
  `TELEGRAM_GROUP_INVITE_URL`.
- `/tracks` - list ACSOS 2026 tracks.
- `/program` - show the current program status.
- `/program main` or `/maintrack` - show Main Track information.
- `/artifacts`, `/doctoral`, `/posters`, `/tutorials`, `/workshops` - show track information.
- `/sessions` - show timed sessions once they are available in the data file.
- `/venue` - show venue information.
- `/registration` - show the registration link.
- `/social` - show social events once they are available in the data file.
- `/ask <question>` - ask the optional LLM assistant. In private chats,
  free-form messages without a slash command are also sent to the LLM assistant.
  In groups, the bot only answers LLM questions sent with `/ask` or with a
  mention, for example `@acsos_26_bot When is the main track?`. Group and
  supergroup answers are replies to the message from the person who asked.

## Data updates

Conference facts live in `src/main/resources/acsos26/conference.json`.

Keep this file as the single source of truth for both the Kotlin commands and the
Python assistant. The `Refresh conference data` GitHub Action runs once per day
and can also be triggered manually. It reads the public ACSOS 2026 website,
rewrites `conference.json`, and commits only when the scraped data changes.

You can run the same refresh locally:

```bash
python3 scripts/refresh_conference_data.py
```

The LLM service also has a separate bounded URL catalog for live lookups. This
does not rebuild `conference.json` or embeddings; it only refreshes the list of
known ACSOS pages and compact metadata used for ranking:

```bash
python3 scripts/refresh_conference_catalog.py --verbose
```

Site analysis notes for `https://2026.acsos.org`:

- `robots.txt` allows public pages, disallows query URLs, sign-in/sign-up, and
  asks crawlers to use a 2 second crawl delay.
- no `sitemap.xml` is exposed;
- the relevant conference content, navigation, dates, news, tracks, committees,
  and attending pages are available in HTML without Playwright or Selenium;
- the internal search page is a POST form on `/search//all`, but the reliable
  low-traffic method is ranking the bounded local URL catalog discovered from
  navigation and known conference pages.

The refresh script also parses the official tentative Program at a Glance into
structured `program.days[].entries`, including each block's day, time, title,
details, room, and category. Room information is read from the program table on
every refresh. Individual paper-to-session assignments are not published yet;
once they are available, they can be imported into `sessions`.

## Run the Kotlin bot

```bash
export BOT_TOKEN=<telegram-token>
export BOT_USERNAME=acsos_26_bot
export BOT_ACCESS_KEY=<private-user-access-key>
export TELEGRAM_GROUP_INVITE_URL=https://telegram.me/+29z6KbEXBdlkYmE0
export TELEGRAM_STARTUP_GREETING="Hello! The ACSOS 2026 bot is back online."
./gradlew run
```

At startup the bot sends `TELEGRAM_STARTUP_GREETING` once to each chat with
queued messages, then skips the remaining messages sent before the bot started.
Messages sent after startup are processed normally. No chat id configuration is
required.

When `BOT_ACCESS_KEY` is set, a Telegram chat must first send:

```text
/start <private-user-access-key>
```

To enable `/ask`, also set the LLM service URL. Set `LLM_API_KEY` on both the
bot and the Python service if the service should reject direct unauthenticated
calls:

```bash
export LLM_API_URL=http://localhost:8000/ask
export LLM_API_KEY=<service-to-service-key>
```

## Run the LLM service

The Python service exposes:

- `GET /health`
- `POST /ask` with `{"question": "..."}`

Install dependencies and run it locally:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r llm_service/requirements.txt
uvicorn llm_service.app:app --reload --host 0.0.0.0 --port 8000
```

By default the assistant makes a **single grounded model call** rather than
running a tool-calling agent loop: the retrieved local and live sources are put
into the prompt, and the model is instructed to answer only from those sources
or to say the information is not available yet. The assistant answers **only
ACSOS 2026 questions in English**; off-topic questions get a fixed refusal. The
full accepted-paper list is always included in the prompt so the model can answer
topic filters ("papers about AI") by meaning rather than exact wording. Set
`USE_DEEPAGENTS=1` to switch to the tool-calling Deep Agent (which also gets a
`list_accepted_papers` tool for semantic paper filtering), and set
`DISABLE_LLM=true` (or the legacy `DISABLE_DEEPAGENTS=true`) to run deterministic
retrieval only.

The Docker Compose deployment is Gemini-only: set `GEMINI_API_KEY` to the key
created in Google AI Studio. The default model is
`GEMINI_MODEL=gemini-2.5-flash`, and `OLLAMA_ENABLED=false` prevents construction
of a local model client. If Gemini is unavailable, the service falls back to
deterministic answers from the conference data rather than another LLM.

With `USE_DEEPAGENTS=1`, agents also receive bounded arithmetic and JSON/CSV
analysis tools. These tools do not expose Python execution, the shell, files, or
the network.

Latency and reliability defaults:

- `LLM_GENERATION_TIMEOUT_SECONDS=30` caps how long one answer may take
  server-side, so a slow generation falls back instead of hanging the request.
- `LLM_FAILURE_COOLDOWN_SECONDS=60` backs off after a *hard* backend failure
  (e.g. a provider error); it is short so the assistant recovers quickly.
- `LLM_TIMEOUT_COOLDOWN_SECONDS=20` is a brief back-off after a slow generation.
- `LLM_TEMPERATURE=0.1` keeps answers deterministic.

The assistant answers in English only.

### Live ACSOS website retrieval

`/ask` first searches local `conference.json`. For questions with weak local
matches, or questions that explicitly ask for recent/current information, the
service performs a bounded live lookup against ACSOS pages, merges the live
chunks with local context, and asks the model to answer from those sources
(saying so explicitly when the sources do not contain the answer). Simple
high-confidence questions still use deterministic local answers to keep
Telegram latency low.

The live retriever:

- ranks at most `ACSOS_MAX_SEARCH_RESULTS=5` candidate URLs from the catalog;
- fetches at most `ACSOS_MAX_PAGES_PER_QUERY=3` pages per question;
- allows only `2026.acsos.org` and the required `conf.researchr.org` host;
- blocks non-HTTP(S), query URLs, login/signup paths, private/local IPs, and
  redirects outside the allowlist;
- caches extracted pages with ETag/Last-Modified revalidation;
- uses TTLs of 15 minutes for dynamic pages, 6 hours for standard pages, and
  24 hours for mostly static pages by default;
- falls back to local data if the live site is slow or unavailable.

Useful live-search environment variables:

```dotenv
ACSOS_BASE_URL=https://2026.acsos.org
ACSOS_LIVE_SEARCH_ENABLED=true
ACSOS_MAX_SEARCH_RESULTS=5
ACSOS_MAX_PAGES_PER_QUERY=3
ACSOS_MAX_LIVE_TOOL_CALLS=2
ACSOS_CACHE_TTL_DYNAMIC_SECONDS=900
ACSOS_CACHE_TTL_STANDARD_SECONDS=21600
ACSOS_CACHE_TTL_STATIC_SECONDS=86400
ACSOS_CONNECT_TIMEOUT_SECONDS=3
ACSOS_READ_TIMEOUT_SECONDS=7
ACSOS_OVERALL_TIMEOUT_SECONDS=10
ACSOS_USER_AGENT=acsos26-telegram-bot/1.0 (+https://2026.acsos.org)
```

Examples:

- local-only path: `who are the general chairs?`
- live-verification path: `what is the latest registration information?`

## Docker Compose

For local Docker Compose runs, put the keys in a local `.env` file at the
repository root:

```bash
cp .env.example .env
```

Then edit `.env`:

```dotenv
BOT_TOKEN=<telegram-token>
BOT_ACCESS_KEY=<private-user-access-key>
TELEGRAM_GROUP_INVITE_URL=https://telegram.me/+29z6KbEXBdlkYmE0
TELEGRAM_STARTUP_GREETING="Hello! The ACSOS 2026 bot is back online."
LLM_API_KEY=<service-to-service-key>
GEMINI_API_KEY=<google-ai-studio-key>
GEMINI_MODEL=gemini-2.5-flash
GEMINI_TIMEOUT_SECONDS=10
OLLAMA_ENABLED=false
LLM_FAILURE_COOLDOWN_SECONDS=60
LLM_TIMEOUT_COOLDOWN_SECONDS=20
LLM_GENERATION_TIMEOUT_SECONDS=30
LLM_TEMPERATURE=0.1
ACSOS_LIVE_SEARCH_ENABLED=true
```

`LLM_API_KEY` is required by Docker Compose and must have the same value for the
`bot` and `llm` services. The provided `docker-compose.yml` passes it to both
services from `.env`.
On the cluster, store it as a secret and expose it as the `LLM_API_KEY`
environment variable in both containers.

If the LLM service logs `Unsupported upgrade request` and `/ask` returns a 422
empty-body error, rebuild the bot image. The Kotlin client is pinned to HTTP/1.1
to avoid cleartext HTTP/2 upgrade attempts against Uvicorn.

```bash
export BOT_TOKEN=<telegram-token>
export BOT_ACCESS_KEY=<private-user-access-key>
export LLM_API_KEY=<service-to-service-key>
export GEMINI_API_KEY=<google-ai-studio-key>
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

The compose stack starts only the Kotlin bot and the Python service. If Gemini
cannot answer, the Python service falls back to deterministic answers from
`conference.json` without exposing backend error details to Telegram users.

The default `json-file` logging driver works on both Docker Desktop for macOS and
Linux. The supplied production systemd unit overrides it with `journald`.

For production hardening, boot setup, credential rotation, verification, and
rollback procedures, see [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Verification

```bash
./gradlew test
python3 -m py_compile llm_service/app.py
python3 -m py_compile llm_service/conference_live.py scripts/refresh_conference_catalog.py
python3 -m pytest llm_service
```
