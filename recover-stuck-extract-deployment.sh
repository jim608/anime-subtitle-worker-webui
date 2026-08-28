#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
WORK_DIR="${WORK_DIR:-/mnt/user/appdata/anime-subtitle-worker/work}"
WORKER_CONTAINER="${WORKER_CONTAINER_NAME:-anime-subtitle-worker}"
MIN_RUNTIME_SECONDS="${MIN_RUNTIME_SECONDS:-900}"
HOLD_FILE="$WORK_DIR/deployment_hold.json"

usage() {
  echo "Usage: $0 <mikan-extract-job-key>" >&2
  echo "Safely retires one stuck running extraction so an already-waiting stack update can roll back." >&2
}

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  usage
  exit 2
fi
JOB_KEY="$1"

for command_name in docker kill tr; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is missing: $command_name" >&2
    exit 2
  fi
done
if [ ! -f "$HOLD_FILE" ]; then
  echo "No active deployment hold exists; refusing the deployment-specific recovery." >&2
  exit 2
fi
if [ "$(docker inspect -f '{{.State.Running}}' "$WORKER_CONTAINER" 2>/dev/null || true)" != "true" ]; then
  echo "Worker container is not running; no live extraction can be safely identified." >&2
  exit 2
fi

hold_fields="$(docker exec -i "$WORKER_CONTAINER" python - <<'PY'
from pathlib import Path
import json

payload = json.loads(Path('/work/deployment_hold.json').read_text(encoding='utf-8'))
if not payload.get('active') or payload.get('reason') != 'safe-stack-update':
    raise SystemExit('Deployment hold is not an active safe-stack-update hold')
deployment_id = str(payload.get('deployment_id') or '')
try:
    deployment_pid = int(deployment_id.rsplit('-', 1)[1])
except (IndexError, TypeError, ValueError):
    raise SystemExit(f'Cannot recover deployment process id from {deployment_id!r}')
print(f'{deployment_id}|{deployment_pid}')
PY
)"
DEPLOYMENT_ID="${hold_fields%%|*}"
DEPLOYMENT_PID="${hold_fields##*|}"
case "$DEPLOYMENT_PID" in
  ''|*[!0-9]*)
    echo "Invalid deployment process id: $DEPLOYMENT_PID" >&2
    exit 2
    ;;
esac
if ! kill -0 "$DEPLOYMENT_PID" 2>/dev/null; then
  echo "Deployment process $DEPLOYMENT_PID is no longer running; refusing to mutate job state." >&2
  exit 2
fi
command_line="$(tr '\000' ' ' < "/proc/$DEPLOYMENT_PID/cmdline" 2>/dev/null || true)"
case "$command_line" in
  *safe-update-stack.sh*) ;;
  *)
    echo "PID $DEPLOYMENT_PID is not safe-update-stack.sh: $command_line" >&2
    exit 2
    ;;
esac
if [ -f "$WORK_DIR/deployment_backups/$DEPLOYMENT_ID/SHA256SUMS" ]; then
  echo "Deployment already passed the idle boundary and has a verified state backup; use its normal rollback instead." >&2
  exit 2
fi

echo "Backing up Mikan state and retiring stuck extraction: $JOB_KEY"
recovery_dir="$(docker exec -i "$WORKER_CONTAINER" python - "$JOB_KEY" "$MIN_RUNTIME_SECONDS" <<'PY'
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import time

job_key = sys.argv[1]
minimum_runtime = max(60, int(sys.argv[2]))
source = Path('/work/mikan_state.sqlite3')
now = time.time()

connection = sqlite3.connect(source, timeout=60)
connection.row_factory = sqlite3.Row
connection.execute('PRAGMA busy_timeout=60000')
try:
    row = connection.execute(
        '''
        SELECT job_key, torrent_name, status, worker_id, started_at, updated_at,
               result_json, last_error
        FROM mikan_extract_jobs
        WHERE job_key = ?
        ''',
        (job_key,),
    ).fetchone()
    if row is None:
        raise SystemExit(f'Extraction job does not exist: {job_key}')
    original = dict(row)
    if original['status'] != 'running' or not original['worker_id']:
        raise SystemExit(f"Extraction job is not actively leased: status={original['status']!r}")
    runtime = now - float(original['started_at'] or now)
    if runtime < minimum_runtime:
        raise SystemExit(
            f'Extraction runtime {runtime:.0f}s is below the safety threshold {minimum_runtime}s'
        )

    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))
    backup_dir = Path('/work/emergency_extract_recovery') / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_path = backup_dir / 'mikan_state.sqlite3'
    backup = sqlite3.connect(backup_path, timeout=60)
    try:
        connection.backup(backup, pages=512, sleep=0.05)
        backup.commit()
        check = backup.execute('PRAGMA quick_check').fetchone()
        if check is None or str(check[0]).casefold() != 'ok':
            raise RuntimeError(f'Backup quick_check failed: {check}')
    finally:
        backup.close()

    detail = (
        'Operator safely interrupted a stuck extraction before deploying the bounded sidecar-reader fix; '
        'use the per-job retry action after the fixed Worker is live.'
    )
    result = {
        'extracted_count': 0,
        'failure_reason': 'operator_cancelled_for_safe_upgrade',
        'failure_bucket': 'operator_cancelled',
        'failure_detail': detail,
        'failure_context': {'previous_progress': json.loads(original['result_json'] or '{}')},
        'subtitle_diagnostics': [],
        'retryable': False,
        'defer_seconds': 0,
    }
    connection.execute('BEGIN IMMEDIATE')
    cursor = connection.execute(
        '''
        UPDATE mikan_extract_jobs
        SET status='terminal_failed', worker_id='', lease_until=0,
            result_json=?, last_error=?, updated_at=?, finished_at=?
        WHERE job_key=? AND status='running' AND worker_id=?
        ''',
        (
            json.dumps(result, ensure_ascii=False, sort_keys=True),
            detail,
            now,
            now,
            job_key,
            original['worker_id'],
        ),
    )
    if cursor.rowcount != 1:
        connection.rollback()
        raise SystemExit('Extraction lease changed during recovery; no state was modified')
    connection.commit()
    check = connection.execute('PRAGMA quick_check').fetchone()
    if check is None or str(check[0]).casefold() != 'ok':
        raise RuntimeError(f'Live database quick_check failed after update: {check}')

    digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    manifest = {
        'created_at': now,
        'job_key': job_key,
        'original': original,
        'backup_sha256': digest,
        'new_status': 'terminal_failed',
        'reason': 'operator_cancelled_for_safe_upgrade',
    }
    (backup_dir / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(backup_dir)
finally:
    connection.close()
PY
)"

echo "Recovery backup verified: $recovery_dir"
echo "Stopping the waiting deployment through its own rollback trap: pid=$DEPLOYMENT_PID"
kill -TERM "$DEPLOYMENT_PID"

elapsed=0
while [ "$elapsed" -lt 240 ]; do
  process_alive=0
  hold_active=0
  worker_running=0
  if kill -0 "$DEPLOYMENT_PID" 2>/dev/null; then process_alive=1; fi
  if [ -f "$HOLD_FILE" ]; then hold_active=1; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$WORKER_CONTAINER" 2>/dev/null || true)" = "true" ]; then
    worker_running=1
  fi
  if [ "$process_alive" -eq 0 ] && [ "$hold_active" -eq 0 ] && [ "$worker_running" -eq 1 ]; then
    break
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done
if [ "$elapsed" -ge 240 ]; then
  echo "Rollback did not reach a healthy endpoint within 240s. Backup: $recovery_dir" >&2
  exit 3
fi

status="$(docker exec -i "$WORKER_CONTAINER" python - "$JOB_KEY" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect('file:/work/mikan_state.sqlite3?mode=ro', uri=True, timeout=30)
try:
    row = connection.execute(
        'SELECT status FROM mikan_extract_jobs WHERE job_key=?',
        (sys.argv[1],),
    ).fetchone()
finally:
    connection.close()
print(str(row[0]) if row else 'missing')
PY
)"
if [ "$status" != "terminal_failed" ]; then
  echo "Unexpected post-rollback extraction state: $status. Backup: $recovery_dir" >&2
  exit 3
fi

echo "Recovery complete: Worker is running, deployment hold is gone, job is preserved for a targeted retry."
echo "Now rerun: $SCRIPT_DIR/safe-update-stack.sh"
