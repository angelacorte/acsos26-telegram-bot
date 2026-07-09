# TELEGRAM BOT FOR ACSOS 2026

Telegram bot and optional LLM assistant for [ACSOS 2026](https://2026.acsos.org/).

The Kotlin bot is the always-on Telegram process. The Python service is optional and
serves `/ask` through Deep Agents plus Ollama when configured.

## Bot commands

- `/help` - list the available commands.
- `/about` - show conference dates, location, and website.
- `/tracks` - list ACSOS 2026 tracks.
- `/program` - show the current program status.
- `/program main` or `/maintrack` - show Main Track information.
- `/artifacts`, `/doctoral`, `/posters`, `/tutorials`, `/workshops` - show track information.
- `/sessions` - show timed sessions once they are available in the data file.
- `/venue` - show venue information.
- `/registration` - show the registration link.
- `/social` - show social events once they are available in the data file.
- `/ask <question>` - ask the optional LLM assistant.

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

When the final schedule is published, the refresh script should be extended to
parse the new schedule section into `sessions` with day, time, room, track id,
and paper titles. Until then, the bot explicitly says that session times and
rooms are not available.

## Run the Kotlin bot

```bash
export BOT_TOKEN=<telegram-token>
export BOT_USERNAME=acsos_26_bot
export BOT_ACCESS_KEY=<private-user-access-key>
./gradlew run
```

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

By default it uses `DEEPAGENTS_MODEL=ollama:gpt-oss:20b`. Pull the model before
using Deep Agents with Ollama:

```bash
ollama pull gpt-oss:20b
```

Set `DISABLE_DEEPAGENTS=true` to run deterministic retrieval only.

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
LLM_API_KEY=<service-to-service-key>
DEEPAGENTS_MODEL=ollama:gpt-oss:20b
OLLAMA_MODEL=gpt-oss:20b
```

`LLM_API_KEY` must have the same value for the `bot` and `llm` services. The
provided `docker-compose.yml` already passes it to both services from `.env`.
On the cluster, store it as a secret and expose it as the `LLM_API_KEY`
environment variable in both containers.

If the LLM service logs `Unsupported upgrade request` and `/ask` returns a 422
empty-body error, rebuild the bot image. The Kotlin client is pinned to HTTP/1.1
to avoid cleartext HTTP/2 upgrade attempts against Uvicorn.

If `ollama pull gpt-oss:20b` says the model requires a newer Ollama version,
recreate the Ollama container. The compose file pins `ollama/ollama:0.31.1`,
which is new enough for current GPT-OSS model manifests.

Docker Compose also starts an `ollama-pull` one-shot service. It waits for
Ollama, pulls `OLLAMA_MODEL`, and only then lets the LLM service start. If the
model is already in the `ollama` volume, this step is effectively a no-op.

```bash
export BOT_TOKEN=<telegram-token>
export BOT_ACCESS_KEY=<private-user-access-key>
export LLM_API_KEY=<service-to-service-key>
docker compose up --build
```

The compose stack starts the Kotlin bot, the Python service, Ollama, and the
one-shot model puller. If the model cannot run because the host is out of memory,
the Python service falls back to deterministic answers from `conference.json`
without exposing backend error details to Telegram users.

## Verification

```bash
./gradlew test
python3 -m py_compile llm_service/app.py
```
