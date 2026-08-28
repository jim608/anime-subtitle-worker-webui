#!/bin/sh
set -eu

SERVICE="${SERVICE:-anime-subtitle-worker-webui}"
WEBUI_URL="${WEBUI_URL:-http://127.0.0.1:8765}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

docker compose -f "$COMPOSE_FILE" --project-directory "$SCRIPT_DIR" build "$SERVICE"
docker compose -f "$COMPOSE_FILE" --project-directory "$SCRIPT_DIR" up -d --no-build --force-recreate "$SERVICE"

attempt=0
while ! curl -fsS "$WEBUI_URL/api/status?lite=true" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "WebUI did not become healthy within 60 seconds." >&2
        docker compose -f "$COMPOSE_FILE" --project-directory "$SCRIPT_DIR" logs --tail=80 "$SERVICE" >&2
        exit 1
    fi
    sleep 1
done

status_json="$(curl -fsS "$WEBUI_URL/api/status?lite=true")"
fingerprint="$(printf '%s\n' "$status_json" | sed -n 's/.*"webui_fingerprint":"\([^"]*\)".*/\1/p')"
expected_app_sha="$(sha256sum "$SCRIPT_DIR/app.py" | awk '{print substr($1, 1, 12)}')"
live_app_sha="$(printf '%s\n' "$status_json" | sed -n 's/.*"app.py":{"sha256":"\([^"]*\)".*/\1/p')"
if [ -z "$live_app_sha" ] || [ "$live_app_sha" != "$expected_app_sha" ]; then
    echo "WebUI source verification failed. expected_app_sha=$expected_app_sha live_app_sha=${live_app_sha:-missing}" >&2
    exit 1
fi
if printf '%s\n' "$status_json" | grep -q '"overall":"error"'; then
    echo "WebUI started, but health checks report an error: $status_json" >&2
    exit 1
fi

queue_json="$(curl -fsS "$WEBUI_URL/api/queue?limit=1")"
if printf '%s\n' "$queue_json" | grep -q '"error"'; then
    echo "WebUI queue database verification failed: $queue_json" >&2
    exit 1
fi

echo "WebUI update complete. fingerprint=${fingerprint:-unknown}"
