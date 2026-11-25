#!/usr/bin/env bash
# Обновить программу: новый архив elomer_server.tar.gz положить в /opt и запустить этот скрипт.
# Данные на сервере (базы, проекты, загрузки) не трогаются.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCHIVE="${1:-/opt/elomer_server.tar.gz}"
[ -f "$ARCHIVE" ] || { echo "Нет архива $ARCHIVE"; exit 1; }
tmp=$(mktemp -d)
tar -xzf "$ARCHIVE" -C "$tmp"
rm -rf "$ROOT/backend/app" "$ROOT/backend/tools" "$ROOT/frontend"
cp -r "$tmp/backend/app" "$tmp/backend/tools" "$ROOT/backend/"
cp "$tmp/backend/requirements.txt" "$ROOT/backend/"
cp -r "$tmp/frontend" "$ROOT/"
cp "$tmp/deploy/Dockerfile" "$tmp/deploy/docker-compose.yml" "$tmp/deploy/"*.sh "$ROOT/deploy/"
rm -rf "$tmp"
cd "$ROOT/deploy"
docker compose up -d --build
echo "Обновлено."
