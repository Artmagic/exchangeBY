#!/usr/bin/env bash
# Авто-деплой: тянет свежий код с GitHub и перезапускает бота ТОЛЬКО если
# на origin/main появились новые коммиты. Запускается по cron раз в пару минут.
# Ставится один раз (см. README-деплой ниже), после этого деплой = git push.
cd /root/exchangeBY || exit 0

git fetch -q origin main || exit 0
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "$(date '+%F %T') новый коммит $REMOTE — обновляюсь"
    git reset --hard -q origin/main
    systemctl restart kursbot
fi
