#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
WORK_DIR="${WORK_DIR:-/mnt/user/appdata/anime-subtitle-worker/work}"
CONFIG_FILE="${CONFIG_FILE:-/mnt/user/appdata/anime-subtitle-worker/config.yaml}"
WORKER_CONTAINER="${WORKER_CONTAINER_NAME:-anime-subtitle-worker}"
WEBUI_CONTAINER="${WEBUI_CONTAINER_NAME:-anime-subtitle-worker-webui}"
WORKER_IMAGE="${WORKER_IMAGE:-anime-subtitle-worker:latest}"
WEBUI_IMAGE="${WEBUI_IMAGE:-anime-subtitle-worker-webui:latest}"
UPDATE_LOCK_DIR="${UPDATE_LOCK_DIR:-$WORK_DIR/deployment_update.lock}"
HOLD_FILE="$WORK_DIR/deployment_hold.json"
AI_CONTROL_FILE="$WORK_DIR/ai_control.json"

usage() {
  echo "Usage: $0 <deployment-id> [--preview|--apply]" >&2
  echo "Recovers only a dead safe-update-stack process that stopped before the verified backup boundary." >&2
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ] || [ -z "$1" ]; then
  usage
  exit 2
fi
DEPLOYMENT_ID="$1"
MODE="${2:---preview}"
case "$MODE" in
  --preview|--apply) ;;
  *) usage; exit 2 ;;
esac
case "$DEPLOYMENT_ID" in
  20??????T??????Z-[0-9]*) ;;
  *) echo "Invalid deployment id: $DEPLOYMENT_ID" >&2; exit 2 ;;
esac
DEPLOYMENT_PID="${DEPLOYMENT_ID##*-}"
case "$DEPLOYMENT_PID" in
  ''|*[!0-9]*) echo "Invalid deployment process id: $DEPLOYMENT_PID" >&2; exit 2 ;;
esac

for command_name in docker sha256sum cp mv sed find grep date; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is missing: $command_name" >&2
    exit 2
  fi
done

BACKUP_DIR="$WORK_DIR/deployment_backups/$DEPLOYMENT_ID"
OWNER_FILE="$UPDATE_LOCK_DIR/owner"
if [ ! -d "$UPDATE_LOCK_DIR" ] || [ ! -f "$OWNER_FILE" ]; then
  echo "The deployment lock and owner record must both exist." >&2
  exit 2
fi
if [ ! -f "$HOLD_FILE" ] || [ ! -d "$BACKUP_DIR" ]; then
  echo "The matching deployment hold and backup directory must both exist." >&2
  exit 2
fi

owner_pid="$(sed -n 's/^pid=//p' "$OWNER_FILE")"
owner_script="$(sed -n 's/^script=//p' "$OWNER_FILE")"
if [ "$owner_pid" != "$DEPLOYMENT_PID" ]; then
  echo "Lock owner pid does not match deployment id: owner=$owner_pid deployment=$DEPLOYMENT_PID" >&2
  exit 2
fi
if [ "$owner_script" != "$SCRIPT_DIR/safe-update-stack.sh" ]; then
  echo "Lock owner is not this stack's safe-update-stack.sh: $owner_script" >&2
  exit 2
fi
if kill -0 "$DEPLOYMENT_PID" 2>/dev/null; then
  echo "Deployment process $DEPLOYMENT_PID is still alive; use its normal rollback trap." >&2
  exit 2
fi

if [ -f "$BACKUP_DIR/SHA256SUMS" ] || [ -f "$BACKUP_DIR/images.json" ]; then
  echo "Deployment reached the verified backup boundary; orphan recovery is not safe." >&2
  exit 2
fi
if find "$BACKUP_DIR/databases" -type f -print -quit 2>/dev/null | grep -q .; then
  echo "Database backup output exists; orphan recovery is restricted to the pre-backup wait stage." >&2
  exit 2
fi
if [ ! -f "$BACKUP_DIR/config.yaml" ]; then
  echo "Deployment config backup is missing." >&2
  exit 2
fi
if [ "$(sha256sum "$BACKUP_DIR/config.yaml" | sed 's/[[:space:]].*$//')" != "$(sha256sum "$CONFIG_FILE" | sed 's/[[:space:]].*$//')" ]; then
  echo "Live config changed after the orphaned deployment started; refusing recovery." >&2
  exit 2
fi
if [ -f "$BACKUP_DIR/ai_control.json" ] && [ -f "$BACKUP_DIR/ai_control.absent" ]; then
  echo "AI control backup is ambiguous." >&2
  exit 2
fi
if [ ! -f "$BACKUP_DIR/ai_control.json" ] && [ ! -f "$BACKUP_DIR/ai_control.absent" ]; then
  echo "AI control backup is missing." >&2
  exit 2
fi

hold_check="$(docker exec -i "$WORKER_CONTAINER" python - "$DEPLOYMENT_ID" <<'PY'
from pathlib import Path
import json
import sys

expected = sys.argv[1]
payload = json.loads(Path('/work/deployment_hold.json').read_text(encoding='utf-8'))
if payload.get('active') is not True:
    raise SystemExit('Deployment hold is not active')
if str(payload.get('reason') or '') != 'safe-stack-update':
    raise SystemExit('Deployment hold reason is not safe-stack-update')
if str(payload.get('deployment_id') or '') != expected:
    raise SystemExit('Deployment hold id does not match the requested recovery')
print('hold_verified')
PY
)"
if [ "$hold_check" != "hold_verified" ]; then
  echo "Deployment hold verification failed: $hold_check" >&2
  exit 2
fi

for container_name in "$WORKER_CONTAINER" "$WEBUI_CONTAINER"; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$container_name" 2>/dev/null || true)" != "true" ]; then
    echo "Live container is not running: $container_name" >&2
    exit 2
  fi
done
LIVE_WORKER_IMAGE_ID="$(docker inspect -f '{{.Image}}' "$WORKER_CONTAINER")"
LIVE_WEBUI_IMAGE_ID="$(docker inspect -f '{{.Image}}' "$WEBUI_CONTAINER")"
case "$LIVE_WORKER_IMAGE_ID:$LIVE_WEBUI_IMAGE_ID" in
  sha256:*:sha256:*) ;;
  *) echo "Could not identify both live container images." >&2; exit 2 ;;
esac

status_check="$(docker exec -i "$WEBUI_CONTAINER" python - "$DEPLOYMENT_ID" <<'PY'
import json
import sys
from urllib.request import urlopen

expected = sys.argv[1]
with urlopen('http://127.0.0.1:8765/api/status?lite=true', timeout=10) as response:
    payload = json.load(response)
hold = payload.get('deployment_hold') or {}
if hold.get('active') is not True or str(hold.get('deployment_id') or '') != expected:
    raise SystemExit('WebUI does not report the exact orphaned deployment hold')
if int((payload.get('health') or {}).get('failed_errors') or 0) != 0:
    raise SystemExit('Live stack has failed health checks')
worker = payload.get('worker') or {}
if worker.get('running') is not True or worker.get('restarting') is True:
    raise SystemExit('Live Worker is not stable')
print('endpoint_verified')
PY
)"
if [ "$status_check" != "endpoint_verified" ]; then
  echo "Live endpoint verification failed: $status_check" >&2
  exit 2
fi

echo "Orphan recovery preview passed."
echo "deployment_id=$DEPLOYMENT_ID"
echo "dead_pid=$DEPLOYMENT_PID"
echo "live_worker_image=$LIVE_WORKER_IMAGE_ID"
echo "live_webui_image=$LIVE_WEBUI_IMAGE_ID"
echo "boundary=pre_verified_backup"
if [ "$MODE" = "--preview" ]; then
  echo "No state changed. Rerun with --apply to restore the live image tags and saved AI control state."
  exit 0
fi

if kill -0 "$DEPLOYMENT_PID" 2>/dev/null; then
  echo "Deployment process $DEPLOYMENT_PID reappeared after preview checks; refusing recovery." >&2
  exit 2
fi

RECOVERED_HOLD_FILE="$BACKUP_DIR/orphaned_deployment_hold.active.json"
RECOVERED_LOCK_DIR="$BACKUP_DIR/orphaned_deployment_update.lock"
RECOVERED_AI_CONTROL_FILE="$BACKUP_DIR/orphaned_ai_control.active.json"
for recovery_target in "$RECOVERED_HOLD_FILE" "$RECOVERED_LOCK_DIR" "$RECOVERED_AI_CONTROL_FILE"; do
  if [ -e "$recovery_target" ]; then
    echo "Recovery evidence target already exists: $recovery_target" >&2
    exit 2
  fi
done

docker image tag "$LIVE_WORKER_IMAGE_ID" "$WORKER_IMAGE"
docker image tag "$LIVE_WEBUI_IMAGE_ID" "$WEBUI_IMAGE"
if [ -f "$AI_CONTROL_FILE" ]; then
  mv "$AI_CONTROL_FILE" "$RECOVERED_AI_CONTROL_FILE"
fi
if [ -f "$BACKUP_DIR/ai_control.json" ]; then
  cp "$BACKUP_DIR/ai_control.json" "$AI_CONTROL_FILE.orphan-recovery.tmp"
  mv "$AI_CONTROL_FILE.orphan-recovery.tmp" "$AI_CONTROL_FILE"
fi
mv "$HOLD_FILE" "$RECOVERED_HOLD_FILE"
mv "$UPDATE_LOCK_DIR" "$RECOVERED_LOCK_DIR"

if [ -e "$HOLD_FILE" ] || [ -e "$UPDATE_LOCK_DIR" ]; then
  echo "Orphan recovery did not release the deployment hold and lock." >&2
  exit 3
fi
if [ "$(docker image inspect -f '{{.Id}}' "$WORKER_IMAGE")" != "$LIVE_WORKER_IMAGE_ID" ]; then
  echo "Worker latest tag was not restored to the live image." >&2
  exit 3
fi
if [ "$(docker image inspect -f '{{.Id}}' "$WEBUI_IMAGE")" != "$LIVE_WEBUI_IMAGE_ID" ]; then
  echo "WebUI latest tag was not restored to the live image." >&2
  exit 3
fi
if [ -f "$BACKUP_DIR/ai_control.json" ]; then
  if [ "$(sha256sum "$BACKUP_DIR/ai_control.json" | sed 's/[[:space:]].*$//')" != "$(sha256sum "$AI_CONTROL_FILE" | sed 's/[[:space:]].*$//')" ]; then
    echo "AI control state was not restored exactly." >&2
    exit 3
  fi
elif [ -e "$AI_CONTROL_FILE" ]; then
  echo "AI control file should have been restored to absent." >&2
  exit 3
fi

post_status_check="$(docker exec -i "$WEBUI_CONTAINER" python - <<'PY'
import json
from urllib.request import urlopen

with urlopen('http://127.0.0.1:8765/api/status?lite=true', timeout=10) as response:
    payload = json.load(response)
if bool((payload.get('deployment_hold') or {}).get('active')):
    raise SystemExit('WebUI still reports an active deployment hold')
worker = payload.get('worker') or {}
if worker.get('running') is not True or worker.get('restarting') is True:
    raise SystemExit('Live Worker became unstable during orphan recovery')
print('recovery_verified')
PY
)"
if [ "$post_status_check" != "recovery_verified" ]; then
  echo "Post-recovery endpoint verification failed: $post_status_check" >&2
  exit 3
fi

{
  printf 'deployment_id=%s\n' "$DEPLOYMENT_ID"
  printf 'recovered_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'dead_pid=%s\n' "$DEPLOYMENT_PID"
  printf 'live_worker_image=%s\n' "$LIVE_WORKER_IMAGE_ID"
  printf 'live_webui_image=%s\n' "$LIVE_WEBUI_IMAGE_ID"
  printf 'boundary=pre_verified_backup\n'
} > "$BACKUP_DIR/orphan_recovery.txt.tmp"
mv "$BACKUP_DIR/orphan_recovery.txt.tmp" "$BACKUP_DIR/orphan_recovery.txt"

echo "Orphan recovery complete. Live containers were untouched; image tags and AI control were restored."
echo "Now rerun: $SCRIPT_DIR/safe-update-stack.sh"
