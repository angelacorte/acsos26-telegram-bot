#!/bin/sh
docker service create \
    -e BOT_TOKEN="$BOT_TOKEN" \
    -e TELEGRAM_STARTUP_GREETING="${TELEGRAM_STARTUP_GREETING:-Hello! The ACSOS 2026 bot is back online.}" \
    --name acsos26-telegram-bot \
    angelacorte/acsos26-telegram-bot:latest
while true; do
    docker pull angelacorte/acsos26-telegram-bot:latest
    docker service update --image angelacorte/acsos26-telegram-bot:latest acsos26-telegram-bot
    sleep 1m
done
