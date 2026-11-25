#!/usr/bin/env bash
# Добавить пользователя или сменить пароль. Удалить — убрать его строку из deploy/users.caddy и запустить этот скрипт с пустым логином.
set -euo pipefail
cd "$(dirname "$0")"
read -rp "Логин (пусто — только перечитать список): " LOGIN
if [ -n "$LOGIN" ]; then
  read -rsp "Пароль (не короче 10 символов): " PASS; echo
  [ ${#PASS} -ge 10 ] || { echo "Слишком короткий пароль."; exit 1; }
  HASH=$(docker run --rm mirror.gcr.io/library/caddy:2 caddy hash-password --plaintext "$PASS")
  grep -v "^$LOGIN " users.caddy > users.tmp || true
  echo "$LOGIN $HASH" >> users.tmp
  mv users.tmp users.caddy
fi
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
echo "Пользователи: $(cut -d' ' -f1 users.caddy | tr '\n' ' ')"
