#!/bin/sh
docker service create -e BOT_TOKEN=$BOT_TOKEN --name acsos26-telegram-bot angelacorte/acsos26-telegram-bot:latest
while true; do
    docker pull angelacorte/acsos26-telegram-bot:latest
    docker service update --image angelacorte/acsos26-telegram-bot:latest acsos26-telegram-bot
    sleep 1m
done
