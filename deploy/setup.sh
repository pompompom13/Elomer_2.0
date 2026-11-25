#!/usr/bin/env bash
# Первая установка ЭЛОМЕРа на чистый сервер Ubuntu. Запуск: bash /opt/elomer/deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "== ЭЛОМЕР: установка на сервер =="

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Ставлю Docker из репозитория Ubuntu (2–3 минуты)…"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 ufw cron     || { echo "Не получилось из репозитория Ubuntu, пробую установщик Docker…"; curl -fsSL https://get.docker.com | sh; }
  systemctl enable --now docker
fi

read -rp "Адрес сайта (например elomer.company.ru). Пусто — открывать по IP сервера: " DOMAIN
read -rp "Логин первого пользователя: " LOGIN
while true; do
  read -rsp "Пароль (не короче 10 символов): " PASS; echo
  [ ${#PASS} -ge 10 ] && break
  echo "Слишком короткий пароль."
done

HASH=$(docker run --rm mirror.gcr.io/library/caddy:2 caddy hash-password --plaintext "$PASS")
echo "$LOGIN $HASH" > users.caddy

if [ -z "$DOMAIN" ]; then
  # внутренний адрес сервера (hostname -I) у облачных хостингов часто не совпадает с внешним — спрашиваем
  read -rp "Внешний IP-адрес сервера (из письма хостинга, например 203.0.113.10): " IP
  while ! [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; do
    read -rp "Нужен IP вида 203.0.113.10: " IP
  done
  SITE="https://$IP"
  TLS="tls internal"   # сертификат самого сервера: браузер один раз попросит подтвердить вход
  echo "Сайт будет доступен по адресу https://$IP"
else
  SITE="$DOMAIN"
  TLS=""               # бесплатный сертификат Let's Encrypt, домен уже должен указывать на этот сервер
  echo "Сайт будет доступен по адресу https://$DOMAIN"
fi

GLOBAL=""
if [ -z "$DOMAIN" ]; then
  # браузер не сообщает имя сайта при входе по IP — без этого Caddy не знает, какой сертификат отдать
  GLOBAL="{
	default_sni $IP
}
"
fi

cat > Caddyfile <<EOF
$GLOBAL
$SITE {
	$TLS
	encode gzip
	basic_auth {
		import users.caddy
	}
	request_body {
		max_size 300MB
	}
	reverse_proxy app:8000
}
EOF

# брандмауэр: снаружи открыты только SSH и сайт
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null || true
fi

# ежедневная копия данных в /root/elomer_backups (хранится 14 дней)
mkdir -p /root/elomer_backups
DATA_DIR="$(cd .. && pwd)/backend/data"
CRON="15 3 * * * tar -czf /root/elomer_backups/data_\$(date +\\%F).tar.gz -C $DATA_DIR . && find /root/elomer_backups -name 'data_*.tar.gz' -mtime +14 -delete"
( crontab -l 2>/dev/null | grep -v elomer_backups; echo "$CRON" ) | crontab -

echo "Собираю и запускаю (первый раз 5–10 минут)…"
docker compose up -d --build

echo
echo "Готово. Откройте адрес выше и войдите: логин $LOGIN и ваш пароль."
echo "Добавить коллегу:   bash $(pwd)/add_user.sh"
echo "Обновить программу: bash $(pwd)/update.sh"
