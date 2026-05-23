#!/bin/bash
# Запускать на сервере DigitalOcean (Ubuntu 22.04)
# Команда: bash deploy_digitalocean.sh

set -e

echo "=== Установка зависимостей ==="
apt update -y
apt install -y python3 python3-pip git

echo "=== Копирование файлов ==="
mkdir -p /opt/tao_bot
cp -r . /opt/tao_bot/
cd /opt/tao_bot

echo "=== Установка Python пакетов ==="
pip3 install -r requirements.txt

echo "=== Создание .env ==="
if [ ! -f /opt/tao_bot/.env ]; then
    echo "Создай .env файл вручную:"
    echo "  nano /opt/tao_bot/.env"
    echo "  Содержимое:"
    echo "  TELEGRAM_TOKEN=..."
    echo "  CHAT_ID=..."
    echo "  ANTHROPIC_API_KEY=..."
fi

echo "=== Создание systemd сервиса ==="
cat > /etc/systemd/system/tao-bot.service << 'EOF'
[Unit]
Description=TAO Signal Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/tao_bot
EnvironmentFile=/opt/tao_bot/.env
ExecStart=/usr/bin/python3 /opt/tao_bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tao-bot
systemctl start tao-bot

echo "=== Готово! ==="
echo "Статус: systemctl status tao-bot"
echo "Логи:   journalctl -u tao-bot -f"
