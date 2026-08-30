#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
WORKER_DIR="${WORKER_DIR:-$(dirname "$SCRIPT_DIR")/anime-subtitle-worker}"
WEBUI_DIR="${WEBUI_DIR:-$SCRIPT_DIR}"
WORK_DIR="${WORK_DIR:-/mnt/user/appdata/anime-subtitle-worker/work}"
LOG_DIR="${LOG_DIR:-/mnt/user/appdata/anime-subtitle-worker/logs}"
CONFIG_FILE="${CONFIG_FILE:-/mnt/user/appdata/anime-subtitle-worker/config.yaml}"
WEBUI_URL="${WEBUI_URL:-http://127.0.0.1:8765}"
WORKER_SERVICE="${WORKER_SERVICE:-anime-subtitle-worker}"
WEBUI_SERVICE="${WEBUI_SERVICE:-anime-subtitle-worker-webui}"
WORKER_CONTAINER="${WORKER_CONTAINER_NAME:-anime-subtitle-worker}"
WEBUI_CONTAINER="${WEBUI_CONTAINER_NAME:-anime-subtitle-worker-webui}"
WORKER_IMAGE="${WORKER_IMAGE:-anime-subtitle-worker:latest}"
WEBUI_IMAGE="${WEBUI_IMAGE:-anime-subtitle-worker-webui:latest}"
IDLE_WAIT_SECONDS="${IDLE_WAIT_SECONDS:-14400}"
IDLE_STABLE_SECONDS="${IDLE_STABLE_SECONDS:-15}"
POLL_SECONDS="${POLL_SECONDS:-2}"
STATUS_REPORT_SECONDS="${STATUS_REPORT_SECONDS:-30}"
# Array-backed startup housekeeping can legitimately take longer than three minutes.
COMMAND_PROBE_TIMEOUT_SECONDS="${COMMAND_PROBE_TIMEOUT_SECONDS:-900}"
BACKUP_AI_CACHE="${BACKUP_AI_CACHE:-0}"
BACKUP_RETENTION_NEWEST="${BACKUP_RETENTION_NEWEST:-3}"
BACKUP_RETENTION_DAILY="${BACKUP_RETENTION_DAILY:-7}"
BACKUP_RETENTION_WEEKLY="${BACKUP_RETENTION_WEEKLY:-4}"
SCANNER_STATE_RESTORE_DEPLOYMENT_ID="${SCANNER_STATE_RESTORE_DEPLOYMENT_ID:-}"
RUN_TESTS="${RUN_TESTS:-1}"
UPDATE_LOCK_DIR="${UPDATE_LOCK_DIR:-$WORK_DIR/deployment_update.lock}"
AUTO_RECOVER_ORPHANED_PREBACKUP="${AUTO_RECOVER_ORPHANED_PREBACKUP:-1}"

case "$IDLE_WAIT_SECONDS:$IDLE_STABLE_SECONDS:$POLL_SECONDS:$STATUS_REPORT_SECONDS:$COMMAND_PROBE_TIMEOUT_SECONDS:$BACKUP_RETENTION_NEWEST:$BACKUP_RETENTION_DAILY:$BACKUP_RETENTION_WEEKLY" in
  *[!0-9:]*|*::*|:*|*:)
    echo "Deployment timeout and polling settings must be non-negative integers." >&2
    exit 2
    ;;
esac
if [ "$POLL_SECONDS" -le 0 ] || [ "$IDLE_STABLE_SECONDS" -le 0 ] || [ "$STATUS_REPORT_SECONDS" -le 0 ] || [ "$COMMAND_PROBE_TIMEOUT_SECONDS" -le 0 ]; then
  echo "Polling, stability and command probe timeout settings must be greater than zero." >&2
  exit 2
fi
case "$AUTO_RECOVER_ORPHANED_PREBACKUP" in
  0|1) ;;
  *)
    echo "AUTO_RECOVER_ORPHANED_PREBACKUP must be 0 or 1." >&2
    exit 2
    ;;
esac

for command_name in docker curl sha256sum cp mv sed sh; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is missing: $command_name" >&2
    exit 2
  fi
done
for required_path in "$WORKER_DIR/docker-compose.yml" "$WEBUI_DIR/docker-compose.yml" "$CONFIG_FILE" "$WORK_DIR" "$LOG_DIR"; do
  if [ ! -e "$required_path" ]; then
    echo "Required deployment path is missing: $required_path" >&2
    exit 2
  fi
done

LOCK_ACQUIRED=0
release_update_lock() {
  if [ "$LOCK_ACQUIRED" != "1" ]; then
    return 0
  fi
  rm -f "$UPDATE_LOCK_DIR/owner"
  if ! rmdir "$UPDATE_LOCK_DIR" 2>/dev/null; then
    echo "WARNING: deployment lock directory could not be removed: $UPDATE_LOCK_DIR" >&2
  fi
  LOCK_ACQUIRED=0
}

early_exit() {
  exit_code=$?
  trap - 0 1 2 15
  release_update_lock
  exit "$exit_code"
}

recover_orphaned_prebackup_lock() {
  if [ "$AUTO_RECOVER_ORPHANED_PREBACKUP" != "1" ]; then
    return 1
  fi
  recovery_script="$SCRIPT_DIR/recover-orphaned-deployment.sh"
  hold_file="$WORK_DIR/deployment_hold.json"
  if [ ! -f "$recovery_script" ] || [ ! -f "$hold_file" ]; then
    return 1
  fi
  orphaned_deployment_id="$(sed -n 's/.*"deployment_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$hold_file")"
  if [ -z "$orphaned_deployment_id" ]; then
    return 1
  fi
  echo "Detected an existing deployment lock; verifying bounded orphan recovery: $orphaned_deployment_id"
  sh "$recovery_script" "$orphaned_deployment_id" --preview || return 1
  sh "$recovery_script" "$orphaned_deployment_id" --apply || return 1
  return 0
}

if ! mkdir "$UPDATE_LOCK_DIR" 2>/dev/null; then
  if recover_orphaned_prebackup_lock && mkdir "$UPDATE_LOCK_DIR" 2>/dev/null; then
    echo "Recovered a verified orphaned pre-backup deployment; continuing with a new safe update."
  else
    echo "Another stack update is already active; refusing concurrent deployment." >&2
    if [ -f "$UPDATE_LOCK_DIR/owner" ]; then
      echo "Lock owner:" >&2
      sed 's/^/  /' "$UPDATE_LOCK_DIR/owner" >&2 || true
    fi
    echo "Lock directory: $UPDATE_LOCK_DIR" >&2
    exit 3
  fi
fi
LOCK_ACQUIRED=1
trap early_exit 0 1 2 15
{
  echo "pid=$$"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "script=$SCRIPT_DIR/safe-update-stack.sh"
} > "$UPDATE_LOCK_DIR/owner"

DEPLOYMENT_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
BACKUP_DIR="$WORK_DIR/deployment_backups/$DEPLOYMENT_ID"
HOLD_FILE="$WORK_DIR/deployment_hold.json"
AI_CONTROL_FILE="$WORK_DIR/ai_control.json"
OLD_WORKER_IMAGE_ID="$(docker image inspect "$WORKER_IMAGE" --format '{{.Id}}')"
OLD_WEBUI_IMAGE_ID="$(docker image inspect "$WEBUI_IMAGE" --format '{{.Id}}')"
MAINTENANCE_STARTED=0
CONTAINERS_RETIRED=0
DEPLOYMENT_COMPLETE=0
WORKER_FROZEN=0

restore_ai_control() {
  if [ -f "$BACKUP_DIR/ai_control.json" ]; then
    cp "$BACKUP_DIR/ai_control.json" "$AI_CONTROL_FILE.rollback.tmp"
    mv "$AI_CONTROL_FILE.rollback.tmp" "$AI_CONTROL_FILE"
  elif [ -f "$BACKUP_DIR/ai_control.absent" ]; then
    rm -f "$AI_CONTROL_FILE"
  fi
}

remove_container_for_recreate() {
  container_name="$1"
  stop_seconds="$2"
  if ! docker container inspect "$container_name" >/dev/null 2>&1; then
    return 0
  fi

  # A container in a fast restart loop can change from running to stopped
  # between Docker Compose's inspect and kill calls.  Disable restart first,
  # then remove the old container explicitly so Compose never races it.
  docker update --restart=no "$container_name" >/dev/null 2>&1 || true
  docker stop --time "$stop_seconds" "$container_name" >/dev/null 2>&1 || true
  remove_attempt=0
  while docker container inspect "$container_name" >/dev/null 2>&1; do
    remove_attempt=$((remove_attempt + 1))
    docker rm -f "$container_name" >/dev/null 2>&1 || true
    if [ "$remove_attempt" -ge 20 ]; then
      echo "Unable to retire container safely before recreate: $container_name" >&2
      return 1
    fi
    sleep 1
  done
}

rollback() {
  exit_code=$?
  trap - 0 1 2 15
  if [ "$DEPLOYMENT_COMPLETE" = "1" ]; then
    release_update_lock
    exit "$exit_code"
  fi
  set +e
  if [ "$MAINTENANCE_STARTED" = "0" ]; then
    docker image tag "$OLD_WORKER_IMAGE_ID" "$WORKER_IMAGE" >/dev/null 2>&1
    docker image tag "$OLD_WEBUI_IMAGE_ID" "$WEBUI_IMAGE" >/dev/null 2>&1
    echo "Pre-deployment build or test failed; live containers were untouched and previous latest tags were restored." >&2
    release_update_lock
    exit "$exit_code"
  fi
  if [ "$CONTAINERS_RETIRED" = "0" ]; then
    echo "Pre-retire maintenance failed; restoring hold, AI control and image tags without replacing live containers or databases." >&2
    if [ "$WORKER_FROZEN" = "1" ]; then
      docker kill --signal=CONT "$WORKER_CONTAINER" >/dev/null 2>&1 || true
      WORKER_FROZEN=0
    fi
    docker image tag "$OLD_WORKER_IMAGE_ID" "$WORKER_IMAGE" >/dev/null 2>&1
    docker image tag "$OLD_WEBUI_IMAGE_ID" "$WEBUI_IMAGE" >/dev/null 2>&1
    restore_ai_control
    rm -f "$HOLD_FILE"
    release_update_lock
    exit "$exit_code"
  fi
  echo "Deployment failed; rolling back images and verified state backup." >&2
  if [ "$WORKER_FROZEN" = "1" ]; then
    docker kill --signal=CONT "$WORKER_CONTAINER" >/dev/null 2>&1
    WORKER_FROZEN=0
  fi
  remove_container_for_recreate "$WORKER_CONTAINER" 60 || true
  remove_container_for_recreate "$WEBUI_CONTAINER" 30 || true
  if [ -f "$BACKUP_DIR/SHA256SUMS" ]; then
    docker run --rm -v "$WORK_DIR:/work" --entrypoint python "$WORKER_IMAGE" \
      /app/deployment_backup_retention.py mark \
      --backup "/work/deployment_backups/$DEPLOYMENT_ID" \
      --state deployment_failed \
      --verified-by safe-update-stack-rollback >/dev/null 2>&1 || true
  fi
  docker image tag "$OLD_WORKER_IMAGE_ID" "$WORKER_IMAGE" >/dev/null 2>&1
  docker image tag "$OLD_WEBUI_IMAGE_ID" "$WEBUI_IMAGE" >/dev/null 2>&1
  if [ -f "$BACKUP_DIR/SHA256SUMS" ] && ! (cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS) >/dev/null 2>&1; then
    echo "CRITICAL: rollback backup checksum verification failed; containers remain stopped and no state files were overwritten. Backup: $BACKUP_DIR" >&2
    release_update_lock
    exit "$exit_code"
  fi
  for database_name in scanner_state.sqlite3 mikan_state.sqlite3 control_state.sqlite3 series_metadata.sqlite3; do
    backup_database="$BACKUP_DIR/databases/$database_name"
    if [ -f "$backup_database" ]; then
      rm -f "$WORK_DIR/$database_name-wal" "$WORK_DIR/$database_name-shm"
      cp "$backup_database" "$WORK_DIR/.$database_name.rollback.tmp"
      mv "$WORK_DIR/.$database_name.rollback.tmp" "$WORK_DIR/$database_name"
    fi
  done
  if [ -f "$BACKUP_DIR/config.yaml" ]; then
    cp "$BACKUP_DIR/config.yaml" "$CONFIG_FILE.rollback.tmp"
    mv "$CONFIG_FILE.rollback.tmp" "$CONFIG_FILE"
  fi
  if [ -d "$BACKUP_DIR/legacy_state" ]; then
    for legacy_name in mikan_auto_matches.json mikan_fallback_sources.json mikan_seen.json; do
      if [ -f "$BACKUP_DIR/legacy_state/$legacy_name" ]; then
        cp "$BACKUP_DIR/legacy_state/$legacy_name" "$WORK_DIR/.$legacy_name.rollback.tmp"
        mv "$WORK_DIR/.$legacy_name.rollback.tmp" "$WORK_DIR/$legacy_name"
        rm -f "$WORK_DIR/${legacy_name%.json}.legacy-readonly.json"
      fi
    done
  fi
  restore_ai_control
  rm -f "$HOLD_FILE"
  docker compose -f "$WORKER_DIR/docker-compose.yml" --project-directory "$WORKER_DIR" up -d --no-build --force-recreate "$WORKER_SERVICE"
  docker compose -f "$WEBUI_DIR/docker-compose.yml" --project-directory "$WEBUI_DIR" up -d --no-build --force-recreate "$WEBUI_SERVICE"
  echo "Rollback complete. Backup retained at: $BACKUP_DIR" >&2
  release_update_lock
  exit "$exit_code"
}
trap rollback 0 1 2 15

compute_worker_source_revision() (
  cd "$WORKER_DIR"
  LC_ALL=C sha256sum Dockerfile requirements.txt config.yaml ./*.py | LC_ALL=C sha256sum | awk '{print $1}'
)

compute_worker_python_revision() (
  cd "$WORKER_DIR"
  LC_ALL=C sha256sum ./*.py | LC_ALL=C sha256sum | awk '{print $1}'
)

compute_webui_source_revision() (
  cd "$WEBUI_DIR"
  find Dockerfile requirements.txt package.json package-lock.json index.html vite.config.js app.py control_api.py src tests \
    -type f -print | LC_ALL=C sort | while IFS= read -r source_path; do
      sha256sum "$source_path"
    done | LC_ALL=C sha256sum | awk '{print $1}'
)

WORKER_SOURCE_REVISION="$(compute_worker_source_revision)"
WORKER_PYTHON_REVISION="$(compute_worker_python_revision)"
WEBUI_SOURCE_REVISION="$(compute_webui_source_revision)"

assert_source_trees_unchanged() {
  boundary="$1"
  current_worker_revision="$(compute_worker_source_revision)"
  current_worker_python_revision="$(compute_worker_python_revision)"
  current_webui_revision="$(compute_webui_source_revision)"
  if [ "$current_worker_revision" != "$WORKER_SOURCE_REVISION" ] || \
     [ "$current_worker_python_revision" != "$WORKER_PYTHON_REVISION" ]; then
    echo "Worker source tree changed during deployment; refusing a mixed-version rollout. boundary=$boundary" >&2
    return 1
  fi
  if [ "$current_webui_revision" != "$WEBUI_SOURCE_REVISION" ]; then
    echo "WebUI source tree changed during deployment; refusing a mixed-version rollout. boundary=$boundary" >&2
    return 1
  fi
}

verify_worker_image_sources() {
  image_revision="$(docker run --rm --entrypoint cat "$WORKER_IMAGE" /app/.source-revision 2>/dev/null || true)"
  if [ "$image_revision" != "$WORKER_SOURCE_REVISION" ]; then
    echo "Built Worker image revision mismatch. live=$image_revision expected=$WORKER_SOURCE_REVISION" >&2
    return 1
  fi
  image_python_revision="$(
    docker run --rm --entrypoint sh "$WORKER_IMAGE" -c \
      'cd /app && LC_ALL=C sha256sum ./*.py' | LC_ALL=C sha256sum | awk '{print $1}'
  )"
  if [ "$image_python_revision" != "$WORKER_PYTHON_REVISION" ]; then
    echo "Built Worker image contains a mixed or stale Python source set. image=$image_python_revision expected=$WORKER_PYTHON_REVISION" >&2
    return 1
  fi
  for worker_source_file in \
    main.py \
    ai_scheduler_state.py \
    transcriber.py \
    worker.py \
    retranslate_ai_lines.py \
    repair_ai_outputs.py \
    subtitle_extract.py \
    safe_files.py \
    config.py \
    subtitle_quality.py \
    selective_ai_cleanup.py \
    scanner_state_recovery.py \
    scanner_state_auto_recovery.py \
    deployment_backup_retention.py
  do
    expected_sha="$(sha256sum "$WORKER_DIR/$worker_source_file" | awk '{print substr($1, 1, 12)}')"
    image_sha="$(docker run --rm --entrypoint sha256sum "$WORKER_IMAGE" "/app/$worker_source_file" | awk '{print substr($1, 1, 12)}')"
    if [ "$expected_sha" != "$image_sha" ]; then
      echo "Built Worker image source mismatch. file=$worker_source_file image=$image_sha expected=$expected_sha" >&2
      return 1
    fi
  done
}

verify_webui_image_sources() {
  image_revision="$(docker run --rm --entrypoint cat "$WEBUI_IMAGE" /app/.source-revision 2>/dev/null || true)"
  if [ "$image_revision" != "$WEBUI_SOURCE_REVISION" ]; then
    echo "Built WebUI image revision mismatch. live=$image_revision expected=$WEBUI_SOURCE_REVISION" >&2
    return 1
  fi
  for webui_source_file in app.py control_api.py; do
    expected_sha="$(sha256sum "$WEBUI_DIR/$webui_source_file" | awk '{print substr($1, 1, 12)}')"
    image_sha="$(docker run --rm --entrypoint sha256sum "$WEBUI_IMAGE" "/app/$webui_source_file" | awk '{print substr($1, 1, 12)}')"
    if [ "$expected_sha" != "$image_sha" ]; then
      echo "Built WebUI image source mismatch. file=$webui_source_file image=$image_sha expected=$expected_sha" >&2
      return 1
    fi
  done
}

verify_worker_image_config() (
  image_ref="$1"
  image_label="$2"
  if ! config_output="$(docker run --rm \
    -v "$CONFIG_FILE:/app/deployment-config.yaml:ro" \
    --entrypoint python "$image_ref" \
    -c 'from config import load_config; load_config("/app/deployment-config.yaml")' 2>&1)"; then
    echo "$image_label cannot parse the current config.yaml; deployment and rollback are unsafe." >&2
    if [ -n "$config_output" ]; then
      printf '%s\n' "$config_output" >&2
    fi
    return 1
  fi
)

echo "[1/8] Building Worker and WebUI images while the live stack continues running."
docker compose -f "$WORKER_DIR/docker-compose.yml" --project-directory "$WORKER_DIR" build \
  --build-arg "SOURCE_REVISION=$WORKER_SOURCE_REVISION" "$WORKER_SERVICE"
if ! verify_worker_image_sources; then
  echo "Worker build cache returned stale source; rebuilding once without cache." >&2
  docker compose -f "$WORKER_DIR/docker-compose.yml" --project-directory "$WORKER_DIR" build --no-cache \
    --build-arg "SOURCE_REVISION=$WORKER_SOURCE_REVISION" "$WORKER_SERVICE"
  verify_worker_image_sources
fi
docker compose -f "$WEBUI_DIR/docker-compose.yml" --project-directory "$WEBUI_DIR" build \
  --build-arg "SOURCE_REVISION=$WEBUI_SOURCE_REVISION" "$WEBUI_SERVICE"
if ! verify_webui_image_sources; then
  echo "WebUI build cache returned stale source; rebuilding once without cache." >&2
  docker compose -f "$WEBUI_DIR/docker-compose.yml" --project-directory "$WEBUI_DIR" build --no-cache \
    --build-arg "SOURCE_REVISION=$WEBUI_SOURCE_REVISION" "$WEBUI_SERVICE"
  verify_webui_image_sources
fi

echo "Verifying that both the new Worker image and rollback image accept the current config.yaml."
verify_worker_image_config "$WORKER_IMAGE" "New Worker image"
verify_worker_image_config "$OLD_WORKER_IMAGE_ID" "Rollback Worker image"

if [ -n "$SCANNER_STATE_RESTORE_DEPLOYMENT_ID" ]; then
  echo "Verifying requested scanner state restore source before maintenance."
  docker run --rm -v "$WORK_DIR:/work:ro" --entrypoint python "$WORKER_IMAGE" \
    /app/scanner_state_recovery.py \
    --work-root /work \
    --source-deployment-id "$SCANNER_STATE_RESTORE_DEPLOYMENT_ID"
fi

if [ "$RUN_TESTS" != "0" ]; then
  echo "[2/8] Running the full source tests inside the newly built images."
  docker run --rm --entrypoint sh -v "$WORKER_DIR:/src:ro" "$WORKER_IMAGE" -c 'cd /src && python -B -m unittest'
  docker run --rm --entrypoint sh -v "$WEBUI_DIR:/src:ro" "$WEBUI_IMAGE" -c 'cd /src && python -B -m unittest tests.test_webui_backend tests.test_deployment_script'
else
  echo "[2/8] RUN_TESTS=0; pre-deployment tests were explicitly skipped."
fi
assert_source_trees_unchanged "after-image-tests"

echo "[3/8] Entering deployment hold and waiting for a stable safe endpoint."
mkdir -p "$BACKUP_DIR/databases"
cp "$CONFIG_FILE" "$BACKUP_DIR/config.yaml"
if [ -f "$AI_CONTROL_FILE" ]; then
  cp "$AI_CONTROL_FILE" "$BACKUP_DIR/ai_control.json"
else
  : > "$BACKUP_DIR/ai_control.absent"
fi
printf '{"active":true,"deployment_id":"%s","created_at":%s,"reason":"safe-stack-update"}\n' \
  "$DEPLOYMENT_ID" "$(date +%s)" > "$HOLD_FILE.tmp"
mv "$HOLD_FILE.tmp" "$HOLD_FILE"
printf '{"paused":true,"requested_at":"%s","updated_at":%s,"requested_by":"safe-stack-update"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(date +%s)" > "$AI_CONTROL_FILE.tmp"
mv "$AI_CONTROL_FILE.tmp" "$AI_CONTROL_FILE"
MAINTENANCE_STARTED=1

wait_started_at="$(date +%s)"
stable_started_at=0
last_progress_key=""
last_state_reported_at=0
stale_ai_recovery_attempted=0
while :; do
  state="$({ docker exec -i "$WEBUI_CONTAINER" python - <<'PY'
import json
import time
from urllib.request import urlopen

try:
    with urlopen("http://127.0.0.1:8765/api/status?lite=true", timeout=10) as response:
        payload = json.load(response)
    current_ai = payload.get("current_ai")
    ai_scheduler = payload.get("ai_scheduler") or {}
    deployment_hold = payload.get("deployment_hold") or {}
    queue_counts = payload.get("queue_counts") or {}
    mikan = payload.get("mikan") or {}
    state_db = mikan.get("state_db") or {}
    extract_jobs = state_db.get("extract_jobs") or {}
    raw_extract_counts = extract_jobs.get("counts")
    # ``active`` includes queued jobs.  A deployment hold intentionally keeps
    # queued jobs unclaimed, so only a genuinely running extraction may block
    # the safe replacement boundary.  SQLite GROUP BY omits zero-valued
    # statuses, so an existing empty counts object means running=0; falling
    # back to ``active`` in that case would turn a queued job into a false
    # running job and make this maintenance window wait forever.
    if isinstance(raw_extract_counts, dict):
        extract_active = int(raw_extract_counts.get("running") or 0)
    else:
        # Compatibility only for an older WebUI that did not expose counts.
        extract_active = int(extract_jobs.get("active") or 0)
    mikan_busy = bool(mikan.get("busy"))
    ai_running_stale = bool(
        isinstance(current_ai, dict) and current_ai.get("running_stale")
    )
    scheduler_current_video = str(ai_scheduler.get("current_video") or "").strip()
    deployment_hold_active = bool(deployment_hold.get("active"))
    ai_running_count = max(0, int(queue_counts.get("running") or 0))
    stale_ai_recovery_eligible = bool(
        deployment_hold_active
        and isinstance(current_ai, dict)
        and ai_running_stale
        and not scheduler_current_video
        and ai_running_count == 1
        and extract_active == 0
        and not mikan_busy
    )
    idle = current_ai is None and extract_active == 0 and not mikan_busy
    print(f"idle={1 if idle else 0}")
    print(f"ai_busy={1 if current_ai else 0}")
    print(f"ai_running_stale={1 if ai_running_stale else 0}")
    print(f"ai_scheduler_current_video={scheduler_current_video}")
    print(f"ai_running_count={ai_running_count}")
    print(f"deployment_hold_active={1 if deployment_hold_active else 0}")
    print(f"stale_ai_recovery_eligible={1 if stale_ai_recovery_eligible else 0}")
    print(f"extract_active={extract_active}")
    print(f"mikan_busy={1 if mikan_busy else 0}")
    if extract_active:
        with urlopen("http://127.0.0.1:8765/api/status?lite=false", timeout=10) as response:
            detailed_payload = json.load(response)
        detailed_mikan = detailed_payload.get("mikan") or {}
        detailed_state_db = detailed_mikan.get("state_db") or {}
        detailed_extract_jobs = detailed_state_db.get("extract_jobs") or {}
        running_job = next(
            (
                item
                for item in detailed_extract_jobs.get("recent", [])
                if isinstance(item, dict) and str(item.get("status") or "").lower() == "running"
            ),
            None,
        )
        if running_job:
            now = time.time()
            progress = running_job.get("progress") or {}
            processed = max(0, int(progress.get("processed") or 0))
            total = max(0, int(progress.get("total") or 0))
            percent = float(progress.get("percent") or 0.0)
            current = str(progress.get("current") or "").replace("\r", " ").replace("\n", " ")
            current_name = current.rsplit("/", 1)[-1]
            torrent_name = str(running_job.get("torrent_name") or "").replace("\r", " ").replace("\n", " ")
            started_at = float(running_job.get("started_at") or 0)
            updated_at = float(running_job.get("updated_at") or 0)
            print(f"extract_job={torrent_name[:180] or running_job.get('job_key') or '-'}")
            print(f"extract_progress={processed}/{total} ({percent:.1f}%)")
            print(f"extract_current={current_name[:180] or '-'}")
            print(f"extract_runtime_seconds={max(0, int(now - started_at)) if started_at else 0}")
            print(f"extract_heartbeat_age_seconds={max(0, int(now - updated_at)) if updated_at else -1}")
            print(f"extract_progress_key={processed}/{total}|{current_name[:180]}")
except Exception as exc:
    print("idle=0")
    print(f"status_error={exc}")
PY
  } 2>/dev/null)"
  now="$(date +%s)"
  waited=$((now - wait_started_at))
  progress_key="$(printf '%s\n' "$state" | sed -n 's/^extract_progress_key=//p' | head -n 1)"
  display_state="$(printf '%s\n' "$state" | grep -v '^extract_progress_key=' || true)"
  stale_ai_recovery_eligible="$(printf '%s\n' "$state" | sed -n 's/^stale_ai_recovery_eligible=//p' | head -n 1)"
  if [ "$stale_ai_recovery_attempted" = "0" ] && [ "$stale_ai_recovery_eligible" = "1" ]; then
    stale_ai_recovery_attempted=1
    echo "  Requeuing one or more timed-out running AI queue rows through the Worker maintenance CLI."
    if docker run --rm --volumes-from "$WORKER_CONTAINER" --entrypoint python "$WORKER_IMAGE" /app/main.py --config /app/config.yaml --requeue-stale-ai-running; then
      echo "  Stale AI queue recovery command completed; rechecking the safe endpoint."
    else
      echo "  Stale AI queue recovery command was unavailable or failed; deployment remains in the safe wait loop." >&2
    fi
    stable_started_at=0
    sleep "$POLL_SECONDS"
    continue
  fi
  if printf '%s\n' "$state" | grep -q '^idle=1$'; then
    if [ "$stable_started_at" -eq 0 ]; then
      stable_started_at="$now"
    fi
    stable_for=$((now - stable_started_at))
    if [ "$stable_for" -ge "$IDLE_STABLE_SECONDS" ]; then
      printf '%s\n' "$display_state" | sed 's/^/  /'
      break
    fi
    echo "  Safe endpoint observed for ${stable_for}s/${IDLE_STABLE_SECONDS}s."
  else
    stable_started_at=0
    if [ "$progress_key" != "$last_progress_key" ] || [ $((now - last_state_reported_at)) -ge "$STATUS_REPORT_SECONDS" ]; then
      echo "  wait_elapsed=${waited}s/${IDLE_WAIT_SECONDS}s"
      printf '%s\n' "$display_state" | sed 's/^/  /'
      last_state_reported_at="$now"
    fi
    last_progress_key="$progress_key"
  fi
  if [ "$waited" -ge "$IDLE_WAIT_SECONDS" ]; then
    echo "Safe endpoint timeout after ${waited}s; deployment cancelled without terminating active work." >&2
    exit 3
  fi
  sleep "$POLL_SECONDS"
done

assert_source_trees_unchanged "after-safe-endpoint-wait"

worker_runtime_state="$(docker inspect -f '{{if .State.Restarting}}restarting{{else if .State.Running}}running{{else}}stopped{{end}}' "$WORKER_CONTAINER" 2>/dev/null || echo missing)"
if [ "$worker_runtime_state" != "running" ]; then
  echo "  Live Worker state is $worker_runtime_state; using restart-loop recovery boundary after idle checks and verified backup."
elif docker exec -i "$WORKER_CONTAINER" grep -q 'def _deployment_hold_active' /app/main.py 2>/dev/null; then
  echo "  Live Worker supports deployment hold; no process freeze is required."
else
  echo "  Bootstrapping hold support: freezing the already-idle old Worker before backup."
  docker kill --signal=STOP "$WORKER_CONTAINER" >/dev/null
  WORKER_FROZEN=1
fi

echo "[4/8] Creating online SQLite backups, important state backup and SHA-256 manifest."
mkdir -p "$BACKUP_DIR/runtime"
docker inspect "$WORKER_CONTAINER" > "$BACKUP_DIR/runtime/worker-inspect-before.json" 2>/dev/null || true
docker logs --timestamps --tail 500 "$WORKER_CONTAINER" > "$BACKUP_DIR/runtime/worker-log-before.txt" 2>&1 || true
docker run --rm -i -v "$WORK_DIR:/work" --entrypoint python "$WORKER_IMAGE" - "$DEPLOYMENT_ID" <<'PY'
from pathlib import Path
import sqlite3
import sys

deployment_id = sys.argv[1]
work = Path("/work")
backup = work / "deployment_backups" / deployment_id / "databases"
backup.mkdir(parents=True, exist_ok=True)
for name in ("scanner_state.sqlite3", "mikan_state.sqlite3", "control_state.sqlite3", "series_metadata.sqlite3"):
    source = work / name
    if not source.is_file():
        continue
    destination = backup / name
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60)
    destination_connection = sqlite3.connect(destination, timeout=60)
    try:
        source_connection.execute("PRAGMA busy_timeout=60000")
        source_connection.backup(destination_connection, pages=512, sleep=0.05)
        destination_connection.commit()
        result = destination_connection.execute("PRAGMA quick_check").fetchone()
        if result is None or str(result[0]).casefold() != "ok":
            raise RuntimeError(f"quick_check failed for {name}: {result}")
    finally:
        destination_connection.close()
        source_connection.close()
PY

mkdir -p "$BACKUP_DIR/cache"
for cache_name in ai_output_manifests processing_provenance asr_diagnostics series_metadata; do
  if [ -e "$WORK_DIR/$cache_name" ]; then
    cp -a "$WORK_DIR/$cache_name" "$BACKUP_DIR/cache/"
  fi
done
mkdir -p "$BACKUP_DIR/legacy_state"
for legacy_name in mikan_auto_matches.json mikan_fallback_sources.json mikan_seen.json; do
  if [ -f "$WORK_DIR/$legacy_name" ]; then
    cp "$WORK_DIR/$legacy_name" "$BACKUP_DIR/legacy_state/$legacy_name"
  fi
done
if [ "$BACKUP_AI_CACHE" != "0" ] && [ -d "$WORK_DIR/ai_srt_cache" ]; then
  cp -a "$WORK_DIR/ai_srt_cache" "$BACKUP_DIR/cache/"
fi
printf '{"worker":"%s","webui":"%s","deployment_id":"%s"}\n' \
  "$OLD_WORKER_IMAGE_ID" "$OLD_WEBUI_IMAGE_ID" "$DEPLOYMENT_ID" > "$BACKUP_DIR/images.json"
docker run --rm -v "$WORK_DIR:/work" --entrypoint python "$WORKER_IMAGE" \
  /app/deployment_backup_retention.py create \
  --backup "/work/deployment_backups/$DEPLOYMENT_ID"
(cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS)
docker run --rm -v "$WORK_DIR:/work" --entrypoint python "$WORKER_IMAGE" \
  /app/deployment_backup_retention.py mark \
  --backup "/work/deployment_backups/$DEPLOYMENT_ID" \
  --state backup_verified \
  --verified-by safe-update-stack \
  --external-sha256-verified

echo "[5/8] Rehearsing every additive database migration on backup copies."
docker run --rm -v "$WORK_DIR:/work" --entrypoint python "$WORKER_IMAGE" \
  /app/migration_preflight.py --backup-dir "/work/deployment_backups/$DEPLOYMENT_ID"

assert_source_trees_unchanged "before-container-recreate"
echo "[6/8] Recreating Worker and WebUI with deployment hold still active."
if [ "$WORKER_FROZEN" = "1" ]; then
  docker kill --signal=CONT "$WORKER_CONTAINER" >/dev/null
  WORKER_FROZEN=0
fi
CONTAINERS_RETIRED=1
remove_container_for_recreate "$WORKER_CONTAINER" 60
remove_container_for_recreate "$WEBUI_CONTAINER" 30
if [ -n "$SCANNER_STATE_RESTORE_DEPLOYMENT_ID" ]; then
  echo "Restoring verified scanner state while all database consumers are stopped."
  docker run --rm -v "$WORK_DIR:/work" --entrypoint python "$WORKER_IMAGE" \
    /app/scanner_state_recovery.py \
    --work-root /work \
    --source-deployment-id "$SCANNER_STATE_RESTORE_DEPLOYMENT_ID" \
    --apply \
    --hold-deployment-id "$DEPLOYMENT_ID"
fi
docker compose -f "$WORKER_DIR/docker-compose.yml" --project-directory "$WORKER_DIR" up -d --no-build "$WORKER_SERVICE"
docker compose -f "$WEBUI_DIR/docker-compose.yml" --project-directory "$WEBUI_DIR" up -d --no-build "$WEBUI_SERVICE"

attempt=0
until curl -fsS "$WEBUI_URL/api/status?lite=true" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 120 ]; then
    echo "New WebUI did not become healthy within 120 seconds." >&2
    exit 4
  fi
  sleep 1
done

assert_source_trees_unchanged "after-container-recreate"

live_worker_source_revision="$(docker exec -i "$WORKER_CONTAINER" cat /app/.source-revision 2>/dev/null || true)"
if [ "$live_worker_source_revision" != "$WORKER_SOURCE_REVISION" ]; then
  echo "Live Worker source revision verification failed. live=$live_worker_source_revision expected=$WORKER_SOURCE_REVISION" >&2
  exit 4
fi
live_webui_source_revision="$(docker exec -i "$WEBUI_CONTAINER" cat /app/.source-revision 2>/dev/null || true)"
if [ "$live_webui_source_revision" != "$WEBUI_SOURCE_REVISION" ]; then
  echo "Live WebUI source revision verification failed. live=$live_webui_source_revision expected=$WEBUI_SOURCE_REVISION" >&2
  exit 4
fi

for worker_source_file in \
  main.py \
  ai_scheduler_state.py \
  transcriber.py \
  worker.py \
  retranslate_ai_lines.py \
  repair_ai_outputs.py \
  subtitle_extract.py \
  safe_files.py \
  mikan_worker.py \
  output_manifest.py \
  subtitle_paths.py \
  config.py \
  subtitle_quality.py \
  selective_ai_cleanup.py \
  deployment_backup_retention.py
do
  expected_worker_source_sha="$(sha256sum "$WORKER_DIR/$worker_source_file" | awk '{print substr($1, 1, 12)}')"
  live_worker_source_sha="$(docker exec -i "$WORKER_CONTAINER" sha256sum "/app/$worker_source_file" | awk '{print substr($1, 1, 12)}')"
  if [ "$expected_worker_source_sha" != "$live_worker_source_sha" ]; then
    echo "Live Worker source verification failed. file=$worker_source_file live=$live_worker_source_sha expected=$expected_worker_source_sha" >&2
    exit 4
  fi
done
for webui_source_file in app.py control_api.py; do
  expected_webui_sha="$(sha256sum "$WEBUI_DIR/$webui_source_file" | awk '{print substr($1, 1, 12)}')"
  live_webui_sha="$(docker exec -i "$WEBUI_CONTAINER" sha256sum "/app/$webui_source_file" | awk '{print substr($1, 1, 12)}')"
  if [ "$expected_webui_sha" != "$live_webui_sha" ]; then
    echo "Live WebUI source verification failed. file=$webui_source_file live=$live_webui_sha expected=$expected_webui_sha" >&2
    exit 4
  fi
done

echo "  Moving legacy quality reports out of the media library while new work is held."
docker exec -i "$WORKER_CONTAINER" python /app/migrate_quality_sidecars.py \
  --root /anime \
  --work-path /work \
  --container-anime-root /anime \
  --apply \
  --progress-interval 30

echo "[7/8] Verifying health, v2 payloads, series API and command mailbox end to end."
docker exec -i "$WEBUI_CONTAINER" python - "$DEPLOYMENT_ID" "$COMMAND_PROBE_TIMEOUT_SECONDS" <<'PY'
import json
import sqlite3
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

deployment_id = sys.argv[1]
command_probe_timeout_seconds = max(1, int(sys.argv[2]))
base = "http://127.0.0.1:8765"

def get_raw(path):
    with urlopen(base + path, timeout=15) as response:
        headers = dict(response.headers.items())
        raw = response.read()
    return headers, raw

def get(path):
    _headers, raw = get_raw(path)
    return json.loads(raw), raw

status, _ = get("/api/status?lite=true")
if status.get("health", {}).get("overall") == "error":
    print(
        "health failure payload: "
        + json.dumps(status.get("health") or {}, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit("health reports an error")
if not status.get("worker", {}).get("running") or status.get("worker", {}).get("restarting"):
    raise SystemExit("Worker is not steadily running")
if not status.get("deployment_hold", {}).get("active"):
    raise SystemExit("deployment hold was not preserved across recreate")
if status.get("current_ai") is not None:
    raise SystemExit("AI work started while deployment hold was active")
ai_scheduler = status.get("ai_scheduler")
if not isinstance(ai_scheduler, dict) or not ai_scheduler.get("exists"):
    raise SystemExit("AI scheduler heartbeat state is missing")
if ai_scheduler.get("stale"):
    raise SystemExit(f"AI scheduler heartbeat is stale during deployment hold: {ai_scheduler}")
if ai_scheduler.get("problem"):
    raise SystemExit(f"AI scheduler reports a problem during deployment hold: {ai_scheduler}")
if str(ai_scheduler.get("state") or "") not in {"starting", "deployment_hold", "paused"}:
    raise SystemExit(f"AI scheduler entered an unsafe state during deployment hold: {ai_scheduler}")
verified_extract_jobs = status.get("mikan", {}).get("state_db", {}).get("extract_jobs", {})
verified_extract_counts = verified_extract_jobs.get("counts")
if isinstance(verified_extract_counts, dict):
    verified_extract_running = int(verified_extract_counts.get("running") or 0)
else:
    verified_extract_running = int(verified_extract_jobs.get("active") or 0)
if verified_extract_running:
    raise SystemExit("subtitle extraction started while deployment hold was active")

overview_timings = []
overview = None
overview_raw = b""
# The first reads after a container recreate populate Python, SQLite and SMB
# page caches.  Exclude a fixed, bounded warm-up from the steady-state SLO;
# the following 20 requests remain fully measured against the same 150 ms
# p95 gate, so persistent regressions still fail and roll back.
for _ in range(5):
    overview, overview_raw = get("/api/v2/overview")
for _ in range(20):
    started = time.perf_counter()
    overview, overview_raw = get("/api/v2/overview")
    overview_timings.append(time.perf_counter() - started)
overview_timings.sort()
overview_p95 = overview_timings[max(0, int(len(overview_timings) * 0.95) - 1)]
if overview_p95 > 0.150:
    raise SystemExit(f"v2 overview p95 exceeds 150 ms: {overview_p95 * 1000:.1f} ms")
if len(overview_raw) > 20 * 1024:
    raise SystemExit(f"v2 overview exceeds 20 KiB: {len(overview_raw)}")
overview_scheduler = overview.get("ai_scheduler")
if not isinstance(overview_scheduler, dict) or not overview_scheduler.get("exists"):
    raise SystemExit("v2 overview is missing AI scheduler state")
if overview_scheduler.get("problem"):
    raise SystemExit(f"v2 overview reports an AI scheduler problem: {overview_scheduler}")
latency = overview.get("mikan", {}).get("extract_start_latency", {})
if int(latency.get("target_seconds") or 0) != 15:
    raise SystemExit(f"v2 overview extraction latency SLO is missing: {latency}")
get("/api/v2/ai/tasks?limit=1&fields=compact")
get("/api/v2/mikan/items?limit=1&fields=compact")
events, _ = get("/api/v2/events?limit=1")
for event in events.get("items") or []:
    if "technical_detail" in event:
        raise SystemExit("compact v2 events leaked technical_detail")

review_summaries, review_summary_raw = get(
    "/api/v2/review-items?status=open&limit=30&view=summary"
)
if len(review_summary_raw) > 128 * 1024:
    raise SystemExit(f"review summary exceeds 128 KiB: {len(review_summary_raw)}")
for review in review_summaries.get("items") or []:
    leaked = {"diagnosis", "candidates", "resolution"}.intersection(review)
    if leaked:
        raise SystemExit(f"review summary leaked detail fields: {sorted(leaked)}")

summary_items = review_summaries.get("items") or []
review_detail_verified = False
artwork_verified = False
if summary_items:
    summary_review_id = str(summary_items[0].get("review_id") or "")
    review_detail_payload, _ = get("/api/v2/review-items/" + summary_review_id)
    review_detail = review_detail_payload.get("item")
    if not isinstance(review_detail, dict):
        raise SystemExit("review detail endpoint did not return an item object")
    if str(review_detail.get("review_id") or "") != summary_review_id:
        raise SystemExit("review detail endpoint returned the wrong review id")
    review_detail_verified = True
    artwork_url = str(review_detail.get("artwork_url") or "")
    if artwork_url:
        artwork_headers, artwork_raw = get_raw(artwork_url)
        artwork_type = str(artwork_headers.get("Content-Type") or "").casefold()
        if not artwork_type.startswith("image/") or not artwork_raw:
            raise SystemExit("series artwork proxy did not return a verified image")
        artwork_verified = True

openapi, _ = get("/openapi.json")
documented_paths = set((openapi.get("paths") or {}).keys())
required_review_paths = {
    "/api/v2/review-items/{review_id}",
    "/api/v2/review-items/batch-resolve",
    "/api/v2/series/{series_id}/artwork",
}
missing_review_paths = required_review_paths.difference(documented_paths)
if missing_review_paths:
    raise SystemExit(f"deployed v2 review contract is incomplete: {sorted(missing_review_paths)}")

reviews, _ = get(
    "/api/v2/review-items?status=open&kind=target_ambiguity&limit=100&view=detail"
)
seen_torrent_hashes = set()
for review in reviews.get("items") or []:
    if str(review.get("kind") or "") != "target_ambiguity":
        continue
    diagnosis = review.get("diagnosis") if isinstance(review.get("diagnosis"), dict) else {}
    torrent_hash = str(diagnosis.get("torrent_hash") or "").strip().casefold()
    if len(torrent_hash) != 40 or any(character not in "0123456789abcdef" for character in torrent_hash):
        continue
    if torrent_hash in seen_torrent_hashes:
        raise SystemExit(f"duplicate open target review for torrent hash: {torrent_hash}")
    seen_torrent_hashes.add(torrent_hash)

legacy_series, _ = get("/api/series?page=1&page_size=1")
series_items = legacy_series.get("items") or legacy_series.get("series") or []
if series_items:
    series_id = str(series_items[0].get("series_id") or series_items[0].get("id") or "")
    if series_id:
        get("/api/v2/series/" + series_id)

bootstrap, _ = get("/api/v2/bootstrap")
body = json.dumps({"action": "system.health_probe"}).encode("utf-8")
request = Request(
    base + "/api/v2/commands",
    data=body,
    method="POST",
    headers={
        "Content-Type": "application/json",
        "Origin": base,
        "X-CSRF-Token": str(bootstrap["csrf_token"]),
        "Idempotency-Key": f"deployment-health-{deployment_id}",
    },
)
with urlopen(request, timeout=15) as response:
    command = json.load(response)
command_id = command["command_id"]
command_deadline = time.monotonic() + command_probe_timeout_seconds
next_command_report = time.monotonic() + 15
while True:
    command, _ = get("/api/v2/commands/" + command_id)
    if command.get("status") not in {"accepted", "queued", "running"}:
        break
    now = time.monotonic()
    if now >= command_deadline:
        break
    if now >= next_command_report:
        print(
            f"Worker command health probe waiting: status={command.get('status')} "
            f"remaining={max(0, int(command_deadline - now))}s",
            file=sys.stderr,
            flush=True,
        )
        next_command_report = now + 15
    time.sleep(min(1, max(0.05, command_deadline - now)))
if command.get("status") != "completed":
    raise SystemExit(
        f"Worker command health probe failed after {command_probe_timeout_seconds}s: {command}"
    )
if not command.get("result", {}).get("deployment_hold"):
    raise SystemExit("health probe did not execute under deployment hold")

with sqlite3.connect("file:/work/control_state.sqlite3?mode=ro", uri=True, timeout=15) as connection:
    control_version_row = connection.execute(
        "SELECT value FROM control_meta WHERE key='schema_version'"
    ).fetchone()
control_version = int(control_version_row[0] or 0) if control_version_row else 0
if control_version < 5:
    raise SystemExit(f"control review file-time migration is missing: schema={control_version}")

with sqlite3.connect("file:/work/mikan_state.sqlite3?mode=ro", uri=True, timeout=15) as connection:
    extract_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(mikan_extract_jobs)").fetchall()
    }
required_extract_time_columns = {
    "current_file_timestamp",
    "current_file_time_kind",
    "current_file_size",
}
missing_extract_time_columns = required_extract_time_columns.difference(extract_columns)
if missing_extract_time_columns:
    raise SystemExit(
        f"Mikan extraction file-time migration is incomplete: {sorted(missing_extract_time_columns)}"
    )
print(json.dumps({
    "health": status["health"]["overall"],
    "overview_bytes": len(overview_raw),
    "overview_p95_ms": round(overview_p95 * 1000, 1),
    "revision": overview.get("revision"),
    "review_summary_bytes": len(review_summary_raw),
    "review_detail_verified": review_detail_verified,
    "artwork_verified": artwork_verified,
    "file_time_schema_verified": True,
    "ai_scheduler_state": ai_scheduler.get("state"),
    "command_id": command_id,
    "command_status": command.get("status"),
}, indent=2))
PY

echo "[8/8] Applying verified backup retention, then releasing deployment hold."
set -- \
  /app/deployment_backup_retention.py prune \
  --root /work/deployment_backups \
  --success-log-root /logs \
  --exclude "$DEPLOYMENT_ID"
if [ -n "$SCANNER_STATE_RESTORE_DEPLOYMENT_ID" ]; then
  set -- "$@" --exclude "$SCANNER_STATE_RESTORE_DEPLOYMENT_ID"
fi
set -- "$@" \
  --newest "$BACKUP_RETENTION_NEWEST" \
  --daily "$BACKUP_RETENTION_DAILY" \
  --weekly "$BACKUP_RETENTION_WEEKLY" \
  --apply
docker run --rm -v "$WORK_DIR:/work" -v "$LOG_DIR:/logs:ro" --entrypoint python "$WORKER_IMAGE" "$@"
restore_ai_control
rm -f "$HOLD_FILE"

echo "  Requesting an explicit AI scheduler retry after deployment hold release."
docker exec -i "$WEBUI_CONTAINER" python - "$DEPLOYMENT_ID" "$COMMAND_PROBE_TIMEOUT_SECONDS" <<'PY'
import json
import sys
import time
from urllib.request import Request, urlopen

deployment_id = sys.argv[1]
timeout_seconds = max(1, int(sys.argv[2]))
deadline = time.monotonic() + timeout_seconds
base = "http://127.0.0.1:8765"
idempotency_key = f"deployment-scheduler-retry-{deployment_id}"
command = {}
command_id = ""
last_error = ""

while time.monotonic() < deadline and not command_id:
    try:
        with urlopen(base + "/api/v2/bootstrap", timeout=15) as response:
            bootstrap = json.load(response)
        request = Request(
            base + "/api/v2/commands",
            data=json.dumps({"action": "system.ai_scheduler_retry"}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": base,
                "X-CSRF-Token": str(bootstrap["csrf_token"]),
                "Idempotency-Key": idempotency_key,
            },
        )
        with urlopen(request, timeout=15) as response:
            command = json.load(response)
        command_id = str(command.get("command_id") or "").strip()
        if not command_id:
            raise RuntimeError(f"command response is missing command_id: {command}")
    except Exception as exc:
        last_error = str(exc)
        command = {}
        command_id = ""
        time.sleep(min(1, max(0.05, deadline - time.monotonic())))

if not command_id:
    raise SystemExit(
        "Unable to submit AI scheduler retry after deployment hold release: " + last_error
    )

next_command_report = time.monotonic() + 15
while True:
    try:
        with urlopen(base + "/api/v2/commands/" + command_id, timeout=15) as response:
            command = json.load(response)
        last_error = ""
    except Exception as exc:
        last_error = str(exc)
    now = time.monotonic()
    command_status = str(command.get("status") or "").strip()
    if command_status and command_status not in {"accepted", "queued", "running"}:
        break
    if now >= deadline:
        break
    if now >= next_command_report:
        print(
            f"AI scheduler retry command waiting: status={command_status or 'unavailable'} "
            f"remaining={max(0, int(deadline - now))}s",
            file=sys.stderr,
            flush=True,
        )
        next_command_report = now + 15
    time.sleep(min(1, max(0.05, deadline - now)))

if command.get("status") != "completed":
    detail = command if command else {"command_id": command_id, "read_error": last_error}
    raise SystemExit(
        f"AI scheduler retry command failed after {timeout_seconds}s: "
        + json.dumps(detail, ensure_ascii=False)
    )
result = command.get("result") if isinstance(command.get("result"), dict) else {}
if result.get("action") != "system.ai_scheduler_retry" or result.get("applied") is not True:
    raise SystemExit(
        "AI scheduler retry command returned an invalid result: "
        + json.dumps(command, ensure_ascii=False)
    )
print(json.dumps({
    "ai_scheduler_retry_command_id": command_id,
    "command_status": command.get("status"),
    "retry_requested_at": result.get("retry_requested_at"),
}, indent=2))
PY

echo "  Verifying AI scheduler recovery after deployment hold release."
docker exec -i "$WEBUI_CONTAINER" python - "$COMMAND_PROBE_TIMEOUT_SECONDS" <<'PY'
import json
import sys
import time
from urllib.request import urlopen

timeout_seconds = max(1, int(sys.argv[1]))
deadline = time.monotonic() + timeout_seconds
base = "http://127.0.0.1:8765"
last_status = {}
while time.monotonic() < deadline:
    try:
        with urlopen(base + "/api/status?lite=true", timeout=15) as response:
            last_status = json.load(response)
    except Exception as exc:
        last_status = {"read_error": str(exc)}
        time.sleep(1)
        continue
    worker = last_status.get("worker") if isinstance(last_status.get("worker"), dict) else {}
    scheduler = (
        last_status.get("ai_scheduler")
        if isinstance(last_status.get("ai_scheduler"), dict)
        else {}
    )
    state = str(scheduler.get("state") or "")
    if (
        worker.get("running")
        and not worker.get("restarting")
        and scheduler.get("exists")
        and not scheduler.get("stale")
        and not scheduler.get("problem")
        and state not in {"starting", "deployment_hold", "unknown", "unavailable"}
    ):
        print(json.dumps({
            "worker_status": worker.get("status"),
            "ai_scheduler_state": state,
            "ai_scheduler_heartbeat_age_seconds": scheduler.get("heartbeat_age_seconds"),
            "current_ai_stage": (last_status.get("current_ai") or {}).get("stage"),
        }, indent=2))
        break
    time.sleep(1)
else:
    raise SystemExit(
        "AI scheduler did not recover after deployment hold release: "
        + json.dumps(last_status.get("ai_scheduler") or last_status, ensure_ascii=False)
    )
PY

docker run --rm -v "$WORK_DIR:/work" --entrypoint python "$WORKER_IMAGE" \
  /app/deployment_backup_retention.py mark \
  --backup "/work/deployment_backups/$DEPLOYMENT_ID" \
  --state deployment_completed \
  --verified-by safe-update-stack
RECOVERY_ANCHOR_DEPLOYMENT_ID="$DEPLOYMENT_ID"
if [ -n "$SCANNER_STATE_RESTORE_DEPLOYMENT_ID" ]; then
  RECOVERY_ANCHOR_DEPLOYMENT_ID="$SCANNER_STATE_RESTORE_DEPLOYMENT_ID"
fi
docker run --rm -v "$WORK_DIR:/work" --entrypoint python "$WORKER_IMAGE" \
  /app/scanner_state_recovery.py \
  --work-root /work \
  --source-deployment-id "$RECOVERY_ANCHOR_DEPLOYMENT_ID" \
  --write-anchor
DEPLOYMENT_COMPLETE=1
release_update_lock
trap - 0 1 2 15
echo "Stack update complete. deployment_id=$DEPLOYMENT_ID backup=$BACKUP_DIR"
