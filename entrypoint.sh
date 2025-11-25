#!/bin/sh

PORT=${PORT:-8001}

echo "Starting Telegram Bridge on port $PORT"

exec uvicorn app.main:app --host 0.0.0.0 --port $PORT

