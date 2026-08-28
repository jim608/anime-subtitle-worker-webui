from pathlib import Path
import shutil
import subprocess
import unittest


class DeploymentScriptContractTests(unittest.TestCase):
    def test_stack_update_is_valid_for_posix_sh(self) -> None:
        shell = shutil.which("sh")
        if shell is None:
            self.skipTest("POSIX sh is unavailable on this host")
        result = subprocess.run(
            [shell, "-n", "safe-update-stack.sh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stack_update_has_hold_backup_migration_verification_and_rollback(self) -> None:
        script = Path("safe-update-stack.sh").read_text(encoding="utf-8")

        self.assertIn("deployment_hold.json", script)
        self.assertIn("deployment_update.lock", script)
        self.assertIn('mkdir "$UPDATE_LOCK_DIR"', script)
        self.assertIn("refusing concurrent deployment", script)
        self.assertIn('AUTO_RECOVER_ORPHANED_PREBACKUP="${AUTO_RECOVER_ORPHANED_PREBACKUP:-1}"', script)
        self.assertIn("recover_orphaned_prebackup_lock", script)
        self.assertIn('sh "$recovery_script" "$orphaned_deployment_id" --preview', script)
        self.assertIn('sh "$recovery_script" "$orphaned_deployment_id" --apply', script)
        self.assertIn("Recovered a verified orphaned pre-backup deployment", script)
        self.assertIn("release_update_lock", script)
        self.assertIn("Safe endpoint timeout", script)
        self.assertIn("without terminating active work", script)
        self.assertIn(
            "scanner_state.sqlite3 mikan_state.sqlite3 control_state.sqlite3 series_metadata.sqlite3",
            script,
        )
        self.assertIn(
            '("scanner_state.sqlite3", "mikan_state.sqlite3", "control_state.sqlite3", "series_metadata.sqlite3")',
            script,
        )
        self.assertIn("SHA256SUMS", script)
        self.assertIn('BACKUP_AI_CACHE="${BACKUP_AI_CACHE:-0}"', script)
        self.assertIn("rollback backup checksum verification failed", script)
        self.assertIn("containers remain stopped and no state files were overwritten", script)
        self.assertIn("migration_preflight.py", script)
        self.assertIn("tests.test_webui_backend tests.test_deployment_script", script)
        self.assertIn("overview_p95", script)
        self.assertIn("for _ in range(5):", script)
        self.assertIn("Exclude a fixed, bounded warm-up", script)
        self.assertIn("mikan_fallback_sources.json", script)
        self.assertIn("system.health_probe", script)
        self.assertIn("deployment_backup_retention.py", script)
        self.assertIn("deployment_backup_retention.py create", script)
        manifest_region = script[
            script.index('> "$BACKUP_DIR/images.json"') :
            script.index("Rehearsing every additive database migration")
        ]
        self.assertNotIn(
            "<<",
            manifest_region,
            "The checksum phase must use the tested Worker CLI, not an inline heredoc that can be parsed as shell code at runtime.",
        )
        self.assertIn("--state backup_verified", script)
        self.assertIn("--state deployment_completed", script)
        self.assertIn("--state deployment_failed", script)
        self.assertIn('SCANNER_STATE_RESTORE_DEPLOYMENT_ID="${SCANNER_STATE_RESTORE_DEPLOYMENT_ID:-}"', script)
        self.assertIn("/app/scanner_state_recovery.py", script)
        self.assertIn("scanner_state_auto_recovery.py", script)
        self.assertIn("--write-anchor", script)
        self.assertIn('RECOVERY_ANCHOR_DEPLOYMENT_ID="$DEPLOYMENT_ID"', script)
        self.assertIn('set -- "$@" --exclude "$SCANNER_STATE_RESTORE_DEPLOYMENT_ID"', script)
        self.assertIn("--success-log-root /logs", script)
        self.assertIn('--exclude "$DEPLOYMENT_ID"', script)
        self.assertIn('--newest "$BACKUP_RETENTION_NEWEST"', script)
        self.assertIn('--daily "$BACKUP_RETENTION_DAILY"', script)
        self.assertIn('--weekly "$BACKUP_RETENTION_WEEKLY"', script)
        self.assertIn("migrate_quality_sidecars.py", script)
        self.assertIn("--container-anime-root /anime", script)
        self.assertIn("/api/v2/events?limit=1", script)
        self.assertIn("duplicate open target review for torrent hash", script)
        self.assertIn("review summary exceeds 128 KiB", script)
        self.assertIn("review summary leaked detail fields", script)
        self.assertIn("review detail endpoint returned the wrong review id", script)
        self.assertIn("review detail endpoint did not return an item object", script)
        self.assertIn('review_detail = review_detail_payload.get("item")', script)
        self.assertIn("deployed v2 review contract is incomplete", script)
        self.assertIn("series artwork proxy did not return a verified image", script)
        self.assertIn("control review file-time migration is missing", script)
        self.assertIn("Mikan extraction file-time migration is incomplete", script)
        self.assertIn('"current_file_timestamp"', script)
        self.assertIn('"current_file_time_kind"', script)
        self.assertIn('"file_time_schema_verified": True', script)
        self.assertIn("/api/v2/review-items?status=open&limit=30&view=summary", script)
        self.assertIn("/api/v2/review-items/{review_id}", script)
        self.assertIn("/api/v2/review-items/batch-resolve", script)
        self.assertIn("/api/v2/series/{series_id}/artwork", script)
        self.assertIn('"review_summary_bytes": len(review_summary_raw)', script)
        self.assertIn("overview exceeds 20 KiB", script)
        self.assertIn("rolling back images and verified state backup", script)
        self.assertIn("docker image tag", script)
        self.assertIn("restore_ai_control", script)
        self.assertIn("extract_progress=", script)
        self.assertIn("extract_heartbeat_age_seconds=", script)
        self.assertIn("STATUS_REPORT_SECONDS", script)
        self.assertIn("COMMAND_PROBE_TIMEOUT_SECONDS", script)
        self.assertIn("command_deadline", script)
        self.assertIn("Worker command health probe waiting", script)
        self.assertIn("ai_scheduler_state.py", script)
        self.assertIn("AI scheduler heartbeat state is missing", script)
        self.assertIn("v2 overview is missing AI scheduler state", script)
        self.assertIn("Verifying AI scheduler recovery after deployment hold release", script)
        self.assertIn("AI scheduler did not recover after deployment hold release", script)
        self.assertIn("remove_container_for_recreate", script)
        self.assertIn("docker update --restart=no", script)
        self.assertIn("using restart-loop recovery boundary", script)
        self.assertIn('SOURCE_REVISION=$WORKER_SOURCE_REVISION', script)
        self.assertIn('SOURCE_REVISION=$WEBUI_SOURCE_REVISION', script)
        self.assertIn("verify_worker_image_sources", script)
        self.assertIn("verify_webui_image_sources", script)
        self.assertIn("compute_worker_source_revision", script)
        self.assertIn("compute_worker_python_revision", script)
        self.assertIn("compute_webui_source_revision", script)
        self.assertIn("assert_source_trees_unchanged", script)
        self.assertIn('assert_source_trees_unchanged "after-image-tests"', script)
        self.assertIn('assert_source_trees_unchanged "after-safe-endpoint-wait"', script)
        self.assertIn('assert_source_trees_unchanged "before-container-recreate"', script)
        self.assertIn('assert_source_trees_unchanged "after-container-recreate"', script)
        self.assertIn("contains a mixed or stale Python source set", script)
        self.assertIn("verify_worker_image_config", script)
        self.assertIn("New Worker image", script)
        self.assertIn("Rollback Worker image", script)
        self.assertIn("deployment and rollback are unsafe", script)
        self.assertIn("Worker build cache returned stale source", script)
        self.assertIn("WebUI build cache returned stale source", script)
        self.assertIn("build --no-cache", script)
        self.assertIn("/app/.source-revision", script)
        for source_name in (
            "transcriber.py",
            "worker.py",
            "retranslate_ai_lines.py",
            "repair_ai_outputs.py",
            "subtitle_extract.py",
            "safe_files.py",
            "config.py",
            "subtitle_quality.py",
            "selective_ai_cleanup.py",
            "deployment_backup_retention.py",
        ):
            self.assertIn(source_name, script)
        self.assertIn("Live Worker source verification failed", script)
        self.assertIn("Live Worker source revision verification failed", script)
        self.assertIn("Live WebUI source revision verification failed", script)
        self.assertIn("control_api.py", script)
        self.assertIn('docker exec -i "$WORKER_CONTAINER" cat /app/.source-revision', script)
        self.assertIn('docker exec -i "$WEBUI_CONTAINER" cat /app/.source-revision', script)
        for live_source_name in (
            "mikan_worker.py",
            "retranslate_ai_lines.py",
            "subtitle_extract.py",
            "safe_files.py",
            "output_manifest.py",
            "subtitle_paths.py",
            "deployment_backup_retention.py",
        ):
            live_verification_region = script[
                script.index("live_worker_source_revision=") : script.index("expected_webui_sha=")
            ]
            self.assertIn(live_source_name, live_verification_region)
        self.assertIn("worker-log-before.txt", script)
        self.assertIn("docker logs --timestamps --tail 500", script)
        self.assertNotIn("for _ in range(30):", script)
        self.assertIn('raw_extract_counts.get("running")', script)
        self.assertIn('verified_extract_counts.get("running")', script)
        self.assertIn("if isinstance(raw_extract_counts, dict):", script)
        self.assertIn("if isinstance(verified_extract_counts, dict):", script)
        self.assertNotIn('if "running" in extract_counts', script)
        self.assertNotIn(
            'if int(status.get("mikan", {}).get("state_db", {}).get("extract_jobs", {}).get("active") or 0):',
            script,
        )
        self.assertIn("only a genuinely running extraction", script)
        self.assertLess(
            script.index('SOURCE_REVISION=$WORKER_SOURCE_REVISION'),
            script.index("Entering deployment hold"),
        )
        self.assertLess(
            script.index('mkdir "$UPDATE_LOCK_DIR"'),
            script.index('SOURCE_REVISION=$WORKER_SOURCE_REVISION'),
        )
        self.assertLess(
            script.index('sh "$recovery_script" "$orphaned_deployment_id" --preview'),
            script.index('sh "$recovery_script" "$orphaned_deployment_id" --apply'),
        )
        self.assertLess(
            script.index("verify_worker_image_sources"),
            script.index("Entering deployment hold"),
        )
        self.assertLess(
            script.index('verify_worker_image_config "$OLD_WORKER_IMAGE_ID"'),
            script.index("Entering deployment hold"),
        )
        self.assertLess(script.index("Rehearsing every additive database migration"), script.index("Recreating Worker and WebUI"))
        recreate_index = script.index("Recreating Worker and WebUI")
        restore_index = script.index(
            "Restoring verified scanner state while all database consumers are stopped.",
            recreate_index,
        )
        self.assertLess(
            script.index('remove_container_for_recreate "$WEBUI_CONTAINER" 30', recreate_index),
            restore_index,
        )
        self.assertLess(
            restore_index,
            script.index('docker compose -f "$WORKER_DIR/docker-compose.yml"', restore_index),
        )
        self.assertLess(script.index("(cd \"$BACKUP_DIR\" && sha256sum -c SHA256SUMS)"), script.index("--state backup_verified"))
        self.assertLess(script.index("deployment_backup_retention.py prune"), script.rindex("\nrestore_ai_control\n"))
        self.assertLess(script.rindex('rm -f "$HOLD_FILE"'), script.index("--state deployment_completed"))
        self.assertLess(script.rindex("release_update_lock"), script.rindex("trap - 0 1 2 15"))

    def test_pre_retire_rollback_never_recreates_or_restores_live_state(self) -> None:
        script = Path("safe-update-stack.sh").read_text(encoding="utf-8")

        self.assertIn("CONTAINERS_RETIRED=0", script)
        branch_start = script.index('if [ "$CONTAINERS_RETIRED" = "0" ]; then')
        destructive_start = script.index(
            'echo "Deployment failed; rolling back images and verified state backup."',
            branch_start,
        )
        pre_retire_rollback = script[branch_start:destructive_start]
        self.assertIn("Pre-retire maintenance failed", pre_retire_rollback)
        self.assertIn('docker kill --signal=CONT "$WORKER_CONTAINER"', pre_retire_rollback)
        self.assertIn('docker image tag "$OLD_WORKER_IMAGE_ID" "$WORKER_IMAGE"', pre_retire_rollback)
        self.assertIn('docker image tag "$OLD_WEBUI_IMAGE_ID" "$WEBUI_IMAGE"', pre_retire_rollback)
        self.assertIn("restore_ai_control", pre_retire_rollback)
        self.assertIn('rm -f "$HOLD_FILE"', pre_retire_rollback)
        self.assertIn("release_update_lock", pre_retire_rollback)
        for forbidden in (
            "remove_container_for_recreate",
            "docker stop",
            "docker rm",
            "docker compose",
            "--force-recreate",
            'cp "$backup_database"',
            "scanner_state_recovery.py",
        ):
            self.assertNotIn(forbidden, pre_retire_rollback)

        recreate_phase = script.index('echo "[6/8] Recreating Worker and WebUI')
        retired_boundary = script.index("CONTAINERS_RETIRED=1", recreate_phase)
        first_retire = script.index(
            'remove_container_for_recreate "$WORKER_CONTAINER" 60',
            recreate_phase,
        )
        self.assertLess(retired_boundary, first_retire)

    def test_stale_ai_recovery_is_single_shot_strictly_guarded_and_fail_closed(self) -> None:
        script = Path("safe-update-stack.sh").read_text(encoding="utf-8")

        self.assertIn("stale_ai_recovery_attempted=0", script)
        self.assertIn("deployment_hold_active", script)
        self.assertIn("ai_running_stale", script)
        self.assertIn("scheduler_current_video", script)
        self.assertIn("ai_running_count == 1", script)
        self.assertIn("extract_active == 0", script)
        self.assertIn("not mikan_busy", script)
        guard = (
            'if [ "$stale_ai_recovery_attempted" = "0" ] '
            '&& [ "$stale_ai_recovery_eligible" = "1" ]; then'
        )
        self.assertIn(guard, script)
        recovery_start = script.index(guard)
        recovery_end = script.index(
            "if printf '%s\\n' \"$state\" | grep -q '^idle=1$'",
            recovery_start,
        )
        recovery = script[recovery_start:recovery_end]
        self.assertEqual(recovery.count("--requeue-stale-ai-running"), 1)
        self.assertIn('--volumes-from "$WORKER_CONTAINER"', recovery)
        self.assertIn('--entrypoint python "$WORKER_IMAGE"', recovery)
        self.assertLess(
            recovery.index("stale_ai_recovery_attempted=1"),
            recovery.index("--requeue-stale-ai-running"),
        )
        self.assertIn("deployment remains in the safe wait loop", recovery)
        self.assertIn("continue", recovery)
        self.assertNotIn("exit ", recovery)

    def test_stuck_extract_recovery_is_targeted_backed_up_and_uses_normal_rollback(self) -> None:
        script = Path("recover-stuck-extract-deployment.sh").read_text(encoding="utf-8")

        self.assertIn("PRAGMA quick_check", script)
        self.assertIn("connection.backup", script)
        self.assertIn("emergency_extract_recovery", script)
        self.assertIn("WHERE job_key=? AND status='running' AND worker_id=?", script)
        self.assertIn("status='terminal_failed'", script)
        self.assertIn("kill -TERM \"$DEPLOYMENT_PID\"", script)
        self.assertIn("safe-update-stack.sh", script)
        self.assertNotIn("DELETE FROM", script)
        self.assertNotIn("Remove-Item", script)

    def test_orphaned_deployment_recovery_is_explicit_and_pre_backup_only(self) -> None:
        script = Path("recover-orphaned-deployment.sh").read_text(encoding="utf-8")

        self.assertIn("[--preview|--apply]", script)
        self.assertIn('kill -0 "$DEPLOYMENT_PID"', script)
        self.assertIn("Deployment process $DEPLOYMENT_PID is still alive", script)
        self.assertIn('str(payload.get(\'deployment_id\') or \'\') != expected', script)
        self.assertIn('str(payload.get(\'reason\') or \'\') != \'safe-stack-update\'', script)
        self.assertIn('find "$BACKUP_DIR/databases" -type f', script)
        self.assertIn("pre-backup wait stage", script)
        self.assertIn('docker image tag "$LIVE_WORKER_IMAGE_ID" "$WORKER_IMAGE"', script)
        self.assertIn('docker image tag "$LIVE_WEBUI_IMAGE_ID" "$WEBUI_IMAGE"', script)
        self.assertIn('cp "$BACKUP_DIR/ai_control.json"', script)
        self.assertIn('mv "$HOLD_FILE" "$RECOVERED_HOLD_FILE"', script)
        self.assertIn('mv "$UPDATE_LOCK_DIR" "$RECOVERED_LOCK_DIR"', script)
        self.assertLess(
            script.index('if [ "$MODE" = "--preview" ]'),
            script.index('mv "$HOLD_FILE" "$RECOVERED_HOLD_FILE"'),
        )
        self.assertNotIn('rm -f "$HOLD_FILE"', script)
        self.assertNotIn('rmdir "$UPDATE_LOCK_DIR"', script)
        self.assertIn("post_status_check", script)
        self.assertIn("WebUI still reports an active deployment hold", script)
        self.assertNotIn("DELETE FROM", script)

    def test_orphaned_deployment_recovery_is_valid_for_posix_sh(self) -> None:
        shell = shutil.which("sh")
        if shell is None:
            self.skipTest("POSIX sh is unavailable on this host")
        result = subprocess.run(
            [shell, "-n", "recover-orphaned-deployment.sh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
