# ЭЛОМЕР на сервере: интерфейс собирается в первом шаге, в итоговом образе — только Python и готовые файлы.
# образы берутся с зеркала Google: Docker Hub ограничивает скачивание с российских серверов
FROM mirror.gcr.io/library/node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM mirror.gcr.io/library/python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY backend/tools backend/tools
COPY --from=web /web/dist frontend/dist
ENV PYTHONUNBUFFERED=1 TZ=Europe/Moscow
EXPOSE 8000
# один процесс: фоновые загрузки (города, пригороды, справочники) живут в памяти сервера
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
