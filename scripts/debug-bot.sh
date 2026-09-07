#!/usr/bin/env bash
# Launch the bot against a throwaway Telegram bot, using the current working tree.
#
# Credentials live in .env.debug (gitignored). Create it from .env.debug.example and put the
# token BotFather gave you in BOT_TOKEN.
#
#   ./scripts/debug-bot.sh              bot + local assistant, so /ask works
#   ./scripts/debug-bot.sh --no-llm     bot only; /ask reports the assistant is unavailable
#   ./scripts/debug-bot.sh --refresh    re-scrape conference.json first
#
# Ctrl+C stops both processes.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=${ENV_FILE:-.env.debug}
WITH_LLM=1
REFRESH=0
for arg in "$@"; do
  case "$arg" in
    --no-llm) WITH_LLM=0 ;;
    --refresh) REFRESH=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [[ ! -f $ENV_FILE ]]; then
  echo "error: $ENV_FILE not found. Copy .env.debug.example to $ENV_FILE and set BOT_TOKEN." >&2
  exit 1
fi
set -a; . "./$ENV_FILE"; set +a
: "${BOT_TOKEN:?set BOT_TOKEN in $ENV_FILE}"

# The bot username decides which @mention it answers in groups. It defaults to the PRODUCTION
# name in conference.json, so ask Telegram who this token actually belongs to and use that.
resolved=$(
  curl -fsS --max-time 10 "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>/dev/null |
    sed -n 's/.*"username":"\([^"]*\)".*/\1/p'
) || true
if [[ -z ${resolved:-} ]]; then
  echo "error: Telegram rejected BOT_TOKEN (or the network is down). Check $ENV_FILE." >&2
  exit 1
fi
if [[ ${BOT_USERNAME:-} != "$resolved" ]]; then
  echo "note: BOT_USERNAME -> @$resolved (was '${BOT_USERNAME:-unset}')"
  export BOT_USERNAME="$resolved"
fi

if (( REFRESH )); then
  echo "== refreshing conference.json =="
  python3 scripts/refresh_conference_data.py
fi

llm_pid=""
cleanup() {
  [[ -n $llm_pid ]] && kill "$llm_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if (( WITH_LLM )); then
  python=$([[ -x .venv/bin/python ]] && echo .venv/bin/python || echo python3)
  # The assistant reads conference.json once at import, so it must start after --refresh.
  echo "== assistant on :8000 ($python) =="
  $python -m uvicorn llm_service.app:app --host 127.0.0.1 --port 8000 --log-level warning &
  llm_pid=$!
  for _ in $(seq 1 40); do
    curl -fsS --max-time 1 http://127.0.0.1:8000/health >/dev/null 2>&1 && break
    kill -0 "$llm_pid" 2>/dev/null || { echo "error: assistant died on startup" >&2; exit 1; }
    sleep 0.5
  done
  mode=$(curl -fsS http://127.0.0.1:8000/health | sed -n 's/.*"mode":"\([^"]*\)".*/\1/p')
  echo "   assistant ready (mode: ${mode:-unknown})"
else
  unset LLM_API_URL
  echo "== assistant skipped (--no-llm): /ask will report it is unavailable =="
fi

echo "== bot @$BOT_USERNAME -- talk to it at https://t.me/$BOT_USERNAME (Ctrl+C to stop) =="
./gradlew --console=plain -q run
