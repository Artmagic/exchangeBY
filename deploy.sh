#!/usr/bin/env bash
# Разворачивает бота на Ubuntu-сервере. Запускать от root ИЗ папки проекта:
#   cd /root/exchangeBY && bash deploy.sh
set -e
cd "$(dirname "$0")"
WORKDIR="$(pwd)"

# не задавать интерактивные вопросы при установке пакетов (keyboard/console-setup)
export DEBIAN_FRONTEND=noninteractive

echo "==> [1/5] Системные пакеты"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip

echo "==> [2/5] venv + зависимости Python"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "==> [3/5] Playwright Chromium + системные библиотеки"
.venv/bin/playwright install --with-deps chromium

echo "==> [4/5] swap 2 ГБ (страховка на 1 ГБ ОЗУ)"
if ! swapon --show | grep -q '/swapfile'; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "    swap подключён"
else
    echo "    swap уже есть — пропуск"
fi

echo "==> [5/5] systemd-сервис kursbot"
sed "s|__WORKDIR__|${WORKDIR}|g" kursbot.service > /etc/systemd/system/kursbot.service
systemctl daemon-reload
systemctl enable --now kursbot
sleep 2
systemctl --no-pager status kursbot | head -n 8

echo
echo "==> Готово. Логи в реальном времени:  journalctl -u kursbot -f"
