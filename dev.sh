#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export ROOM_CALENDAR_CONFIG="$PWD/.private/local/config.json"
.venv/bin/python manage.py migrate --noinput
exec .venv/bin/python manage.py runserver 127.0.0.1:8000
