#!/bin/bash
# Настройка автоподтягивания бота FuelWatch на VPS (systemd-таймер).
# Запуск: bash scripts/setup_autoupdate.sh   (нужен root)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите от root: sudo bash $0" >&2
    exit 1
fi

REPO_DIR=/root/fuel-watch
SERVICE=fuelwatch-bot

if [ ! -d "$REPO_DIR/.git" ]; then
    echo "Репозиторий не найден: $REPO_DIR" >&2
    exit 1
fi

# 1) скрипт обновления: pull и перезапуск бота, если были изменения
cat > /usr/local/bin/fuelwatch-update.sh <<'EOF'
#!/bin/bash
cd /root/fuel-watch || exit 1
before=$(git rev-parse HEAD)
git pull --ff-only -q origin main
after=$(git rev-parse HEAD)
if [ "$before" != "$after" ]; then
    systemctl restart fuelwatch-bot
    echo "бот перезапущен: $before -> $after"
fi
EOF
chmod +x /usr/local/bin/fuelwatch-update.sh

# 2) одноразовый сервис
cat > /etc/systemd/system/fuelwatch-update.service <<'EOF'
[Unit]
Description=FuelWatch auto-update
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/fuelwatch-update.sh
EOF

# 3) таймер — каждые 5 минут
cat > /etc/systemd/system/fuelwatch-update.timer <<'EOF'
[Unit]
Description=FuelWatch auto-update timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now fuelwatch-update.timer

echo "Готово. Таймер активен:"
systemctl list-timers fuelwatch-update.timer --no-pager
