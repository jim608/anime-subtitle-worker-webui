from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_app_module():
    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class FastAPI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def middleware(self, *args, **kwargs):
            return lambda fn: fn

        def get(self, *args, **kwargs):
            return lambda fn: fn

        def post(self, *args, **kwargs):
            return lambda fn: fn

        def patch(self, *args, **kwargs):
            return lambda fn: fn

    class Request:
        pass

    responses = types.ModuleType("fastapi.responses")

    class FileResponse:
        def __init__(self, path: str, **kwargs) -> None:
            self.path = path
            self.media_type = kwargs.get("media_type")
            self.headers = kwargs.get("headers") or {}

    class PlainTextResponse:
        def __init__(self, *args, **kwargs) -> None:
            pass

    fastapi.FastAPI = FastAPI
    fastapi.HTTPException = HTTPException
    fastapi.Request = Request
    responses.FileResponse = FileResponse
    responses.PlainTextResponse = PlainTextResponse
    sys.modules.setdefault("fastapi", fastapi)
    sys.modules.setdefault("fastapi.responses", responses)

    spec = importlib.util.spec_from_file_location("webui_app_for_tests", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class WebuiBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_app_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.module.WORK_PATH = self.tmp / "work"
        self.module.LOG_PATH = self.tmp / "logs"
        self.module.CONFIG_PATH = self.tmp / "config.yaml"
        self.module.DOCKER_SOCKET = self.tmp / "missing-docker.sock"
        self.module.WORK_PATH.mkdir()
        self.module.LOG_PATH.mkdir()

    def test_scanner_recovery_watch_launches_isolated_worker_image_helper(self) -> None:
        recovery_id = "20260827T110000Z-777"
        (self.module.WORK_PATH / "deployment_hold.json").write_text(
            json.dumps({
                "active": True,
                "deployment_id": recovery_id,
                "reason": "scanner-state-corruption",
            }),
            encoding="utf-8",
        )
        calls: list[tuple[str, str, object]] = []

        def docker_request(method, path, body=None, **_kwargs):
            calls.append((method, path, body))
            if method == "GET" and path.endswith("anime-subtitle-scanner-recovery/json"):
                raise self.module.HTTPException(
                    status_code=502,
                    detail="Docker API error 404: no such container",
                )
            if method == "GET" and path.endswith("anime-subtitle-worker/json"):
                return {"Config": {"Image": "anime-subtitle-worker:latest"}}
            if method == "POST" and path.startswith("/containers/create?"):
                return {"Id": "helper-id"}
            if method == "POST" and path == "/containers/helper-id/start":
                return {}
            raise AssertionError((method, path, body))

        with patch.object(self.module, "_docker_request", side_effect=docker_request):
            launched = self.module._launch_scanner_recovery_helper({
                "status": "pending",
                "recovery_id": recovery_id,
            })

        self.assertTrue(launched)
        create = next(call for call in calls if call[1].startswith("/containers/create?"))
        payload = create[2]
        self.assertEqual(payload["Image"], "anime-subtitle-worker:latest")
        self.assertIn("/app/scanner_state_auto_recovery.py", payload["Cmd"])
        self.assertEqual(
            payload["HostConfig"]["VolumesFrom"],
            ["anime-subtitle-worker:rw"],
        )
        self.assertTrue(payload["HostConfig"]["AutoRemove"])

    def resource_admission_payload(self, *, updated_at: float) -> dict[str, object]:
        return {
            "schema_version": 1,
            "contract": "resource-admission-state-v1",
            "updated_at": updated_at,
            "sampled_at": updated_at,
            "max_age_seconds": 30.0,
            "last_oom_at": None,
            "last_oom": None,
            "hysteresis_state": {
                "effective_tier": "green",
                "candidate_tier": None,
                "consecutive_samples": 0,
            },
            "telemetry": {
                "sampled_at_epoch_seconds": updated_at,
                "available": True,
                "cpu_percent": 20.0,
                "ram_available_mib": 8192.0,
                "ram_total_mib": 16384.0,
                "gpu_util_percent": 30.0,
                "vram_free_mib": 9000.0,
                "vram_total_mib": 12288.0,
                "error_codes": [],
                "age_seconds": 0.0,
            },
            "decision": {
                "tier": "green",
                "allow_new_job": True,
                "allow_running_job": False,
                "asr_compute_type": "float16",
                "asr_model": "large-v3",
                "concurrency_limit": 1,
                "retry_after": 0.0,
                "reason_codes": ["admission_allowed"],
                "hysteresis_state": {
                    "effective_tier": "green",
                    "candidate_tier": None,
                    "consecutive_samples": 0,
                },
                "diagnostics": {},
            },
            "launch_plan": {
                "schema_version": 1,
                "contract": "resource-launch-plan-v1",
                "decision_id": "a" * 32,
                "video": {
                    "canonical_path": "/anime/Series/Season 1/Episode 01.mkv",
                    "size": 1000,
                    "mtime_ns": 2000,
                },
                "sampled_at": updated_at,
                "expires_at": updated_at + 30.0,
                "stage": "transcription",
                "admitted": True,
                "selected_route": {
                    "model": "large-v3",
                    "compute_type": "float16",
                    "required_vram_mib": 8500.0,
                },
                "effective": {
                    "concurrency": 1,
                    "batch_size": 8,
                    "translation_context_max_blocks": 4,
                    "translation_context_max_chars": 4000,
                    "whisperx_batch_size": 8,
                    "transformers_whisper_batch_size": 8,
                },
                "reason_codes": ["admission_allowed"],
                "tier": "green",
                "retry_at": 0.0,
            },
        }

    def _create_ai_delivery_slo_schema(
        self,
        connection: sqlite3.Connection,
        *,
        instrumented_at: float,
        include_queue: bool = True,
        include_inventory: bool = True,
        inventory_completed_at: float | None = None,
        include_measurement_revision: bool = True,
        measurement_revision: str | None = None,
    ) -> None:
        connection.execute(
            "CREATE TABLE ai_delivery_meta(key TEXT PRIMARY KEY, value TEXT, updated_at REAL)"
        )
        meta_rows = [
            ("schema_version", "1", instrumented_at),
            ("instrumented_at", str(instrumented_at), instrumented_at),
            ("inventory_schema_version", "1", instrumented_at),
            ("inventory_current_policy_revision", "policy-v1", instrumented_at),
            ("inventory_current_root_signature", "root-v1", instrumented_at),
            ("inventory_dirty_generation", "0", instrumented_at),
        ]
        if include_measurement_revision:
            meta_rows.append(
                (
                    "measurement_revision",
                    measurement_revision
                    or self.module.AI_DELIVERY_MEASUREMENT_REVISION,
                    instrumented_at,
                )
            )
        connection.executemany(
            "INSERT INTO ai_delivery_meta VALUES (?, ?, ?)",
            meta_rows,
        )
        strict_verification = json.dumps(
            {
                "publication_semantics_verified": True,
                "publication_contract": self.module.AI_DELIVERY_PUBLICATION_CONTRACT,
                "publication_kind": "translated_trilingual",
                "output_languages": ["ja", "zh-CN", "zh-TW"],
                "expected_policy_revision": "policy-v1",
                "manifest_policy_revision": "policy-v1",
                "policy_revision_matched": True,
            },
            separators=(",", ":"),
        ).replace("'", "''")
        connection.execute(
            f"""
            CREATE TABLE ai_delivery_obligations(
                obligation_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                eligible_at REAL NOT NULL,
                due_at REAL NOT NULL,
                verified_at REAL NOT NULL DEFAULT 0,
                terminal_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                exclusion_code TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER DEFAULT 0,
                canonical_path TEXT NOT NULL DEFAULT '',
                media_mtime_ns INTEGER NOT NULL DEFAULT 0,
                media_fingerprint TEXT NOT NULL DEFAULT 'fingerprint-v1',
                media_size INTEGER NOT NULL DEFAULT 1,
                policy_revision TEXT NOT NULL DEFAULT 'policy-v1',
                manifest_path TEXT NOT NULL DEFAULT '/work/manifest.json',
                manifest_sha256 TEXT NOT NULL DEFAULT '{'a' * 64}',
                verification_json TEXT NOT NULL DEFAULT '{strict_verification}'
            )
            """
        )
        if include_queue:
            connection.execute(
                """
                CREATE TABLE ai_candidate_queue(
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
        if include_inventory:
            connection.executescript(
                """
                CREATE TABLE ai_inventory_epochs(
                    epoch_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    measurement_revision TEXT NOT NULL,
                    policy_revision TEXT NOT NULL,
                    root_signature TEXT NOT NULL,
                    state TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL NOT NULL,
                    walk_error_count INTEGER NOT NULL DEFAULT 0,
                    observed_count INTEGER NOT NULL DEFAULT 0,
                    classified_count INTEGER NOT NULL DEFAULT 0,
                    delivery_required_count INTEGER NOT NULL DEFAULT 0,
                    tracked_count INTEGER NOT NULL DEFAULT 0,
                    untracked_count INTEGER NOT NULL DEFAULT 0,
                    legacy_preinstrumented_ai_count INTEGER NOT NULL DEFAULT 0,
                    coverage_complete INTEGER NOT NULL DEFAULT 0,
                    dirty_generation INTEGER NOT NULL DEFAULT 0,
                    failure_code TEXT NOT NULL DEFAULT '',
                    failure_detail TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE ai_media_inventory(
                    inventory_id TEXT PRIMARY KEY,
                    epoch_id TEXT NOT NULL,
                    canonical_path TEXT NOT NULL,
                    media_fingerprint TEXT NOT NULL,
                    media_size INTEGER NOT NULL,
                    media_mtime_ns INTEGER NOT NULL,
                    policy_revision TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    requires_ledger INTEGER NOT NULL,
                    obligation_id TEXT NOT NULL
                );
                """
            )
            completed_at = float(
                instrumented_at if inventory_completed_at is None else inventory_completed_at
            )
            connection.execute(
                """
                INSERT INTO ai_inventory_epochs(
                    epoch_id, schema_version, measurement_revision, policy_revision,
                    root_signature, state, started_at, updated_at, completed_at,
                    coverage_complete
                ) VALUES ('test-inventory', 1, ?, 'policy-v1', 'root-v1',
                          'completed', ?, ?, ?, 1)
                """,
                (
                    measurement_revision or self.module.AI_DELIVERY_MEASUREMENT_REVISION,
                    completed_at,
                    completed_at,
                    completed_at,
                ),
            )

    def _create_completed_delivery_fixture(
        self,
    ) -> tuple[dict[str, object], Path, Path, Path, Path]:
        input_root = self.tmp / "anime"
        completed_root = self.tmp / "completed"
        source = input_root / "Series" / "Episode.mkv"
        source.parent.mkdir(parents=True)
        completed_root.mkdir()
        source.write_bytes(b"source-video")
        config: dict[str, object] = {
            "completed_delivery_enabled": True,
            "completed_delivery_path": str(completed_root),
            "completed_delivery_manifest_path": str(
                self.module.WORK_PATH / "completed_delivery_manifests"
            ),
            "completed_delivery_source_policy": "retain",
            "completed_delivery_timeout_seconds": 7200,
            "ai_output_manifest_path": str(
                self.module.WORK_PATH / "ai_output_manifests"
            ),
            "input_path": str(input_root),
            "work_path": str(self.module.WORK_PATH),
        }
        source, destination, receipt, _marker, manifest = (
            self.module._completed_delivery_expected_paths(str(source), config)
        )
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"completed-video")
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"schema_version":2}\n', encoding="utf-8")
        source_stat = source.stat()
        output_stat = destination.stat()
        committed_at = max(time.time(), output_stat.st_mtime_ns / 1_000_000_000 + 0.001)
        payload = {
            "schema_version": 1,
            "contract": "completed-mkv-delivery-v1",
            "source": {
                "canonical_path": str(source),
                "media_size": source_stat.st_size,
                "media_mtime_ns": source_stat.st_mtime_ns,
                "media_fingerprint": "media-fingerprint",
                "sha256": self.module._completed_delivery_sha256(source),
            },
            "delivery": {
                "obligation_id": "obligation-1",
                "policy_revision": "policy-v1",
            },
            "publication_manifest": {
                "path": str(manifest),
                "sha256": self.module._completed_delivery_sha256(manifest),
            },
            "publication": {
                "contract": "ai-publication-semantics-v2",
                "kind": "adopted_zh_tw",
                "output_languages": ["zh-TW"],
            },
            "destination": str(destination),
            "state": "committed",
            "attempt_id": "attempt-1",
            "output": {
                "path": str(destination),
                "size": output_stat.st_size,
                "mtime_ns": output_stat.st_mtime_ns,
                "sha256": self.module._completed_delivery_sha256(destination),
            },
            "source_retained": True,
            "committed_at": committed_at,
        }
        receipt.parent.mkdir(parents=True)
        receipt.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        receipt_sha256 = self.module._completed_delivery_sha256(receipt)
        with sqlite3.connect(self.module.WORK_PATH / "scanner_state.sqlite3") as connection:
            connection.execute(
                """
                CREATE TABLE ai_delivery_obligations(
                    obligation_id TEXT PRIMARY KEY,
                    canonical_path TEXT NOT NULL,
                    policy_revision TEXT NOT NULL,
                    state TEXT NOT NULL,
                    verified_at REAL NOT NULL,
                    verification_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO ai_delivery_obligations VALUES (?, ?, ?, 'succeeded', ?, ?)",
                (
                    "obligation-1",
                    str(source),
                    "policy-v1",
                    committed_at,
                    json.dumps(
                        {
                            "completed_delivery_verified": True,
                            "completed_delivery_receipt": str(receipt),
                            "completed_delivery_committed_at": committed_at,
                            "completed_delivery_receipt_sha256": receipt_sha256,
                        }
                    ),
                ),
            )
        return config, source, destination, receipt, manifest

    def test_completed_delivery_disabled_never_reads_worker_evidence(self) -> None:
        config = {"completed_delivery_enabled": False}
        with (
            patch.object(self.module, "_completed_delivery_expected_paths") as paths,
            patch.object(self.module, "_completed_delivery_ledger_evidence") as ledger,
            patch.object(self.module, "_recent_ai_completed_summary") as recent,
        ):
            status = self.module._completed_delivery_status(
                "/anime/Episode.mkv",
                config,
                completed=True,
            )
            overview = self.module._completed_delivery_overview(config)

        self.assertEqual(
            status,
            {
                "enabled": False,
                "available": False,
                "state": "disabled",
                "final_path": "",
                "committed_at": 0.0,
                "size": 0,
                "hash": "",
                "error": "",
            },
        )
        paths.assert_not_called()
        ledger.assert_not_called()
        recent.assert_not_called()
        self.assertEqual(overview, status)

    def test_completed_delivery_strict_receipt_and_worker_ledger_are_available(self) -> None:
        config, source, destination, _receipt, _manifest = (
            self._create_completed_delivery_fixture()
        )

        status = self.module._completed_delivery_status(
            str(source),
            config,
            completed=True,
        )

        self.assertTrue(status["enabled"])
        self.assertTrue(status["available"])
        self.assertEqual(status["state"], "committed")
        self.assertEqual(status["final_path"], str(destination))
        self.assertEqual(status["size"], destination.stat().st_size)
        self.assertEqual(
            status["hash"],
            self.module._completed_delivery_sha256(destination),
        )
        self.assertGreater(status["committed_at"], 0)
        self.assertEqual(status["error"], "")

    def test_completed_delivery_missing_stale_and_unverified_fail_closed(self) -> None:
        config, source, destination, receipt, _manifest = (
            self._create_completed_delivery_fixture()
        )
        receipt.unlink()
        missing = self.module._completed_delivery_status(
            str(source), config, completed=True
        )
        self.assertEqual(missing["state"], "missing")
        self.assertFalse(missing["available"])
        self.assertEqual(missing["final_path"], "")
        self.assertEqual(missing["hash"], "")

        # Rebuild a fresh fixture and preserve stat metadata while changing
        # bytes; the cryptographic check must still reject the final artifact.
        self.tmp = Path(tempfile.mkdtemp())
        self.module.WORK_PATH = self.tmp / "work"
        self.module.LOG_PATH = self.tmp / "logs"
        self.module.WORK_PATH.mkdir()
        self.module.LOG_PATH.mkdir()
        config, source, destination, _receipt, _manifest = (
            self._create_completed_delivery_fixture()
        )
        stat = destination.stat()
        original = destination.read_bytes()
        replacement = bytes((byte + 1) % 256 for byte in original)
        self.assertEqual(len(replacement), len(original))
        destination.write_bytes(replacement)
        os.utime(destination, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.module._COMPLETED_DELIVERY_STATUS_CACHE.clear()
        stale = self.module._completed_delivery_status(
            str(source), config, completed=True
        )
        self.assertEqual(stale["state"], "stale")
        self.assertEqual(stale["error"], "final_artifact_hash_stale")
        self.assertFalse(stale["available"])
        self.assertEqual(stale["final_path"], "")

        destination.write_bytes(original)
        os.utime(destination, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.module._COMPLETED_DELIVERY_STATUS_CACHE.clear()
        with sqlite3.connect(self.module.WORK_PATH / "scanner_state.sqlite3") as connection:
            connection.execute("DELETE FROM ai_delivery_obligations")
        unverified = self.module._completed_delivery_status(
            str(source), config, completed=True
        )
        self.assertEqual(unverified["state"], "invalid")
        self.assertEqual(unverified["error"], "worker_delivery_evidence_missing")
        self.assertFalse(unverified["available"])

    def test_completed_delivery_stale_marker_and_old_contract_fail_closed(self) -> None:
        config, source, _destination, receipt, _manifest = (
            self._create_completed_delivery_fixture()
        )
        _source, _destination, _receipt, marker, _manifest = (
            self.module._completed_delivery_expected_paths(str(source), config)
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"state":"muxing"}\n', encoding="utf-8")
        stale_time = time.time() - 7201
        os.utime(marker, (stale_time, stale_time))
        marker_status = self.module._completed_delivery_status(
            str(source), config, completed=True, now=time.time()
        )
        self.assertEqual(marker_status["state"], "stale")
        self.assertEqual(marker_status["error"], "delivery_marker_stale")
        marker.unlink()

        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["contract"] = "completed-mkv-delivery-v0"
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        self.module._COMPLETED_DELIVERY_STATUS_CACHE.clear()
        invalid = self.module._completed_delivery_status(
            str(source), config, completed=True
        )
        self.assertEqual(invalid["state"], "invalid")
        self.assertEqual(invalid["error"], "receipt_contract_invalid")
        self.assertFalse(invalid["available"])

    def test_completed_delivery_overview_and_v2_task_detail_are_read_only(self) -> None:
        config, source, destination, _receipt, _manifest = (
            self._create_completed_delivery_fixture()
        )
        with patch.object(
            self.module,
            "_recent_ai_completed_summary",
            return_value=[{"path": str(source)}],
        ):
            overview = self.module._completed_delivery_overview(config)
        self.assertEqual(overview["state"], "committed")
        self.assertEqual(overview["final_path"], str(destination))

        task = {
            "path": str(source),
            "file_name": source.name,
            "effective_status": "done",
        }
        pending = {
            "path": str(source.parent / "Next.mkv"),
            "effective_status": "running",
        }
        with (
            patch.object(self.module, "_load_config", return_value=config),
            patch.object(
                self.module,
                "_dashboard_tasks_summary",
                return_value={
                    "tasks": [task, pending],
                    "counts": {"done": 1, "running": 1},
                    "filtered": 2,
                },
            ),
        ):
            detail = self.module.v2_ai_tasks(limit=10, fields="detail")
            compact = self.module.v2_ai_tasks(limit=10, fields="compact")

        self.assertEqual(
            detail["items"][0]["completed_delivery"]["state"],
            "committed",
        )
        self.assertEqual(
            detail["items"][0]["completed_delivery"]["final_path"],
            str(destination),
        )
        self.assertEqual(
            detail["items"][1]["completed_delivery"]["state"],
            "pending",
        )
        self.assertNotIn("completed_delivery", compact["items"][0])

    def test_series_sync_action_uses_worker_index_sync_script(self) -> None:
        self.assertEqual(
            self.module.ACTION_COMMANDS["series-sync"],
            ["python", "/app/series_metadata_sync.py", "--config", "/app/config.yaml"],
        )

    def test_safe_update_verifies_live_source_and_queue_database(self) -> None:
        script = (ROOT / "safe-update-webui.sh").read_text(encoding="utf-8")

        self.assertIn("expected_app_sha", script)
        self.assertIn("live_app_sha", script)
        self.assertIn("/api/queue?limit=1", script)
        self.assertIn("queue database verification failed", script)

    def test_database_maintenance_action_waits_for_idle_worker(self) -> None:
        self.assertEqual(
            self.module.ACTION_COMMANDS["database-maintenance"],
            [
                "python",
                "/app/database_maintenance.py",
                "--config",
                "/app/config.yaml",
                "--apply",
                "--wait-seconds",
                "900",
            ],
        )

    def test_ai_failure_summary_deduplicates_historical_events_by_video(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE ai_candidate_queue(path TEXT PRIMARY KEY, status TEXT, last_error TEXT)")
            conn.execute(
                "INSERT INTO ai_candidate_queue VALUES ('/anime/a.mkv', 'failed_retry', 'translation_prompt_leak')"
            )
            conn.execute(
                "CREATE TABLE ai_stage_events(path TEXT, stage TEXT, status TEXT, message TEXT, created_at REAL)"
            )
            conn.executemany(
                "INSERT INTO ai_stage_events VALUES (?, ?, 'failed', ?, ?)",
                [
                    ("/anime/a.mkv", "quality_check", "translation_prompt_leak", now),
                    ("/anime/a.mkv", "translation", "translation_prompt_leak", now - 1),
                    ("/anime/b.mkv", "transcription_review", "ASR review requested", now - 2),
                ],
            )

        summary = self.module._ai_failure_root_summary()

        self.assertEqual(summary["current_total"], 1)
        self.assertEqual(summary["affected_videos_7d"], 2)
        prompt_bucket = next(item for item in summary["buckets"] if item["key"] == "prompt_leak")
        self.assertEqual(prompt_bucket["current"], 1)
        self.assertEqual(prompt_bucket["affected_videos_7d"], 1)

    def test_ai_failure_summary_reports_current_outcomes_and_additive_latest_buckets(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE ai_candidate_queue(path TEXT PRIMARY KEY, status TEXT, last_error TEXT)")
            conn.executemany(
                "INSERT INTO ai_candidate_queue VALUES (?, ?, ?)",
                [
                    ("/anime/failed.mkv", "failed_retry", "translation_prompt_leak"),
                    ("/anime/queued.mkv", "queued", ""),
                    ("/anime/done.mkv", "done", ""),
                ],
            )
            conn.execute(
                "CREATE TABLE ai_stage_events(path TEXT, stage TEXT, status TEXT, message TEXT, created_at REAL)"
            )
            conn.executemany(
                "INSERT INTO ai_stage_events VALUES (?, ?, 'failed', ?, ?)",
                [
                    ("/anime/failed.mkv", "quality_check", "translation_prompt_leak", now),
                    ("/anime/failed.mkv", "translation", "translation failed", now - 10),
                    ("/anime/queued.mkv", "transcription", "whisper failed", now - 20),
                    ("/anime/done.mkv", "translation", "translation failed", now - 30),
                    ("/anime/missing.mkv", "audio", "audio extraction failed", now - 40),
                ],
            )

        summary = self.module._ai_failure_root_summary()

        self.assertEqual(summary["current_total"], 1)
        self.assertEqual(summary["affected_videos_7d"], 4)
        self.assertEqual(
            summary["outcomes_7d"],
            {"queued": 1, "failed_retry": 1, "done": 1, "missing": 1, "other": 0},
        )
        self.assertEqual(summary["historical_7d"]["current_outcomes"], summary["outcomes_7d"])
        self.assertEqual(summary["bucket_mode"], "latest_failure_per_video")
        self.assertTrue(summary["buckets_are_additive"])
        self.assertEqual(
            sum(bucket["affected_videos_7d"] for bucket in summary["buckets"]),
            summary["affected_videos_7d"],
        )

    def test_ai_failure_bucket_prioritizes_translation_omission_over_timing(self) -> None:
        self.assertEqual(
            self.module._ai_failure_bucket(
                "quality_check",
                '{"issues":[{"code":"long_duration"},{"code":"translation_safe_omission"}]}',
            ),
            "translation",
        )
        self.assertEqual(
            self.module._ai_failure_bucket(
                "quality_check",
                "large_gap and translation safe omission",
            ),
            "translation",
        )

    def test_ai_failure_summary_exposes_open_review_overlap_for_deduplication(self) -> None:
        video = self.tmp / "anime" / "Series" / "Episode.mkv"
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE ai_candidate_queue(path TEXT PRIMARY KEY, status TEXT, last_error TEXT)")
            conn.execute(
                "INSERT INTO ai_candidate_queue VALUES (?, 'failed_retry', 'translation_safe_omission')",
                (str(video),),
            )

        ai_review_id = "review_" + "1" * 24
        extract_review_id = "review_" + "2" * 24
        self._write_quality_review(review_id=ai_review_id, video=video)
        self._write_target_review(review_id=extract_review_id, candidates=[])
        summary = self.module._ai_failure_root_summary(
            {},
            extract_jobs={
                "counts": {"terminal_failed": 1},
                "recent_attention": [
                    {
                        "job_key": "hash:blocked",
                        "status": "terminal_failed",
                        "torrent_hash": "blocked",
                        "result": {"retryable": False, "review_id": extract_review_id},
                    }
                ],
            },
        )

        overlap = summary["review_overlap"]
        self.assertTrue(overlap["available"])
        self.assertEqual(overlap["open_total"], 2)
        self.assertEqual(overlap["ai_failed_retry"]["video_count"], 1)
        self.assertEqual(overlap["ai_failed_retry"]["review_ids"], [ai_review_id])
        self.assertEqual(overlap["terminal_extract"]["job_count"], 1)
        self.assertEqual(overlap["terminal_extract"]["review_ids"], [extract_review_id])
        self.assertEqual(overlap["raw_attention_total"], 4)
        self.assertEqual(overlap["deduplicated_attention_total"], 2)

    def test_ai_review_lines_merges_japanese_and_chinese_cache(self) -> None:
        cache = self.module.WORK_PATH / "ai_srt_cache"
        cache.mkdir(parents=True)
        digest = "0123456789abcdef"
        (cache / f"Episode.{digest}.AI.ja.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n",
            encoding="utf-8",
        )
        (cache / f"Episode.{digest}.AI.zh-CN.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n你好\n",
            encoding="utf-8",
        )

        review = self.module._ai_review_lines(self.module.WORK_PATH, digest)

        self.assertEqual(review["line_count"], 1)
        self.assertEqual(review["lines"][0]["japanese"], "こんにちは")
        self.assertEqual(review["lines"][0]["chinese"], "你好")

    def test_series_metadata_path_expands_anime_work_path_placeholder(self) -> None:
        path = self.module._series_metadata_db_path(
            {"series_metadata_db_path": "${ANIME_WORK_PATH:-/work}/series_metadata.sqlite3"}
        )

        self.assertEqual(path, self.module.WORK_PATH / "series_metadata.sqlite3")

    def test_health_expands_state_backup_path_placeholder(self) -> None:
        backup_dir = self.module.WORK_PATH / "state_backups" / "20260713-013108-787205"
        backup_dir.mkdir(parents=True)
        manifest = backup_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")

        summary = self.module._health_summary(
            {"state_backup_path": "${ANIME_WORK_PATH:-/work}/state_backups"},
            fast=True,
        )

        check = next(item for item in summary["checks"] if item["name"] == "state_backups")
        self.assertTrue(check["ok"])
        self.assertEqual(check["detail"], str(manifest))
        self.assertNotIn("${ANIME_WORK_PATH", check["detail"])

    def test_health_reports_unreadable_scanner_state_database(self) -> None:
        db_path = self.module.WORK_PATH / "scanner_state.sqlite3"
        db_path.write_bytes(b"not a sqlite database")

        summary = self.module._health_summary({}, fast=True)

        check = next(item for item in summary["checks"] if item["name"] == "scanner_state_db")
        self.assertFalse(check["ok"])
        self.assertEqual(check["severity"], "error")
        self.assertIn("file is not a database", check["detail"])

    def test_ai_scheduler_summary_exposes_retry_and_persistent_database_failure(self) -> None:
        now = time.time()
        (self.module.WORK_PATH / "ai_scheduler_state.json").write_text(
            json.dumps(
                {
                    "state": "error",
                    "reason_code": "scanner_database_disk_io",
                    "error": "disk I/O error",
                    "updated_at": now,
                    "state_changed_at": now - 90,
                    "next_retry_at": now + 15,
                    "consecutive_errors": 3,
                    "worker_pid": 42,
                }
            ),
            encoding="utf-8",
        )

        summary = self.module._ai_scheduler_summary({"watch_interval_seconds": 300})

        self.assertTrue(summary["exists"])
        self.assertTrue(summary["problem"])
        self.assertFalse(summary["stale"])
        self.assertEqual(summary["state"], "error")
        self.assertEqual(summary["reason_code"], "scanner_database_disk_io")
        self.assertEqual(summary["consecutive_errors"], 3)
        self.assertGreater(summary["retry_in_seconds"], 0)

        health = self.module._health_summary({}, fast=True)
        check = next(item for item in health["checks"] if item["name"] == "ai_scheduler")
        self.assertFalse(check["ok"])
        self.assertEqual(check["severity"], "error")
        self.assertIn("disk I/O error", check["detail"])

    def test_ai_scheduler_summary_marks_stopped_heartbeat_stale(self) -> None:
        now = time.time()
        (self.module.WORK_PATH / "ai_scheduler_state.json").write_text(
            json.dumps(
                {
                    "state": "idle",
                    "updated_at": now - 181,
                    "state_changed_at": now - 600,
                    "last_success_at": now - 600,
                }
            ),
            encoding="utf-8",
        )

        summary = self.module._ai_scheduler_summary({"watch_interval_seconds": 300})

        self.assertTrue(summary["stale"])
        self.assertTrue(summary["problem"])
        self.assertEqual(summary["stale_after_seconds"], 180.0)

    def test_dashboard_recommends_one_click_scheduler_retry(self) -> None:
        recommendations = self.module._dashboard_recommendations(
            {
                "queue_counts": {"queued": 7},
                "ai_scheduler": {
                    "state": "error",
                    "problem": True,
                    "reason_code": "scanner_database_disk_io",
                    "retry_in_seconds": 12,
                },
                "mikan": {"state_db": {"pipeline": {}, "extract_jobs": {"counts": {}}}},
            }
        )

        item = next(row for row in recommendations if row["key"] == "ai-scheduler")
        self.assertEqual(item["action"], "ai-scheduler-retry")
        self.assertIn("12 秒", item["detail"])
        self.assertIn("system.ai_scheduler_retry", self.module.V2_COMMAND_ACTIONS)

    def test_task_language_and_metadata_message_parsing(self) -> None:
        language = self.module._task_language_info(
            "language_uncertain",
            "Skipped source language gate: reason=language_uncertain language=unknown probability=0.52 allowed=ja confident=0 policy=skip samples=unknown:0.52@42s",
        )
        metadata = self.module._task_metadata_info(
            "Series metadata context ready: provider=anilist cached=1 chars=1200",
        )

        self.assertEqual(language["language"], "unknown")
        self.assertEqual(language["probability"], 0.52)
        self.assertFalse(language["allowed"])
        self.assertEqual(language["reason"], "language_uncertain")
        self.assertEqual(language["samples"], "unknown:0.52@42s")
        self.assertIn("Skipped source language gate", language["skip_reason"])
        self.assertEqual(metadata["provider"], "anilist")
        self.assertTrue(metadata["cached"])
        self.assertEqual(metadata["chars"], 1200)
        self.assertEqual(
            self.module._workflow_node_id(stage="language_uncertain", raw_status="done", job_status="skipped"),
            "input",
        )
        self.assertEqual(
            self.module._workflow_progress(
                node_id="translate",
                status="Running",
                message="Translating batch 28/61",
            ),
            46,
        )
        self.assertIsNone(
            self.module._workflow_progress(node_id="transcribe", status="Running", message="Running Whisper")
        )

    def test_sqlite_readonly_connection_blocks_writes(self) -> None:
        db_path = self.tmp / "readonly.sqlite3"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO items(value) VALUES ('ok')")

        with self.module._sqlite_connect(db_path) as conn:
            self.assertEqual(conn.execute("SELECT value FROM items").fetchone()[0], "ok")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO items(value) VALUES ('blocked')")

    def test_dashboard_tasks_read_does_not_migrate_or_create_tables(self) -> None:
        db_path = self.module.WORK_PATH / "scanner_state.sqlite3"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE ai_candidate_queue (
                    path TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO ai_candidate_queue(path, status, updated_at) VALUES (?, 'queued', ?)",
                ("/anime/Legacy Episode.mkv", time.time()),
            )

        payload = self.module._dashboard_tasks_summary(limit=10)

        self.assertEqual(payload["counts"], {"queued": 1})
        self.assertEqual(payload["tasks"][0]["file_name"], "Legacy Episode.mkv")
        with sqlite3.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_candidate_queue)")}
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(columns, {"path", "status", "updated_at"})
        self.assertNotIn("ai_job_state", tables)

    def test_mikan_pipeline_excludes_replacement_history_and_deduplicates_stages(self) -> None:
        pipeline = self.module._mikan_pipeline_counts(
            {
                "downloading": 1,
                "extracting_subtitles": 2,
                "completed_waiting_extract": 0,
                "extract_failed": 4,
                "completed": 12,
            },
            {"running": 2, "queued": 3, "failed": 4, "replaced": 209, "success": 99},
        )

        self.assertEqual(pipeline["extracting"], 2)
        self.assertEqual(pipeline["waiting_extract"], 3)
        self.assertEqual(pipeline["auto_replacing"], 4)
        self.assertEqual(pipeline["imported"], 99)
        self.assertNotIn("replaced", pipeline)

    def test_managed_subtitle_quality_report_is_attached_to_task(self) -> None:
        video = self.tmp / "anime" / "Series" / "Episode 01.mkv"
        video.parent.mkdir(parents=True)
        video.write_text("", encoding="utf-8")
        ass = video.with_name(video.stem + ".AI繁日雙語.zh-TW.ass")
        ass.write_text("", encoding="utf-8")
        report_path = self.module._managed_subtitle_quality_report_path(ass)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "status": "watchable",
                    "score": 98,
                    "dialogues": 321,
                    "has_failures": False,
                    "has_warnings": True,
                    "issues": [
                        {
                            "code": "translation_safe_omission",
                            "severity": "warning",
                            "message": "翻譯模型輸出異常，已安全省略 2 行",
                            "count": 2,
                            "samples": ["#9 原文 → ……", "#12 原文 → ……"],
                            "indexes": [9, "12", 0, "bad", 9],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.module.CONFIG_PATH.write_text(
            yaml.safe_dump({"ai_traditional_chinese_ass_suffix": ".AI繁日雙語.zh-TW.ass"}, allow_unicode=True),
            encoding="utf-8",
        )

        quality = self.module._subtitle_quality_for_video(str(video))

        self.assertIsNotNone(quality)
        assert quality is not None
        self.assertEqual(quality["report_path"], str(report_path))
        self.assertEqual(quality["status"], "watchable")
        self.assertEqual(quality["score"], 98)
        self.assertEqual(quality["dialogues"], 321)
        self.assertEqual(quality["issues"][0]["indexes"], [9, 12])
        self.assertEqual(quality["issues"][0]["samples"], ["#9 原文 → ……", "#12 原文 → ……"])

    def test_lite_status_uses_fast_queue_and_pending_snapshots(self) -> None:
        with sqlite3.connect(self.module.WORK_PATH / "scanner_state.sqlite3") as conn:
            conn.execute("CREATE TABLE ai_candidate_queue(path TEXT PRIMARY KEY, status TEXT NOT NULL)")
            conn.executemany(
                "INSERT INTO ai_candidate_queue(path, status) VALUES (?, ?)",
                [("/anime/one.mkv", "queued"), ("/anime/two.mkv", "done")],
            )
        (self.module.WORK_PATH / "mikan_pending.json").write_text(
            json.dumps(
                {
                    "items": {
                        "1:1": {
                            "bangumi_id": 1,
                            "episode": 1,
                            "title": "Completed E01",
                            "completed_at": "2026-06-22T00:00:00+00:00",
                            "last_extracted_at": "2026-06-22T00:00:00+00:00",
                            "last_extracted_count": 2,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        payload = self.module.status(lite=True)

        self.assertNotIn("library", payload)
        self.assertNotIn("eta", payload)
        self.assertIn("version", payload)
        self.assertIn("webui_fingerprint", payload["version"])
        self.assertIn("health", payload)
        self.assertIn("checks", payload["health"])
        self.assertFalse(payload["ai_control"]["paused"])
        self.assertEqual(payload["queue_counts"], {"done": 1, "queued": 1})
        self.assertEqual(payload["mikan"]["state_db"]["counts"]["completed"], 1)
        completed = payload["mikan"]["state_db"]["extract_jobs"]["recent_completed"]
        self.assertEqual(completed[0]["torrent_name"], "Completed E01")

    def test_lite_status_reports_current_ai_stage_without_full_queue_summary(self) -> None:
        now = time.time()
        with sqlite3.connect(self.module.WORK_PATH / "scanner_state.sqlite3") as conn:
            conn.execute(
                """
                CREATE TABLE ai_candidate_queue (
                    path TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    running_at REAL,
                    updated_at REAL,
                    added_at REAL,
                    last_error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE ai_job_state (
                    path TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ai_candidate_queue(path, status, running_at, updated_at, added_at)
                VALUES (?, 'running', ?, ?, ?)
                """,
                ("/anime/Series/Episode 01.mkv", now - 600, now - 600, now - 600),
            )
            conn.execute(
                """
                INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at)
                VALUES (?, 'translation', 'running', 'Translating batch 2/35', ?, ?)
                """,
                ("/anime/Series/Episode 01.mkv", now - 600, now - 30),
            )

        payload = self.module.status(lite=True)

        self.assertEqual(payload["current_ai"]["file_name"], "Episode 01.mkv")
        self.assertEqual(payload["current_ai"]["stage"], "translation")
        self.assertEqual(payload["current_ai"]["message"], "Translating batch 2/35")
        self.assertFalse(payload["current_ai"]["running_stale"])
        self.assertFalse(payload["io_policy"]["ai_disk_active"])
        self.assertEqual(payload["io_policy"]["extract_workers_effective"], 2)

        with sqlite3.connect(self.module.WORK_PATH / "scanner_state.sqlite3") as conn:
            conn.execute(
                "UPDATE ai_job_state SET stage = 'transcription', message = 'Running Whisper'"
            )
        transcription_payload = self.module.status(lite=True)
        self.assertTrue(transcription_payload["io_policy"]["ai_disk_active"])
        self.assertEqual(transcription_payload["io_policy"]["extract_workers_effective"], 1)

    def test_legacy_ai_control_pause_and_resume_use_worker_mailbox(self) -> None:
        paused = self.module.set_ai_control("pause")

        self.assertTrue(paused["ok"])
        self.assertEqual(paused["action"], "system.ai_queue_pause")
        self.assertFalse((self.module.WORK_PATH / "ai_control.json").exists())

        resumed = self.module.set_ai_control("resume")

        self.assertEqual(resumed["action"], "system.ai_queue_resume")
        actions = {
            json.loads(path.read_text(encoding="utf-8"))["action"]
            for path in (self.module.WORK_PATH / "control_inbox").glob("cmd_*.json")
        }
        self.assertEqual(actions, {"system.ai_queue_pause", "system.ai_queue_resume"})

    def test_ai_control_rejects_unknown_action(self) -> None:
        with self.assertRaises(self.module.HTTPException) as raised:
            self.module.set_ai_control("toggle")

        self.assertEqual(raised.exception.status_code, 404)

    def test_restart_worker_allows_docker_stop_grace_period_before_http_timeout(self) -> None:
        with patch.object(self.module, "_docker_request") as docker_request:
            self.module._run_restart_worker_action()

        docker_request.assert_called_once_with(
            "POST",
            f"/containers/{self.module.WORKER_CONTAINER_NAME}/restart?t=10",
            timeout_seconds=30.0,
        )
        self.assertTrue(self.module._action_snapshot()["ok"])

    def test_cancel_pending_mikan_redownload_uses_worker_mailbox(self) -> None:
        request_path = self.module.WORK_PATH / "mikan_redownload_all.request.json"
        request_path.write_text('{"action":"redownload_all_torrents_and_enqueue"}', encoding="utf-8")

        result = self.module.cancel_mikan_redownload()

        self.assertEqual(result["action"], "mikan.cancel_redownload")
        self.assertTrue(request_path.exists())
        self.assertFalse((self.module.WORK_PATH / "mikan_redownload_all.cancel.json").exists())

    def test_cancel_active_mikan_redownload_does_not_write_marker_from_webui(self) -> None:
        active_path = self.module.WORK_PATH / "mikan_redownload_all.active.json"
        active_path.write_text(
            '{"action":"redownload_all_torrents_and_enqueue","stage":"scan_missing"}',
            encoding="utf-8",
        )

        result = self.module.cancel_mikan_redownload()

        self.assertEqual(result["action"], "mikan.cancel_redownload")
        cancel_path = self.module.WORK_PATH / "mikan_redownload_all.cancel.json"
        self.assertFalse(cancel_path.exists())

    def test_mikan_download_entry_reports_subtitle_state(self) -> None:
        row = self.module._mikan_download_entry(
            "1:1",
            {
                "source": "mikan",
                "title": "Anime 01",
                "completed_at": "2026-06-22T00:00:00+00:00",
                "last_extracted_count": 1,
            },
            time.time(),
        )

        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["subtitle_state"], "official_ready")

    def test_mikan_download_entry_keeps_torrent_times_distinct_from_file_time(self) -> None:
        row = self.module._mikan_download_entry(
            "1:1",
            {
                "title": "Anime 01",
                "torrent_url": "https://example.invalid/anime.torrent",
                "queued_at": "2026-07-16T10:00:00+00:00",
                "pub_date": "2026-07-15T09:00:00+00:00",
                "last_qbit_added_on": 1_784_196_300,
                "last_qbit_completion_on": 1_784_196_600,
            },
            time.time(),
        )

        self.assertEqual(row["source_published_at"], 1_784_106_000)
        self.assertEqual(row["torrent_added_at"], 1_784_196_300)
        self.assertEqual(row["torrent_completed_at"], 1_784_196_600)

        recovered = self.module._mikan_download_entry(
            "1:2",
            {
                "source": "qbit-recovered",
                "title": "Recovered Anime 01",
                "torrent_url": "qbit://hash",
                "pub_date": "2026-07-15T09:00:00+00:00",
                "last_qbit_completion_on": 1_784_196_600,
            },
            time.time(),
        )
        self.assertEqual(recovered["source_published_at"], 0.0)
        self.assertEqual(recovered["torrent_completed_at"], 1_784_196_600)

        archived_recovered = self.module._mikan_download_entry(
            "1:3",
            {
                "last_completed_source": "qbit-recovered",
                "last_completed_title": "Archived Recovered Anime 01",
                "last_completed_torrent_url": "qbit://archived-hash",
                "pub_date": "2026-07-15T09:00:00+00:00",
            },
            time.time(),
        )
        self.assertEqual(archived_recovered["title"], "Archived Recovered Anime 01")
        self.assertEqual(archived_recovered["source"], "qbit-recovered")
        self.assertEqual(archived_recovered["source_published_at"], 0.0)

    def test_retry_all_failures_uses_worker_mailbox_without_database_write(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE ai_candidate_queue(path TEXT PRIMARY KEY, status TEXT NOT NULL)")
            conn.execute("INSERT INTO ai_candidate_queue VALUES('/anime/failed.mkv', 'failed_retry')")
        before = db.read_bytes()
        config = {"work_path": str(self.module.WORK_PATH), "control_inbox_path": "control_inbox"}

        with patch.object(self.module, "_load_config", return_value=config):
            result = self.module.run_action("retry-all-failures")

        self.assertEqual(result["action"], "system.retry_all_failures")
        self.assertEqual(db.read_bytes(), before)
        command = json.loads(
            (self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(command["action"], "system.retry_all_failures")

    def test_fast_queue_counts_treats_done_with_skipped_job_as_skipped(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.executescript(
                """
                CREATE TABLE ai_candidate_queue (path TEXT PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE ai_job_state (
                    path TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                INSERT INTO ai_candidate_queue(path, status) VALUES
                    ('/anime/done.mkv', 'done'),
                    ('/anime/skipped.mkv', 'done'),
                    ('/anime/queued.mkv', 'queued');
                INSERT INTO ai_job_state(path, stage, status, updated_at) VALUES
                    ('/anime/done.mkv', 'complete', 'ok', 1),
                    ('/anime/skipped.mkv', 'skipped', 'skipped', 1);
                """
            )

        self.assertEqual(self.module._fast_queue_counts(), {"done": 1, "queued": 1, "skipped": 1})

    def test_queue_summary_treats_done_with_skipped_job_as_skipped(self) -> None:
        self.create_queue_db()
        path = "/jellyfin_anime/Series/Season 1/Done But Skipped.mkv"
        now = time.time()
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                INSERT INTO ai_candidate_queue(path, mtime_ns, status, attempts, added_at, updated_at)
                VALUES (?, 1, 'done', 0, ?, ?)
                """,
                (path, now, now),
            )
            conn.execute(
                """
                INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                VALUES (?, 'language_gate', 'skipped', 'Language not allowed', ?, ?, ?)
                """,
                (path, now, now, now),
            )

        skipped = self.module._queue_summary(status_filter="skipped", search="Done But Skipped", limit=10)
        done = self.module._queue_summary(status_filter="done", search="Done But Skipped", limit=10)

        self.assertEqual(skipped["filtered"], 1)
        self.assertEqual(skipped["recent"][0]["status"], "skipped")
        self.assertEqual(done["filtered"], 0)

    def create_queue_db(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                CREATE TABLE ai_candidate_queue (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'scan',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    running_at REAL,
                    last_error TEXT,
                    last_error_at REAL,
                    next_retry_at REAL,
                    force_ai INTEGER NOT NULL DEFAULT 0,
                    added_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            for index, status in enumerate(["queued", "running", "failed_retry", "done"], start=1):
                path = f"/jellyfin_anime/Series/Season 1/Episode {index:02d}.mkv"
                conn.execute(
                    """
                    INSERT INTO ai_candidate_queue(path, mtime_ns, status, attempts, running_at, last_error, added_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path,
                        index,
                        status,
                        2 if status == "failed_retry" else 0,
                        now - 86400 if status == "running" else None,
                        "boom" if status == "failed_retry" else None,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ai_job_state (
                        path TEXT PRIMARY KEY,
                        stage TEXT NOT NULL,
                        status TEXT NOT NULL,
                        message TEXT,
                        started_at REAL,
                        updated_at REAL NOT NULL,
                        finished_at REAL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path,
                        "transcription" if status == "running" else status,
                        "running" if status == "running" else status,
                        f"{status} message",
                        now - 20,
                        now - 10,
                        now if status == "done" else None,
                    ),
                )
            conn.execute(
                """
                CREATE TABLE ai_stage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            for offset, stage, status in [
                (60, "complete", "ok"),
                (120, "translation", "running"),
                (180, "failed", "failed"),
            ]:
                conn.execute(
                    """
                    INSERT INTO ai_stage_events(path, stage, status, message, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("/jellyfin_anime/Series/Season 1/Episode 04.mkv", stage, status, f"{stage} message", now - offset),
                )
            conn.execute(
                """
                CREATE TABLE video_scan_cache (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    sidecar_signature TEXT NOT NULL,
                    config_signature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            for index, status in enumerate(["needs_ai", "needs_ai", "finished", "local_chinese", "embedded_chinese"], start=1):
                conn.execute(
                    """
                    INSERT INTO video_scan_cache(path, size, mtime_ns, sidecar_signature, config_signature, status, schema_version, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"/jellyfin_anime/Series/Season 1/Library {index:02d}.mkv",
                        1000 + index,
                        index,
                        "sidecar",
                        "config",
                        status,
                        1,
                        now,
                    ),
                )

    def queue_item(self, path: str, *, status_filter: str | None = None) -> dict:
        summary = self.module._queue_summary(status_filter=status_filter, search=Path(path).name, limit=50)
        for item in summary["recent"]:
            if item["path"] == path:
                return item
        self.fail(f"Queue item not found in summary: {path}")

    def call_queue_action_route(self, action: str, path: str) -> dict:
        class FakeRequest:
            headers = {"idempotency-key": f"legacy-route-{action}"}

            async def json(self) -> dict:
                return {"path": path}

        config = {"input_path": "/jellyfin_anime", "work_path": str(self.module.WORK_PATH)}
        with patch.object(self.module, "_load_config", return_value=config):
            return asyncio.run(self.module.queue_action(action, FakeRequest()))

    def test_queue_summary_counts_every_status(self) -> None:
        self.create_queue_db()
        summary = self.module._queue_summary(limit=50)
        self.assertEqual(summary["counts"], {"done": 1, "failed_retry": 1, "queued": 1, "running": 1})
        self.assertEqual(summary["filtered"], 4)
        self.assertEqual(len(summary["recent"]), 4)
        self.assertTrue(any("job" in item for item in summary["recent"]))
        self.assertEqual(summary["ready"], 2)
        self.assertEqual(summary["stale_running"], 0)
        self.assertEqual(summary["recoverable_running"], 0)
        running_item = self.queue_item("/jellyfin_anime/Series/Season 1/Episode 02.mkv", status_filter="running")
        self.assertGreater(running_item["running_started_at"], 0)
        self.assertGreater(running_item["heartbeat_at"], 0)
        self.assertFalse(running_item["running_stale"])
        self.assertFalse(running_item["running_recoverable"])
        self.assertEqual(running_item["completed_at"], 0)
        done_item = self.queue_item("/jellyfin_anime/Series/Season 1/Episode 04.mkv", status_filter="done")
        self.assertGreater(done_item["completed_at"], 0)
        self.assertEqual(done_item["running_started_at"], 0)

    def test_queue_uses_detailed_stage_event_instead_of_generic_worker_failure(self) -> None:
        self.create_queue_db()
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        path = "/jellyfin_anime/Series/Season 1/Episode 03.mkv"
        now = time.time()
        detail = "subtitle quality fail status=rerun issues=translation_prompt_leak"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                UPDATE ai_candidate_queue
                SET last_error = 'worker returned false', last_error_at = ?, updated_at = ?
                WHERE path = ?
                """,
                (now, now, path),
            )
            conn.execute(
                """
                UPDATE ai_job_state
                SET stage = 'failed', status = 'failed', message = 'worker returned false', updated_at = ?
                WHERE path = ?
                """,
                (now, path),
            )
            conn.execute(
                """
                INSERT INTO ai_stage_events(path, stage, status, message, created_at)
                VALUES (?, 'quality_check', 'failed', ?, ?)
                """,
                (path, detail, now),
            )

        item = self.queue_item(path, status_filter="failed_retry")
        self.assertEqual(item["last_error"], detail)

        tasks = self.module._dashboard_tasks_summary(
            limit=10,
            status_filter="failed_retry",
        )["tasks"]
        task = next(task for task in tasks if task["path"] == path)
        self.assertEqual(task["message"], detail)

    def test_queue_summary_orders_by_worker_queue_priority(self) -> None:
        self.create_queue_db()
        summary = self.module._queue_summary(limit=50)
        self.assertEqual(
            [item["status"] for item in summary["recent"][:3]],
            ["queued", "failed_retry", "running"],
        )
        self.assertTrue(summary["recent"][0]["path"].endswith("Episode 01.mkv"))
        self.assertTrue(summary["recent"][1]["path"].endswith("Episode 03.mkv"))

    def test_queue_summary_orders_newly_added_before_older_high_mtime_backlog(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        old_path = "/jellyfin_anime/Series/Season 1/Old Backlog.mkv"
        new_path = "/jellyfin_anime/Series/Season 1/New Arrival.mkv"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                CREATE TABLE ai_candidate_queue (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'scan',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    running_at REAL,
                    last_error TEXT,
                    last_error_at REAL,
                    next_retry_at REAL,
                    force_ai INTEGER NOT NULL DEFAULT 0,
                    added_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.executescript(
                """
                CREATE TABLE ai_job_state (
                    path TEXT PRIMARY KEY, stage TEXT NOT NULL, status TEXT NOT NULL,
                    message TEXT, started_at REAL, updated_at REAL NOT NULL, finished_at REAL
                );
                CREATE TABLE ai_stage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, stage TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT, created_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT INTO ai_candidate_queue(path, mtime_ns, status, added_at, updated_at)
                VALUES (?, 9999, 'queued', ?, ?), (?, 1, 'queued', ?, ?)
                """,
                (old_path, now, now, new_path, now + 100, now + 100),
            )

        summary = self.module._queue_summary(limit=10)
        self.assertEqual(summary["recent"][0]["path"], new_path)
        self.assertEqual(summary["recent"][1]["path"], old_path)

    def test_force_ai_action_uses_worker_mailbox(self) -> None:
        self.create_queue_db()
        path = "/jellyfin_anime/Series/Season 1/Episode 04.mkv"
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        before = database.read_bytes()

        result = self.call_queue_action_route("force-ai", path)

        self.assertEqual(result["action"], "ai.force")
        self.assertEqual(database.read_bytes(), before)

    def test_retranscribe_action_uses_worker_mailbox(self) -> None:
        self.create_queue_db()
        path = "/jellyfin_anime/Series/Season 1/Episode 04.mkv"

        result = self.call_queue_action_route("retranscribe", path)

        self.assertEqual(result["action"], "ai.retranscribe")
        command = json.loads(
            (self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(command["target"], path)
        self.assertEqual(command["action"], "ai.retranscribe")

    def test_all_queue_actions_are_mapped_to_worker_commands(self) -> None:
        self.create_queue_db()
        queued_path = "/jellyfin_anime/Series/Season 1/Episode 01.mkv"
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        before = database.read_bytes()
        expected = {
            "priority": "ai.prioritize",
            "clear-failure": "ai.retry",
            "pause": "ai.pause",
            "skip": "ai.skip",
            "retry": "ai.retry",
            "force-ai": "ai.force",
            "recover-running": "ai.recover",
        }

        for action, worker_action in expected.items():
            with self.subTest(action=action):
                result = self.call_queue_action_route(action, queued_path)
                self.assertEqual(result["action"], worker_action)

        self.assertEqual(database.read_bytes(), before)

    def test_queue_summary_marks_running_over_capacity_recoverable(self) -> None:
        self.create_queue_db()
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        active_running_path = "/jellyfin_anime/Series/Season 1/Active Running.mkv"
        old_running_path = "/jellyfin_anime/Series/Season 1/Old Running.mkv"
        with sqlite3.connect(db) as conn:
            for path, offset, message in [
                (active_running_path, 5, "active running"),
                (old_running_path, 60, "old running"),
            ]:
                conn.execute(
                    """
                    INSERT INTO ai_candidate_queue(path, mtime_ns, status, attempts, running_at, added_at, updated_at)
                    VALUES (?, ?, 'running', 0, ?, ?, ?)
                    """,
                    (path, 20, now - offset, now - offset, now - offset),
                )
                conn.execute(
                    """
                    INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                    VALUES (?, 'translation', 'running', ?, ?, ?, NULL)
                    """,
                    (path, message, now - offset, now - offset),
                )

        summary = self.module._queue_summary(limit=50, stale_running_seconds=21600, max_concurrent_videos=1)
        self.assertEqual(summary["counts"]["running"], 3)
        self.assertGreaterEqual(summary["recoverable_running"], 1)
        old_item = self.queue_item(old_running_path, status_filter="running")
        self.assertTrue(old_item["running_orphaned"])
        self.assertTrue(old_item["running_recoverable"])

    def test_queue_action_route_accepts_json_body(self) -> None:
        self.create_queue_db()
        queued_path = "/jellyfin_anime/Series/Season 1/Episode 01.mkv"
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        before = database.read_bytes()
        result = self.call_queue_action_route("skip", queued_path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "ai.skip")
        self.assertEqual(database.read_bytes(), before)
        command_path = self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json"
        command = json.loads(command_path.read_text(encoding="utf-8"))
        self.assertEqual(command["target"], queued_path)
        self.assertEqual(self.queue_item(queued_path, status_filter="queued")["status"], "queued")

    def test_queue_actions_work_for_virtual_done_items(self) -> None:
        self.create_queue_db()
        event_done_path = "/jellyfin_anime/Series/Season 1/Virtual Done.mkv"
        now = time.time()
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                INSERT INTO ai_stage_events(path, stage, status, message, created_at)
                VALUES (?, 'complete', 'ok', 'virtual completion', ?)
                """,
                (event_done_path, now),
            )

        self.assertEqual(self.queue_item(event_done_path, status_filter="done")["status"], "done")
        result = self.call_queue_action_route("skip", event_done_path)
        self.assertEqual(result["action"], "ai.skip")
        self.assertEqual(self.queue_item(event_done_path, status_filter="done")["status"], "done")

    def test_scan_cache_finished_is_not_virtual_ai_done(self) -> None:
        self.create_queue_db()
        cache_done_path = "/jellyfin_anime/Series/Season 1/Library 03.mkv"

        summary = self.module._queue_summary(status_filter="done", search="Library 03", limit=50)

        self.assertEqual(summary["filtered"], 0)
        self.assertFalse(any(item["path"] == cache_done_path for item in summary["recent"]))

    def test_eta_summary_uses_library_and_completion_rate(self) -> None:
        self.create_queue_db()
        self.module._ETA_SUMMARY_CACHE.clear()
        summary = self.module._eta_summary()
        self.assertTrue(summary["exists"])
        self.assertEqual(summary["needs_ai"], 2)
        self.assertEqual(summary["active_queue"], 3)
        self.assertEqual(summary["remaining"], 3)
        self.assertGreater(summary["rate_per_hour"], 0)
        self.assertIsNotNone(summary["eta_hours"])

    def test_eta_summary_does_not_count_queue_only_done_as_generated(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE video_scan_cache(path TEXT PRIMARY KEY, status TEXT NOT NULL)")
            conn.execute("INSERT INTO video_scan_cache(path, status) VALUES ('/anime/needs.mkv', 'needs_ai')")
            conn.execute(
                """
                CREATE TABLE ai_candidate_queue (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'scan',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    running_at REAL,
                    last_error TEXT,
                    last_error_at REAL,
                    next_retry_at REAL,
                    added_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO ai_candidate_queue(path, mtime_ns, status, added_at, updated_at) VALUES ('/anime/existing.mkv', 1, 'done', ?, ?)",
                (now - 10, now),
            )
            conn.execute(
                """
                CREATE TABLE ai_job_state (
                    path TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )

        summary = self.module._eta_summary()

        self.assertEqual(summary["completed_last_1h"], 0)
        self.assertEqual(summary["rate_per_hour"], 0)
        self.assertIsNone(summary["eta_hours"])

    def test_eta_summary_reports_unknown_remaining_when_database_is_unavailable(self) -> None:
        self.module._ETA_SUMMARY_CACHE.clear()
        missing = self.module._eta_summary()
        self.assertFalse(missing["available"])
        self.assertEqual(missing["error_code"], "scanner_database_missing")
        self.assertIsNone(missing["remaining"])

        (self.module.WORK_PATH / "scanner_state.sqlite3").write_bytes(b"not-a-sqlite-database")
        self.module._ETA_SUMMARY_CACHE.clear()
        unreadable = self.module._eta_summary()
        self.assertFalse(unreadable["available"])
        self.assertEqual(unreadable["error_code"], "scanner_database_unavailable")
        self.assertIsNone(unreadable["remaining"])

    def test_events_summary_returns_recent_stage_events(self) -> None:
        self.create_queue_db()
        self.module._EVENTS_SUMMARY_CACHE.clear()
        summary = self.module._events_summary(limit=2)
        self.assertTrue(summary["exists"])
        self.assertEqual(len(summary["recent"]), 2)
        self.assertEqual(summary["recent"][0]["stage"], "complete")
        self.assertIn("complete:ok", summary["counts"])

    def test_events_summary_includes_mikan_download_events(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        created_at = time.time()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE mikan_download_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    bangumi_id INTEGER,
                    episode INTEGER,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_download_events(key, bangumi_id, episode, event, detail, created_at)
                VALUES ('1:2', 1, 2, 'source_changed', 'source=a->b', ?)
                """,
                (created_at,),
            )
            conn.commit()
        finally:
            conn.close()

        summary = self.module._events_summary(limit=5)

        self.assertTrue(summary["exists"])
        self.assertTrue(summary["table_exists"])
        self.assertEqual(summary["recent"][0]["stage"], "mikan")
        self.assertEqual(summary["recent"][0]["status"], "source_changed")
        self.assertEqual(summary["recent"][0]["severity"], "queued")
        self.assertIn("bangumi 1 / EP 02", summary["recent"][0]["message"])
        self.assertEqual(summary["counts"]["mikan:source_changed"], 1)

        self.module._EVENTS_SUMMARY_CACHE.clear()
        compact = self.module._events_summary(limit=5, include_counts=False)
        self.assertEqual(compact["counts"], {})
        self.assertEqual(compact["recent"][0]["severity"], "queued")

    def test_failure_event_never_looks_successful_even_with_completed_status(self) -> None:
        severity = self.module._mikan_timeline_event_severity(
            "failure_recorded",
            "status=extracting_subtitles->completed failed=0->1",
            {"status": "completed", "reason": "no_subtitle_streams"},
        )

        self.assertEqual(severity, "warn")

    def test_v2_events_are_compact_structured_and_hide_technical_details(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        created_at = time.time()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE mikan_download_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    bangumi_id INTEGER,
                    episode INTEGER,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    event_key TEXT NOT NULL DEFAULT '',
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    last_seen_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO mikan_download_events(
                    key, bangumi_id, episode, event, detail, detail_json,
                    event_key, occurrence_count, last_seen_at, created_at
                ) VALUES (?, 1, ?, 'failure_recorded', ?, ?, ?, 3, ?, ?)
                """,
                [
                    (
                        f"series:{episode}",
                        episode,
                        "status=completed reason=target_ambiguity source=https://secret.example/private.torrent " + "x" * 2000,
                        json.dumps({"status": "completed", "reason": "target_ambiguity"}),
                        f"event-{episode}",
                        created_at + episode,
                        created_at + episode,
                    )
                    for episode in range(1, 101)
                ],
            )
            conn.commit()
        finally:
            conn.close()

        self.module._EVENTS_SUMMARY_CACHE.clear()
        payload = self.module.v2_events(limit=100)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        self.assertEqual(len(payload["items"]), 50)
        self.assertIsNotNone(payload["next_cursor"])
        self.assertLess(len(encoded), 20 * 1024)
        self.assertTrue(all(item["severity"] == "warn" for item in payload["items"]))
        self.assertTrue(all(item["occurrence_count"] == 3 for item in payload["items"]))
        self.assertNotIn("https://secret.example", encoded.decode("utf-8"))
        self.assertNotIn("technical_detail", payload["items"][0])
        self.assertIn("等待確認", payload["items"][0]["description"])

    def test_status_route_keeps_large_lists_out_of_status_payload(self) -> None:
        self.create_queue_db()
        (self.module.WORK_PATH / "mikan_pending.json").write_text('{"items":{}}', encoding="utf-8")

        result = self.module.status()

        self.assertNotIn("queue", result)
        self.assertIn("mikan", result)
        self.assertNotIn("downloads", result["mikan"])

    def test_workflow_tasks_map_queue_items_to_nodes(self) -> None:
        self.create_queue_db()

        payload = self.module._workflow_tasks_summary(limit=50)

        self.assertTrue(payload["exists"])
        labels = [node["label"] for node in self.module._workflow_nodes()]
        self.assertEqual(labels, ["Input File", "Whisper Transcribe", "LLM Translate", "Output File"])
        by_name = {item["file_name"]: item for item in payload["tasks"]}
        self.assertEqual(by_name["Episode 01.mkv"]["node_id"], "input")
        self.assertEqual(by_name["Episode 01.mkv"]["status"], "Queued")
        self.assertEqual(by_name["Episode 02.mkv"]["node_id"], "transcribe")
        self.assertEqual(by_name["Episode 02.mkv"]["status"], "Running")
        self.assertEqual(by_name["Episode 03.mkv"]["status"], "Failed")
        self.assertEqual(by_name["Episode 04.mkv"]["node_id"], "output")
        self.assertEqual(by_name["Episode 04.mkv"]["status"], "Success")
        self.assertEqual(payload["stats"]["transcribe"]["running"], 1)
        self.assertGreaterEqual(payload["stats"]["output"]["success"], 1)

    def test_dashboard_uses_effective_status_and_queue_priority(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        done_path = "/jellyfin_anime/Series/Season 1/Already Done.mkv"
        newer_done_path = "/jellyfin_anime/Series/Season 1/Newer Done.mkv"
        new_path = "/jellyfin_anime/Series/Season 1/New Queued.mkv"
        old_path = "/jellyfin_anime/Series/Season 1/Old Queued.mkv"
        requeued_failed_path = "/jellyfin_anime/Series/Season 1/Requeued Failed.mkv"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                CREATE TABLE ai_candidate_queue (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'scan',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    running_at REAL,
                    last_error TEXT,
                    last_error_at REAL,
                    next_retry_at REAL,
                    force_ai INTEGER NOT NULL DEFAULT 0,
                    added_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            for path, mtime_ns, added_at, updated_at in [
                (done_path, 1, now + 100, now + 100),
                (newer_done_path, 2, now + 400, now + 400),
                (new_path, 10, now + 300, now),
                (old_path, 300, now, now + 200),
                (requeued_failed_path, 200, now + 200, now + 250),
            ]:
                conn.execute(
                    """
                    INSERT INTO ai_candidate_queue(path, mtime_ns, status, added_at, updated_at)
                    VALUES (?, ?, 'queued', ?, ?)
                    """,
                    (path, mtime_ns, added_at, updated_at),
                )
            conn.execute(
                """
                CREATE TABLE ai_job_state (
                    path TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                VALUES (?, 'complete', 'ok', 'AI subtitle job completed', ?, ?, ?)
                """,
                (done_path, now - 20, now - 10, now - 10),
            )
            conn.execute(
                """
                INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                VALUES (?, 'complete', 'ok', 'AI subtitle job completed', ?, ?, ?)
                """,
                (newer_done_path, now, now + 20, now + 20),
            )
            conn.execute(
                """
                INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                VALUES (?, 'failed', 'failed', 'worker returned false', ?, ?, ?)
                """,
                (requeued_failed_path, now - 20, now - 10, now - 10),
            )

        payload = self.module._dashboard_tasks_summary(limit=10)
        self.assertEqual(payload["counts"]["done"], 2)
        self.assertEqual(payload["counts"]["queued"], 3)
        self.assertEqual(
            [item["path"] for item in payload["recent_completed"]],
            [newer_done_path, done_path],
        )

        queued = self.module._dashboard_tasks_summary(limit=10, status_filter="queued")["tasks"]
        self.assertEqual([item["path"] for item in queued], [new_path, requeued_failed_path, old_path])
        self.assertEqual(queued[1]["status"], "Queued")
        self.assertEqual(queued[1]["job_status"], "failed")
        self.assertTrue(all(item["completion_kind"] == "" for item in queued))
        self.assertTrue(all(item["completion_label"] == "" for item in queued))

        completed = self.module._dashboard_tasks_summary(limit=10, status_filter="success")["tasks"]
        self.assertEqual(completed[0]["path"], newer_done_path)
        self.assertEqual(completed[0]["status"], "Success")
        self.assertEqual(completed[0]["raw_status"], "done")
        self.assertEqual(completed[0]["queue_status"], "queued")

    def test_dashboard_tasks_supports_pagination_and_modes(self) -> None:
        self.create_queue_db()

        active = self.module._dashboard_tasks_summary(page=1, page_size=2, mode="active")
        self.assertEqual(active["page"], 1)
        self.assertEqual(active["page_size"], 2)
        self.assertGreaterEqual(active["page_count"], 2)
        self.assertTrue(all(item["raw_status"] != "done" for item in active["tasks"]))
        self.assertEqual(active["recent_completed"], [])

        completed = self.module._dashboard_tasks_summary(page=1, page_size=5, mode="completed")
        self.assertTrue(completed["tasks"])
        self.assertTrue(all(item["status"] == "Success" for item in completed["tasks"]))
        self.assertEqual(completed["mode"], "completed")
        self.assertEqual(completed["recent_completed"], [])

    def test_compact_mikan_downloads_payload_removes_nested_diagnostics(self) -> None:
        payload = {
            "total": 1,
            "recent": [
                {
                    "key": "1:2",
                    "status": "completed_waiting_extract",
                    "title": "Episode 02",
                    "source_published_at": 100.0,
                    "torrent_added_at": 200.0,
                    "torrent_completed_at": 300.0,
                    "torrent_url": "https://example.invalid/file.torrent",
                    "children": [{"key": "1:2"}, {"key": "1:3"}],
                    "last_subtitle_diagnostics": [{"stream": index} for index in range(20)],
                    "last_extract_context": {
                        "source_video": "/qbit/Episode 02.mkv",
                        "target_video": "/anime/Series/Episode 02.mkv",
                        "unused_debug_blob": "x" * 1000,
                        "target_candidates": [
                            {"path": f"/anime/candidate-{index}.mkv", "score": 100 - index, "reasons": ["title"]}
                            for index in range(8)
                        ],
                    },
                }
            ],
        }

        compact = self.module._compact_mikan_downloads_payload(payload)

        self.assertTrue(compact["compact"])
        row = compact["recent"][0]
        self.assertEqual(row["child_count"], 2)
        self.assertNotIn("children", row)
        self.assertNotIn("torrent_url", row)
        self.assertNotIn("last_subtitle_diagnostics", row)
        self.assertNotIn("unused_debug_blob", row["last_extract_context"])
        self.assertEqual(len(row["last_extract_context"]["target_candidates"]), 3)
        self.assertEqual(row["source_published_at"], 100.0)
        self.assertEqual(row["torrent_added_at"], 200.0)
        self.assertEqual(row["torrent_completed_at"], 300.0)

    def test_extract_job_torrent_times_are_read_from_qbit_payload(self) -> None:
        self.assertEqual(
            self.module._mikan_torrent_time_fields(
                json.dumps({
                    "creation_date": 1_699_999_900,
                    "added_on": 1_700_000_000,
                    "completion_on": 1_700_000_900,
                })
            ),
            {
                "torrent_created_at": 1_699_999_900,
                "torrent_added_at": 1_700_000_000,
                "torrent_completed_at": 1_700_000_900,
            },
        )

    def test_compact_mikan_download_exposes_friendly_problem_without_raw_error(self) -> None:
        raw_detail = (
            "ffprobe failed for /qbit/private/Episode.mkv; "
            "source=https://secret.example/private.torrent; no subtitle streams"
        )
        compact = self.module._compact_mikan_downloads_payload({
            "total": 1,
            "recent": [{
                "key": "1:2",
                "status": "extract_failed",
                "title": "Episode 02",
                "last_extract_failure_reason": "no_subtitle_streams",
                "last_extract_failure_detail": raw_detail,
                "last_error": raw_detail,
            }],
        })

        row = compact["recent"][0]
        encoded = json.dumps(row, ensure_ascii=False)
        self.assertEqual(row["problem"]["code"], "no_subtitle_streams")
        self.assertEqual(row["problem"]["title"], "來源內沒有可用中文字幕")
        self.assertEqual(row["problem"]["recommended_action"], "等待系統更換來源。")
        self.assertNotIn("last_extract_failure_detail", row)
        self.assertNotIn("last_error", row)
        self.assertNotIn("ffprobe", encoded)
        self.assertNotIn("https://secret.example", encoded)

    def test_compact_ai_task_hides_raw_failure_and_exposes_friendly_problem(self) -> None:
        task = self.module._compact_ai_task({
            "path": "/anime/Series/Episode 01.mkv",
            "raw_status": "failed_retry",
            "stage": "translation",
            "message": "Traceback at /work/private/cache: database is locked https://secret.example",
            "next_retry_at": 123,
        })

        encoded = json.dumps(task, ensure_ascii=False)
        self.assertNotIn("message", task)
        self.assertEqual(task["problem"]["code"], "database_locked")
        self.assertEqual(task["problem"]["title"], "狀態資料庫暫時忙碌")
        self.assertIn("已延後", task["display_message"])
        self.assertNotIn("Traceback", encoded)
        self.assertNotIn("https://secret.example", encoded)

    def test_problem_presentation_marks_ambiguous_target_as_user_action(self) -> None:
        problem = self.module._problem_presentation("target_ambiguity", status="review")

        self.assertEqual(problem["title"], "需要確認作品與季度")
        self.assertTrue(problem["requires_user_action"])
        self.assertIn("不匯入字幕", problem["system_action"])

    def test_dashboard_paused_failures_are_not_presented_as_queued(self) -> None:
        self.create_queue_db()
        now = time.time()
        cases = (
            (
                "/anime/Series/Safe Omission.mkv",
                "Translation safe-omission remained after bounded same-job recovery: indexes=[98]",
                "翻譯缺漏，已安全暫停",
            ),
            ("/anime/Series/Generic Failure.mkv", "quality checks exhausted", "AI 已安全暫停"),
        )
        with sqlite3.connect(self.module.WORK_PATH / "scanner_state.sqlite3") as conn:
            for path, message, _title in cases:
                conn.execute(
                    """
                    INSERT INTO ai_candidate_queue(path, mtime_ns, status, attempts, last_error, added_at, updated_at)
                    VALUES (?, ?, 'paused', 4, ?, ?, ?)
                    """,
                    (path, int(now * 1_000_000_000), message, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                    VALUES (?, 'quality_check', 'failed', ?, ?, ?, ?)
                    """,
                    (path, message, now - 20, now, now),
                )

        for path, _message, title in cases:
            task = self.module._dashboard_tasks_summary(search=Path(path).name, limit=1)["tasks"][0]
            self.assertEqual(task["raw_status"], "paused")
            self.assertEqual(task["job_status"], "failed")
            self.assertEqual(task["problem"]["title"], title)
            self.assertIn("未發布", task["problem"]["description"])
            self.assertNotEqual(task["problem"]["title"], "等待處理")

    def test_mikan_operation_state_reports_active_lock(self) -> None:
        lock = self.module.WORK_PATH / "mikan_extract.lock"
        lock.write_text("{}", encoding="utf-8")

        state = self.module._mikan_operation_state()

        self.assertTrue(state["busy"])
        self.assertEqual(state["active_operations"], ["subtitle_extract"])

    def test_background_action_conflict_returns_http_409(self) -> None:
        self.module._claim_background_action("first-action")

        with self.assertRaises(self.module.HTTPException) as raised:
            self.module._claim_background_action("second-action")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("first-action", raised.exception.detail)

    def test_finished_action_snapshot_keeps_elapsed_duration(self) -> None:
        started_at = time.time() - 5
        self.module.ACTION_STATE.update(
            {
                "running": True,
                "action": "test-action",
                "started_at": started_at,
                "finished_at": None,
                "ok": None,
                "output": "",
                "error": "",
            }
        )

        self.module._finish_background_action(ok=True, output="done")
        snapshot = self.module._action_snapshot()

        self.assertFalse(snapshot["running"])
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["output"], "done")
        self.assertGreaterEqual(snapshot["elapsed_seconds"], 4.9)

    def test_retry_all_failures_does_not_use_webui_background_writer(self) -> None:
        config = {"work_path": str(self.module.WORK_PATH), "control_inbox_path": "control_inbox"}

        with patch.object(self.module, "_load_config", return_value=config):
            result = self.module.run_action("retry-all-failures")

        self.assertEqual(result["action"], "system.retry_all_failures")
        self.assertFalse(self.module._action_snapshot()["running"])

    def test_dashboard_summary_is_lite_and_includes_recent_completed(self) -> None:
        self.create_queue_db()

        payload = self.module._dashboard_summary()

        self.assertIn("queue_counts", payload)
        self.assertIn("recent_completed", payload)
        self.assertIn("ai", payload["recent_completed"])
        self.assertIn("eta", payload)
        self.assertIn("recommendations", payload)
        self.assertIn("ai_control", payload)
        self.assertNotIn("library", payload)
        self.assertNotIn("events", payload)

    def test_dashboard_recommendations_are_actionable_and_ignore_automatic_replacements(self) -> None:
        payload = {
            "queue_counts": {"paused": 2, "failed_retry": 1},
            "current_ai": {},
            "health": {"checks": []},
            "mikan": {
                "busy": False,
                "state_db": {
                    "pipeline": {"waiting_extract": 3, "extracting": 0, "auto_replacing": 99},
                    "extract_jobs": {"counts": {"terminal_failed": 1, "replaced": 99}},
                },
            },
            "database_health": {
                "databases": [{"reclaim_mib": 128.5, "freelist_ratio": 0.5}],
            },
        }

        recommendations = self.module._dashboard_recommendations(payload)
        keys = [item["key"] for item in recommendations]

        self.assertIn("paused-ai-review", keys)
        self.assertIn("retry-failures", keys)
        self.assertIn("database-maintenance", keys)
        self.assertIn("resume-extraction", keys)
        self.assertNotIn("auto_replacing", keys)
        database = next(item for item in recommendations if item["key"] == "database-maintenance")
        self.assertEqual(database["action"], "database-maintenance")

    def test_dashboard_retry_recommendation_excludes_terminal_extract_failures(self) -> None:
        payload = {
            "queue_counts": {},
            "current_ai": {},
            "health": {"checks": []},
            "mikan": {
                "busy": False,
                "state_db": {
                    "pipeline": {},
                    "extract_jobs": {
                        "counts": {"terminal_failed": 3, "failed": 2},
                        "retryable_count": 0,
                    },
                },
            },
            "database_health": {"databases": []},
        }

        recommendations = self.module._dashboard_recommendations(payload)

        self.assertNotIn("retry-failures", [item["key"] for item in recommendations])

        payload["queue_counts"]["failed_retry"] = 4
        payload["mikan"]["state_db"]["extract_jobs"]["retryable_count"] = 2
        recommendations = self.module._dashboard_recommendations(payload)
        retry = next(item for item in recommendations if item["key"] == "retry-failures")
        self.assertEqual(retry["retryable_count"], 6)
        self.assertEqual(retry["counts"], {"ai_failed_retry": 4, "extract_retryable": 2})

    def test_recent_ai_completed_marks_queue_only_done_as_detected_existing(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                CREATE TABLE ai_candidate_queue (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'scan',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    running_at REAL,
                    last_error TEXT,
                    last_error_at REAL,
                    next_retry_at REAL,
                    added_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ai_candidate_queue(path, mtime_ns, status, added_at, updated_at)
                VALUES (?, 1, 'done', ?, ?)
                """,
                ("/anime/Existing AI.mkv", now - 10, now),
            )

        rows = self.module._recent_ai_completed_summary(limit=1)

        self.assertEqual(rows[0]["completion_kind"], "detected_existing")
        self.assertEqual(rows[0]["completion_label"], "掃描確認已有 AI 字幕")

    def test_recent_ai_completed_uses_existing_subtitle_mtime_for_scan_detected_rows(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        video = self.tmp / "anime" / "Series" / "Episode 01.mkv"
        video.parent.mkdir(parents=True)
        video.write_text("", encoding="utf-8")
        subtitle = video.with_name(video.stem + ".AI.zh-TW.ass")
        subtitle.write_text("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,字幕", encoding="utf-8")
        subtitle_mtime = 1234.0
        os.utime(subtitle, (subtitle_mtime, subtitle_mtime))
        self.module.CONFIG_PATH.write_text(
            yaml.safe_dump({"ai_traditional_chinese_ass_suffix": ".AI.zh-TW.ass"}, allow_unicode=True),
            encoding="utf-8",
        )
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                CREATE TABLE ai_job_state (
                    path TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                VALUES (?, 'complete', 'ok', 'Finished AI subtitle detected during scan', ?, ?, ?)
                """,
                (str(video), now, now, now),
            )

        rows = self.module._recent_ai_completed_summary(limit=1)

        self.assertEqual(rows[0]["completion_kind"], "detected_existing")
        self.assertEqual(rows[0]["completed_at"], subtitle_mtime)
        self.assertEqual(rows[0]["updated_at"], subtitle_mtime)

    def test_recent_ai_completed_marks_finished_job_as_generated(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                CREATE TABLE ai_job_state (
                    path TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                VALUES (?, 'complete', 'ok', 'done', ?, ?, ?)
                """,
                ("/anime/Generated AI.mkv", now - 600, now, now),
            )

        rows = self.module._recent_ai_completed_summary(limit=1)

        self.assertEqual(rows[0]["completion_kind"], "generated")
        self.assertEqual(rows[0]["completion_label"], "AI 字幕生成完成")

    def test_recent_ai_completed_requires_done_for_existing_queue_rows(self) -> None:
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                CREATE TABLE ai_job_state (
                    path TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO ai_job_state(path, stage, status, message, started_at, updated_at, finished_at)
                VALUES (?, ?, 'ok', 'done', ?, ?, ?)
                """,
                (
                    ("/anime/Still Running.mkv", "translating", now - 600, now, now),
                    ("/anime/Queue Done.mkv", "complete", now - 600, now - 1, now - 1),
                    ("/anime/Legacy No Queue Row.mkv", "complete", now - 600, now - 2, now - 2),
                ),
            )
            conn.execute(
                """
                CREATE TABLE ai_candidate_queue (
                    path TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    added_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO ai_candidate_queue(path, status, added_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    ("/anime/Still Running.mkv", "running", now - 600, now),
                    ("/anime/Queue Done.mkv", "done", now - 600, now - 1),
                ),
            )

        rows = self.module._recent_ai_completed_summary(limit=10)
        paths = {row["path"] for row in rows}

        self.assertNotIn("/anime/Still Running.mkv", paths)
        self.assertIn("/anime/Queue Done.mkv", paths)
        self.assertIn("/anime/Legacy No Queue Row.mkv", paths)

    def test_workflow_routes_return_flow_and_tasks(self) -> None:
        self.create_queue_db()

        flow = self.module.workflow()
        tasks = self.module.workflow_tasks(limit=10)

        self.assertEqual(flow["edges"][0]["source"], "input")
        self.assertEqual(flow["edges"][-1]["target"], "output")
        self.assertIn("stats", flow)
        self.assertLessEqual(len(tasks["tasks"]), 10)
        self.assertIn("queue_counts", tasks)

    def test_library_summary_uses_scan_cache_not_queue_history(self) -> None:
        self.create_queue_db()
        summary = self.module._library_summary(limit=50)
        self.assertEqual(
            summary["counts"],
            {"embedded_chinese": 1, "finished": 1, "local_chinese": 1, "needs_ai": 2},
        )
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["needs_ai"], 2)
        self.assertEqual(summary["subtitle_ready"], 3)

    def test_tail_file_reads_last_lines(self) -> None:
        log_path = self.module.LOG_PATH / "app.log"
        log_path.write_text("\n".join(f"line {index}" for index in range(1000)), encoding="utf-8")

        content = self.module._tail_file(log_path, 3)

        self.assertEqual(content, "line 997\nline 998\nline 999")

    def test_queue_filters_done_failed_running_queued(self) -> None:
        self.create_queue_db()
        for status in ["queued", "running", "failed_retry", "done"]:
            with self.subTest(status=status):
                summary = self.module._queue_summary(status_filter=status, search="Series", limit=10)
                expected = 1
                self.assertEqual(summary["filtered"], expected)
                self.assertEqual(len(summary["recent"]), expected)
                self.assertEqual(summary["recent"][0]["status"], status)

    def test_queue_summary_treats_completed_job_as_done_even_if_queue_was_requeued(self) -> None:
        self.create_queue_db()
        db = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        path = "/jellyfin_anime/Overlay/Season 1/Episode 01.mkv"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                INSERT INTO ai_candidate_queue(path, mtime_ns, status, attempts, added_at, updated_at)
                VALUES (?, ?, 'queued', 0, ?, ?)
                """,
                (path, 9999, now, now),
            )
            conn.execute(
                """
                INSERT INTO ai_stage_events(path, stage, status, message, created_at)
                VALUES (?, 'complete', 'ok', 'completed earlier', ?)
                """,
                (path, now),
            )

        done = self.module._queue_summary(status_filter="done", search="Overlay", limit=10)
        queued = self.module._queue_summary(status_filter="queued", search="Overlay", limit=10)
        self.assertEqual(done["filtered"], 1)
        self.assertEqual(done["recent"][0]["status"], "done")
        self.assertEqual(queued["filtered"], 0)

    def test_queue_missing_db_is_visible(self) -> None:
        summary = self.module._queue_summary()
        self.assertFalse(summary["exists"])
        self.assertIn("scanner_state.sqlite3", summary["database"])

    def test_sqlite_connect_sets_busy_timeout(self) -> None:
        db_path = self.module.WORK_PATH / "timeout.sqlite3"
        with sqlite3.connect(db_path):
            pass

        with self.module._sqlite_connect(db_path) as conn:
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

        self.assertEqual(busy_timeout, int(self.module.SQLITE_BUSY_TIMEOUT_SECONDS * 1000))

    def test_sqlite_connection_context_closes_connection(self) -> None:
        db_path = self.module.WORK_PATH / "closed-after-context.sqlite3"
        with sqlite3.connect(db_path) as writable:
            writable.execute("CREATE TABLE items(id INTEGER PRIMARY KEY)")

        with self.module._sqlite_connect(db_path) as conn:
            conn.execute("SELECT 1")

        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_queue_summary_uses_readonly_connection_after_schema_probe(self) -> None:
        self.create_queue_db()
        self.module._queue_summary()
        original_connect = self.module._sqlite_connect
        calls: list[bool] = []

        def tracked_connect(path: Path, *, readonly: bool = False):
            calls.append(readonly)
            return original_connect(path, readonly=readonly)

        with patch.object(self.module, "_sqlite_connect", side_effect=tracked_connect):
            payload = self.module._queue_summary()

        self.assertTrue(payload["exists"])
        self.assertEqual(calls, [True])

    def test_sqlite_connect_rejects_writer_mode(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Worker command"):
            self.module._sqlite_connect(self.module.WORK_PATH / "forbidden.sqlite3", readonly=False)

    def test_webui_source_has_one_enforced_readonly_sqlite_gateway(self) -> None:
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertEqual(source.count("sqlite3.connect("), 1)
        self.assertIn("?mode=ro&cache=private", source)
        self.assertIn("PRAGMA query_only=ON", source)
        self.assertNotIn("conn.commit()", source)
        self.assertNotIn("ALTER TABLE", source)

    def test_stream_state_version_is_stable_without_file_changes(self) -> None:
        first = self.module._stream_state_version()
        second = self.module._stream_state_version()

        self.assertEqual(first, second)
        self.assertNotIn("time", first)

    def test_save_config_writes_yaml(self) -> None:
        self.module._save_config(
            {
                "ass_primary_font_size": 58,
                "mikan_enabled": True,
                "translator_model": "hf.co/SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF:latest",
            }
        )
        saved = yaml.safe_load(self.module.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["ass_primary_font_size"], 58)
        self.assertTrue(saved["mikan_enabled"])
        self.assertEqual(saved["translator_model"], "hf.co/SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF:latest")

    def test_translator_model_is_editable_string(self) -> None:
        self.assertEqual(
            self.module._coerce_value("translator_model", "hf.co/SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF:latest"),
            "hf.co/SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF:latest",
        )

    def test_worker_missing_socket_does_not_crash(self) -> None:
        worker = self.module._worker_summary()
        self.assertFalse(worker["available"])
        self.assertIn("Docker socket", worker["error"])

    def test_docker_exec_raises_when_exit_code_is_nonzero(self) -> None:
        calls = []

        def fake_request(method, path, body=None, *, parse_json=True, timeout_seconds=None):
            calls.append((method, path, body, parse_json, timeout_seconds))
            if path == "/containers/worker/exec":
                return {"Id": "exec-1"}
            if path == "/exec/exec-1/start":
                return "traceback"
            if path == "/exec/exec-1/json":
                return {"ExitCode": 2}
            raise AssertionError(path)

        with patch.object(self.module, "_docker_request", side_effect=fake_request):
            with self.assertRaises(self.module.HTTPException) as ctx:
                self.module._docker_exec("worker", ["python", "main.py"])

        self.assertIn("exit_code=2", str(ctx.exception.detail))
        self.assertEqual(calls[-1][1], "/exec/exec-1/json")
        self.assertEqual(calls[1][4], self.module.DOCKER_EXEC_TIMEOUT_SECONDS)

    def test_docker_exec_returns_output_when_exit_code_is_zero(self) -> None:
        def fake_request(method, path, body=None, *, parse_json=True, timeout_seconds=None):
            if path == "/containers/worker/exec":
                return {"Id": "exec-1"}
            if path == "/exec/exec-1/start":
                return "ok"
            if path == "/exec/exec-1/json":
                return {"ExitCode": 0}
            raise AssertionError(path)

        with patch.object(self.module, "_docker_request", side_effect=fake_request):
            self.assertEqual(self.module._docker_exec("worker", ["python", "main.py"]), "ok")

    def test_docker_request_sets_socket_timeout(self) -> None:
        socket_path = self.tmp / "docker.sock"
        socket_path.write_text("", encoding="utf-8")
        self.module.DOCKER_SOCKET = socket_path

        class FakeSocket:
            def __init__(self) -> None:
                self.timeout = None
                self.sent = b""
                self.recv_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                return None

            def settimeout(self, timeout) -> None:
                self.timeout = timeout

            def connect(self, path) -> None:
                self.path = path

            def sendall(self, payload) -> None:
                self.sent += payload

            def recv(self, size):
                self.recv_calls += 1
                if self.recv_calls == 1:
                    return b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
                return b""

        fake_socket = FakeSocket()
        with (
            patch.object(self.module.socket, "AF_UNIX", 1, create=True),
            patch.object(self.module.socket, "socket", return_value=fake_socket),
        ):
            result = self.module._docker_request("GET", "/version")

        self.assertEqual(result, {})
        self.assertEqual(fake_socket.timeout, self.module.DOCKER_API_TIMEOUT_SECONDS)
        self.assertIn(b"GET /version HTTP/1.1", fake_socket.sent)

    def test_restarting_worker_is_an_error_even_when_docker_says_running(self) -> None:
        with patch.object(
            self.module,
            "_worker_summary",
            return_value={
                "available": True,
                "running": True,
                "restarting": True,
                "restart_count": 12,
                "status": "restarting",
            },
        ):
            health = self.module._health_summary({}, fast=True)

        check = next(item for item in health["checks"] if item["name"] == "worker_running")
        self.assertFalse(check["ok"])
        self.assertIn("restart_count=12", check["detail"])
        self.assertEqual(health["overall"], "error")

    def test_worker_summary_exposes_exit_and_restart_diagnostics(self) -> None:
        docker_payload = {
            "Name": "/worker",
            "Image": "sha256:image",
            "RestartCount": 7,
            "Config": {"Image": "worker:latest", "Labels": {}},
            "State": {
                "Status": "restarting",
                "Running": True,
                "Restarting": True,
                "Dead": False,
                "Paused": False,
                "OOMKilled": True,
                "ExitCode": 137,
                "Error": "runtime failed",
                "StartedAt": "start",
                "FinishedAt": "finish",
            },
        }
        self.module.DOCKER_SOCKET.write_text("socket", encoding="utf-8")
        with patch.object(self.module, "_docker_request", return_value=docker_payload):
            worker = self.module._worker_summary()

        self.assertTrue(worker["restarting"])
        self.assertTrue(worker["oom_killed"])
        self.assertEqual(worker["exit_code"], 137)
        self.assertEqual(worker["state_error"], "runtime failed")
        self.assertEqual(worker["restart_count"], 7)

    def test_worker_runtime_log_decodes_docker_multiplexed_frames(self) -> None:
        def frame(stream: int, value: bytes) -> bytes:
            return bytes([stream, 0, 0, 0]) + len(value).to_bytes(4, "big") + value

        payload = frame(1, b"stdout line\n") + frame(2, b"stderr line\n")
        with patch.object(self.module, "_docker_request", return_value=payload) as request:
            result = self.module._worker_runtime_log(20)

        self.assertTrue(result["ok"])
        self.assertEqual(result["lines"], ["stdout line", "stderr line"])
        request.assert_called_once_with(
            "GET",
            f"/containers/{self.module.WORKER_CONTAINER_NAME}/logs?stdout=1&stderr=1&timestamps=1&tail=20",
            return_bytes=True,
            timeout_seconds=5.0,
        )

    def test_mikan_lock_busy_action_error_is_friendly(self) -> None:
        detail = (
            "Docker exec failed exit_code=1: Traceback...\n"
            "mikan_worker.MikanWorkerError: Mikan operation already running; "
            "skip operation=reset_all_state_and_enqueue lock=/work/mikan_worker.lock"
        )

        formatted = self.module._format_background_action_error("mikan-reset-all", detail)

        self.assertIn("Mikan 正在執行另一個操作", formatted)
        self.assertIn("mikan_operation_lock_wait_seconds", formatted)
        self.assertIn("lock=/work/mikan_worker.lock", formatted)
        self.assertNotIn("Traceback", formatted)

    def test_mikan_summary_reports_busy_lock(self) -> None:
        lock_path = self.module.WORK_PATH / "mikan_worker.lock"
        lock_path.write_text("pid=1\nprocess_start=123.45\ncreated=2026-06-07T04:20:44\n", encoding="utf-8")
        os.utime(lock_path, (1000, 1000))
        extract_lock_path = self.module.WORK_PATH / "mikan_extract.lock"
        extract_lock_path.write_text("pid=2\nprocess_start=234.56\ncreated=2026-06-07T04:21:44\n", encoding="utf-8")
        os.utime(extract_lock_path, (1020, 1020))
        queue_lock_path = self.module.WORK_PATH / "mikan_enqueue.lock"
        queue_lock_path.write_text("pid=3\nprocess_start=345.67\ncreated=2026-06-07T04:22:44\n", encoding="utf-8")
        os.utime(queue_lock_path, (1045, 1045))
        request_path = self.module.WORK_PATH / "mikan_reset_all.request.json"
        request_path.write_text(
            '{"action":"reset_all_state_and_enqueue","request_count":2,"requested_at":"2026-06-07T06:00:00+00:00"}',
            encoding="utf-8",
        )
        os.utime(request_path, (1010, 1010))
        redownload_path = self.module.WORK_PATH / "mikan_redownload_all.request.json"
        redownload_path.write_text(
            (
                '{"action":"redownload_all_torrents_and_enqueue","request_count":1,'
                '"requested_at":"2026-06-07T06:01:00+00:00",'
                '"qbit_deleted_at":"2026-06-07T06:01:05+00:00",'
                '"state_reset_at":"2026-06-07T06:01:10+00:00"}'
            ),
            encoding="utf-8",
        )
        os.utime(redownload_path, (1030, 1030))
        redownload_active_path = self.module.WORK_PATH / "mikan_redownload_all.active.json"
        redownload_active_path.write_text(
            (
                '{"action":"redownload_all_torrents_and_enqueue","started_at":"2026-06-07T06:01:30+00:00",'
                '"updated_at":"2026-06-07T06:01:45+00:00","delete_files":false,'
                '"stage":"fetch_releases","stage_label":"查詢 Mikan RSS 並加種",'
                '"current":3,"total":12,"queued":2,"deferred":1,'
                '"scan_current":1,"scan_total":2,"scan_path":"/anime/Test"}'
            ),
            encoding="utf-8",
        )
        os.utime(redownload_active_path, (1035, 1035))
        completed_state_update_path = self.module.WORK_PATH / "mikan_completed_state_update.request.json"
        completed_state_update_path.write_text(
            '{"action":"process_completed_downloads_state_update","request_count":3,"requested_at":"2026-06-07T06:02:00+00:00"}',
            encoding="utf-8",
        )
        os.utime(completed_state_update_path, (1040, 1040))

        with patch.object(self.module.time, "time", return_value=1065):
            summary = self.module._mikan_summary()

        self.assertTrue(summary["busy"])
        self.assertEqual(summary["completed_poll_interval_seconds"], 30)
        self.assertEqual(summary["lock"]["pid"], 1)
        self.assertEqual(summary["lock"]["age_seconds"], 65)
        self.assertEqual(summary["lock"]["created"], "2026-06-07T04:20:44")
        self.assertTrue(summary["extract_lock"]["exists"])
        self.assertEqual(summary["extract_lock"]["pid"], 2)
        self.assertEqual(summary["extract_lock"]["age_seconds"], 45)
        self.assertTrue(summary["queue_lock"]["exists"])
        self.assertEqual(summary["queue_lock"]["pid"], 3)
        self.assertEqual(summary["queue_lock"]["age_seconds"], 20)
        self.assertTrue(summary["reset_request"]["exists"])
        self.assertEqual(summary["reset_request"]["age_seconds"], 55)
        self.assertEqual(summary["reset_request"]["request_count"], 2)
        self.assertTrue(summary["redownload_request"]["exists"])
        self.assertEqual(summary["redownload_request"]["age_seconds"], 35)
        self.assertEqual(summary["redownload_request"]["request_count"], 1)
        self.assertEqual(summary["redownload_request"]["qbit_deleted_at"], "2026-06-07T06:01:05+00:00")
        self.assertEqual(summary["redownload_request"]["state_reset_at"], "2026-06-07T06:01:10+00:00")
        self.assertTrue(summary["redownload_active"]["exists"])
        self.assertEqual(summary["redownload_active"]["age_seconds"], 30)
        self.assertEqual(summary["redownload_active"]["started_at"], "2026-06-07T06:01:30+00:00")
        self.assertEqual(summary["redownload_active"]["stage"], "fetch_releases")
        self.assertEqual(summary["redownload_active"]["stage_label"], "查詢 Mikan RSS 並加種")
        self.assertEqual(summary["redownload_active"]["current"], 3)
        self.assertEqual(summary["redownload_active"]["total"], 12)
        self.assertEqual(summary["redownload_active"]["queued"], 2)
        self.assertEqual(summary["redownload_active"]["deferred"], 1)
        self.assertEqual(summary["redownload_active"]["scan_current"], 1)
        self.assertEqual(summary["redownload_active"]["scan_total"], 2)
        self.assertEqual(summary["redownload_active"]["scan_path"], "/anime/Test")
        self.assertFalse(summary["redownload_active"]["delete_files"])
        self.assertIn("redownload_lock", summary)
        self.assertTrue(summary["redownload_active"]["active"])
        self.assertFalse(summary["redownload_active"]["stale"])
        self.assertTrue(summary["completed_state_update_request"]["exists"])
        self.assertEqual(summary["completed_state_update_request"]["age_seconds"], 25)
        self.assertEqual(summary["completed_state_update_request"]["request_count"], 3)

    def test_mikan_summary_marks_redownload_active_stale_without_live_locks(self) -> None:
        redownload_active_path = self.module.WORK_PATH / "mikan_redownload_all.active.json"
        redownload_active_path.write_text(
            (
                '{"action":"redownload_all_torrents_and_enqueue","started_at":"2026-06-09T10:00:40+00:00",'
                '"updated_at":"2026-06-09T10:00:40+00:00","delete_files":false,'
                '"stage":"resolve_series","stage_label":"整理番劇對應","deleted_torrents":687}'
            ),
            encoding="utf-8",
        )
        os.utime(redownload_active_path, (1000, 1000))

        with patch.object(self.module.time, "time", return_value=2005):
            summary = self.module._mikan_summary()

        self.assertFalse(summary["busy"])
        self.assertTrue(summary["redownload_active"]["exists"])
        self.assertFalse(summary["redownload_active"]["active"])
        self.assertTrue(summary["redownload_active"]["stale"])
        self.assertIn("no redownload", summary["redownload_active"]["stale_reason"])

    def test_full_mikan_summary_reports_active_extract_jobs_as_busy_without_legacy_lock(self) -> None:
        state_db = {
            "exists": True,
            "pipeline": {"extracting": 2},
            "extract_jobs": {"active": 2, "counts": {"running": 2}},
        }

        with patch.object(self.module, "_mikan_state_db_summary", return_value=state_db):
            summary = self.module._mikan_summary({}, include_downloads=False)

        self.assertTrue(summary["busy"])
        self.assertEqual(summary["active_operations"], ["subtitle_extract"])
        self.assertIs(summary["state_db"], state_db)

    def test_recent_redownload_heartbeat_is_busy_without_long_held_lock(self) -> None:
        redownload_active_path = self.module.WORK_PATH / "mikan_redownload_all.active.json"
        redownload_active_path.write_text(
            '{"action":"redownload_all_torrents_and_enqueue","stage":"scan_missing","current":4,"total":10}',
            encoding="utf-8",
        )
        os.utime(redownload_active_path, (1000, 1000))

        with patch.object(self.module.time, "time", return_value=1065):
            lite = self.module._mikan_operation_state()
            full = self.module._mikan_summary()

        self.assertTrue(lite["busy"])
        self.assertIn("redownload", lite["active_operations"])
        self.assertTrue(lite["redownload_active"]["active"])
        self.assertTrue(full["busy"])
        self.assertTrue(full["redownload_active"]["active"])

    def test_mikan_summary_marks_reused_pid_lock_stale(self) -> None:
        lock_path = self.module.WORK_PATH / "mikan_worker.lock"
        lock_path.write_text("pid=1\nprocess_start=123.45\ncreated=2026-06-08T10:23:58\n", encoding="utf-8")
        os.utime(lock_path, (1000, 1000))
        queue_lock_path = self.module.WORK_PATH / "mikan_enqueue.lock"
        queue_lock_path.write_text("pid=1\nprocess_start=345.67\ncreated=2026-06-08T11:12:49\n", encoding="utf-8")
        os.utime(queue_lock_path, (1040, 1040))

        with patch.object(self.module.time, "time", return_value=1065):
            summary = self.module._mikan_summary()

        self.assertTrue(summary["busy"])
        self.assertTrue(summary["lock"]["exists"])
        self.assertTrue(summary["lock"]["stale"])
        self.assertEqual(summary["lock"]["stale_reason"], "process_reused")
        self.assertFalse(summary["lock"]["active"])
        self.assertTrue(summary["queue_lock"]["exists"])
        self.assertNotIn("stale", summary["queue_lock"])
        self.assertTrue(summary["queue_lock"]["active"])

    def test_mikan_summary_reports_download_processing_state(self) -> None:
        pending_path = self.module.WORK_PATH / "mikan_pending.json"
        pending_path.write_text(
            json.dumps(
                {
                    "items": {
                        "123:8": {
                            "bangumi_id": 123,
                            "episode": 8,
                            "episodes": [8, 9],
                            "title": "Detective E08-E09",
                            "torrent_url": "https://mikan/two-episodes.torrent",
                            "queued_at": "2026-06-07T04:00:00+00:00",
                            "last_progress": 0.42,
                            "last_downloaded": 1048576,
                            "last_progress_at": "2026-06-07T04:05:00+00:00",
                            "last_qbit_sync_at": "2026-06-07T04:05:10+00:00",
                            "last_dlspeed": 2048,
                            "last_qbit_state": "downloading",
                            "last_qbit_hash": "hash8",
                            "last_qbit_name": "Detective E08-E09",
                        },
                        "123:9": {
                            "bangumi_id": 123,
                            "episode": 9,
                            "episodes": [8, 9],
                            "title": "Detective E08-E09",
                            "torrent_url": "https://mikan/two-episodes.torrent",
                            "queued_at": "2026-06-07T04:00:00+00:00",
                            "last_progress": 0.40,
                            "last_downloaded": 1024,
                            "last_progress_at": "2026-06-07T04:04:00+00:00",
                        },
                        "123:10": {
                            "bangumi_id": 123,
                            "episode": 10,
                            "deferred_torrent_url": "https://mikan/deferred.torrent",
                            "deferred_title": "Detective E10",
                            "deferred_at": "2026-06-07T04:10:00+00:00",
                            "deferred_reason": "qbit_unavailable",
                        },
                        "123:11": {
                            "bangumi_id": 123,
                            "episode": 11,
                            "no_candidate_at": "2026-06-07T04:11:00+00:00",
                            "no_candidate_until": 1780812000,
                        },
                        "123:12": {
                            "bangumi_id": 123,
                            "episode": 12,
                            "last_extract_failed_at": "2026-06-07T04:12:00+00:00",
                            "last_failure_reason": "extract_failed",
                            "last_extract_failure_reason": "subtitle_language_not_supported",
                            "last_extract_failure_detail": "scores: source=embedded status=unclassified lang=-",
                            "last_extract_context": {
                                "qbit_content_path": "/anime/Detective E12 bad source/Detective E12.mkv",
                                "qbit_save_path": "/anime",
                                "mapped_root": "/qbit_subtitle_extractor/Detective E12 bad source/Detective E12.mkv",
                                "mapped_root_exists": False,
                                "qbit_path_mappings": [
                                    {"remote": "/anime", "local": "/qbit_subtitle_extractor"},
                                ],
                                "qbit_files": [
                                    {
                                        "name": "Detective E12.mkv",
                                        "mapped_path": "/qbit_subtitle_extractor/Detective E12 bad source/Detective E12.mkv",
                                        "video": True,
                                    }
                                ],
                            },
                            "last_subtitle_diagnostics": [
                                {
                                    "source": "embedded",
                                    "status": "unclassified",
                                    "kind": "text",
                                    "codec": "ass",
                                    "classification": {
                                        "language": None,
                                        "reason": "no_language_evidence",
                                        "traditional_score": 0,
                                        "simplified_score": 0,
                                        "japanese_score": 0,
                                        "quality_score": 0,
                                    },
                                }
                            ],
                            "last_failed_title": "Detective E12 bad source",
                            "last_failed_torrent_url": "https://mikan/failed.torrent",
                            "failed_urls": ["https://mikan/failed.torrent"],
                        },
                        "123:13": {
                            "bangumi_id": 123,
                            "episode": 13,
                            "title": "Detective E13",
                            "completed_at": "2026-06-07T04:13:00+00:00",
                            "last_extracted_at": "2026-06-07T04:13:00+00:00",
                            "last_extracted_count": 2,
                            "total_extracted_count": 2,
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch.object(self.module.time, "time", return_value=1780808400):
            summary = self.module._mikan_summary({"mikan_pending_path": "mikan_pending.json"})

        downloads = summary["downloads"]
        self.assertTrue(downloads["exists"])
        self.assertEqual(downloads["total"], 5)
        self.assertEqual(downloads["counts"]["downloading"], 1)
        self.assertEqual(downloads["counts"]["completed"], 1)
        self.assertEqual(downloads["counts"]["deferred"], 1)
        self.assertEqual(downloads["counts"]["no_candidate_retry"], 1)
        self.assertEqual(downloads["counts"]["extract_failed"], 1)
        self.assertEqual(downloads["extracted_total"], 2)
        self.assertEqual(downloads["extracted_unknown_completed"], 0)
        downloading = downloads["recent"][0]
        self.assertEqual(downloading["status"], "downloading")
        self.assertEqual(downloading["episodes"], [8, 9])
        self.assertEqual(downloading["episode_count"], 2)
        self.assertEqual(downloading["progress"], 0.42)
        self.assertEqual(downloading["downloaded"], 1048576)
        self.assertEqual(downloading["dlspeed"], 2048)
        self.assertEqual(downloading["last_qbit_state"], "downloading")
        self.assertEqual(downloading["last_qbit_hash"], "hash8")
        self.assertGreater(downloading["last_qbit_sync_at"], downloading["last_progress_at"])
        self.assertEqual(len(downloading["children"]), 2)
        self.assertEqual([child["episode"] for child in downloading["children"]], [8, 9])
        self.assertEqual(downloading["children"][0]["last_qbit_hash"], "hash8")
        failed = next(row for row in downloads["recent"] if row["status"] == "extract_failed")
        self.assertEqual(failed["title"], "Detective E12 bad source")
        self.assertEqual(failed["last_failed_torrent_url"], "https://mikan/failed.torrent")
        self.assertEqual(failed["last_extract_failure_reason"], "subtitle_language_not_supported")
        self.assertIn("scores:", failed["last_extract_failure_detail"])
        self.assertEqual(failed["last_extract_context"]["qbit_content_path"], "/anime/Detective E12 bad source/Detective E12.mkv")
        self.assertEqual(failed["last_extract_context"]["mapped_root"], "/qbit_subtitle_extractor/Detective E12 bad source/Detective E12.mkv")
        self.assertEqual(failed["last_extract_context"]["qbit_path_mappings"][0]["local"], "/qbit_subtitle_extractor")
        self.assertEqual(failed["last_subtitle_diagnostics"][0]["classification"]["reason"], "no_language_evidence")
        completed = next(row for row in downloads["recent"] if row["status"] == "completed")
        self.assertEqual(completed["last_extracted_count"], 2)
        self.assertEqual(completed["total_extracted_count"], 2)
        paged = self.module._mikan_downloads_summary(
            {"mikan_pending_path": "mikan_pending.json"},
            page=2,
            page_size=2,
        )
        self.assertEqual(paged["page"], 2)
        self.assertEqual(paged["page_size"], 2)
        self.assertEqual(paged["page_count"], 3)
        self.assertEqual(len(paged["recent"]), 2)
        self.assertTrue(self.module._MIKAN_DOWNLOADS_CACHE)

    def test_mikan_downloads_overlay_running_extract_job_from_pending_json(self) -> None:
        pending_path = self.module.WORK_PATH / "mikan_pending.json"
        pending_path.write_text(
            json.dumps(
                {
                    "items": {
                        "2299:12": {
                            "bangumi_id": 2299,
                            "episode": 12,
                            "title": "Higurashi Gou E12",
                            "torrent_url": "https://mikan/higurashi-gou-12.torrent",
                            "queued_at": "2026-06-23T15:00:00+00:00",
                            "last_progress": 1.0,
                            "last_downloaded": 345678901,
                            "last_qbit_hash": "09882fd0a78d80cb7659cd177c03ab8a30ecac70",
                            "last_qbit_name": "[Sakurato] Higurashi no Naku Koro ni Gou [12]",
                            "last_extract_deferred_at": "2026-06-23T15:05:00+00:00",
                            "last_extract_deferred_reason": "target_video_not_found",
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        conn = sqlite3.connect(self.module.WORK_PATH / "mikan_state.sqlite3")
        try:
            conn.execute(
                """
                CREATE TABLE mikan_extract_jobs (
                    job_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    torrent_hash TEXT NOT NULL DEFAULT '',
                    torrent_name TEXT NOT NULL DEFAULT '',
                    target_path TEXT NOT NULL DEFAULT '',
                    current_file_timestamp REAL NOT NULL DEFAULT 0,
                    current_file_time_kind TEXT NOT NULL DEFAULT '',
                    current_file_size INTEGER NOT NULL DEFAULT 0,
                    episodes_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:09882', 'running', 28, '09882fd0a78d80cb7659cd177c03ab8a30ecac70',
                        '[Sakurato] Higurashi no Naku Koro ni Gou [12]', '[12]', '', 10, 30, 20, 0)
                """
            )
            conn.commit()
        finally:
            conn.close()

        summary = self.module._mikan_downloads_summary({"mikan_pending_path": "mikan_pending.json"})

        self.assertEqual(summary["counts"]["extracting_subtitles"], 1)
        self.assertNotIn("target_missing", summary["counts"])
        self.assertEqual(summary["recent"][0]["status"], "extracting_subtitles")
        self.assertEqual(summary["recent"][0]["next_action"], "extracting_subtitles")
        self.assertEqual(summary["recent"][0]["extract_job_status"], "running")
        self.assertEqual(summary["recent"][0]["extract_job_attempts"], 28)

        filtered = self.module._mikan_downloads_summary(
            {"mikan_pending_path": "mikan_pending.json"},
            status_filter="extracting_subtitles",
        )
        self.assertEqual(filtered["filtered"], 1)
        self.assertEqual(filtered["recent"][0]["title"], "Higurashi Gou E12")

    def test_mikan_summary_reports_sqlite_state_mirror(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE mikan_download_items (
                    key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    next_action TEXT NOT NULL DEFAULT '',
                    last_progress REAL,
                    last_dlspeed INTEGER NOT NULL DEFAULT 0,
                    last_qbit_state TEXT,
                    updated_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE mikan_download_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    bangumi_id INTEGER,
                    episode INTEGER,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE mikan_state_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE mikan_jobs (
                    job_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    requested_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE mikan_extract_jobs (
                    job_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    torrent_hash TEXT NOT NULL DEFAULT '',
                    torrent_name TEXT NOT NULL DEFAULT '',
                    target_path TEXT NOT NULL DEFAULT '',
                    current_file_timestamp REAL NOT NULL DEFAULT 0,
                    current_file_time_kind TEXT NOT NULL DEFAULT '',
                    current_file_size INTEGER NOT NULL DEFAULT 0,
                    episodes_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.executemany(
                """
                INSERT INTO mikan_download_items(
                    key, status, title, next_action, last_progress, last_dlspeed, last_qbit_state, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("one", "downloading", "Episode 01", "replace_when_stall_timeout", 0.2, 0, "stalledDL", 100.0),
                    ("two", "completed_waiting_extract", "Episode 02", "extract_subtitles", 1.0, 0, "", 200.0),
                    ("three", "extract_failed", "Episode 03", "find_replacement", None, 0, "", 300.0),
                ],
            )
            conn.execute(
                """
                INSERT INTO mikan_download_events(key, bangumi_id, episode, event, detail, created_at)
                VALUES ('one', 1, 1, 'qbit_state_changed', 'qbit=downloading->stalledDL', 123.0)
                """
            )
            conn.execute(
                "INSERT INTO mikan_state_meta(key, value, updated_at) VALUES ('last_sync_at', '456.0', 456.0)"
            )
            conn.execute(
                """
                INSERT INTO mikan_jobs(
                    job_name, status, request_count, worker_id, lease_until, payload_json,
                    requested_at, started_at, updated_at, finished_at, last_error
                )
                VALUES ('redownload_all', 'running', 2, 'worker-1', 9999999999, '{"queued": 4}', 10, 20, 30, 0, '')
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name,
                    target_path, current_file_timestamp, current_file_time_kind, current_file_size,
                    episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:abc', 'running', 1, 'abc', 'Episode 02',
                        '/qbit/Episode 02.mkv', 1234, 'created', 456,
                        '[2]', '', 10, 20, 15, 0)
                """
            )
            conn.executemany(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES (?, 'success', 1, ?, ?, ?, '', ?, ?, ?, ?)
                """,
                [
                    ("hash:old", "old", "Episode 01", "[1]", 30, 100, 40, 100),
                    ("hash:new", "new", "Episode 03", "[3]", 50, 200, 60, 200),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        summary = self.module._mikan_summary({"mikan_pending_path": "mikan_pending.json"}, include_downloads=False)

        self.assertNotIn("downloads", summary)
        state_db = summary["state_db"]
        self.assertTrue(state_db["exists"])
        self.assertTrue(state_db["table_exists"])
        self.assertEqual(state_db["total"], 3)
        self.assertEqual(state_db["active"], 2)
        self.assertEqual(state_db["stalled"], 1)
        self.assertEqual(state_db["zero_speed_downloading"], 1)
        self.assertEqual(state_db["replacement_needed"], 1)
        self.assertEqual(state_db["counts"]["extract_failed"], 1)
        self.assertEqual(state_db["last_sync_at"], 456.0)
        self.assertEqual(state_db["recent_events"][0]["event"], "qbit_state_changed")
        self.assertTrue(any(item["next_action"] == "extract_subtitles" for item in state_db["active_samples"]))
        self.assertEqual(state_db["jobs"][0]["job_name"], "redownload_all")
        self.assertEqual(state_db["jobs"][0]["status"], "running")
        self.assertEqual(state_db["jobs"][0]["payload"]["queued"], 4)
        self.assertEqual(state_db["extract_jobs"]["active"], 1)
        self.assertEqual(state_db["extract_jobs"]["counts"]["running"], 1)
        self.assertEqual(state_db["extract_jobs"]["recent"][0]["episodes"], [2])
        self.assertEqual(state_db["extract_jobs"]["recent"][0]["current_file_path"], "/qbit/Episode 02.mkv")
        self.assertEqual(state_db["extract_jobs"]["recent"][0]["current_file_timestamp"], 1234)
        self.assertEqual(state_db["extract_jobs"]["recent"][0]["current_file_time_kind"], "created")
        self.assertEqual(state_db["extract_jobs"]["recent"][0]["current_file_size"], 456)
        self.assertEqual(
            [item["job_key"] for item in state_db["extract_jobs"]["recent_completed"]],
            ["hash:new", "hash:old"],
        )

    def test_mikan_downloads_summary_uses_sqlite_state_for_paged_rows(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        batch_one = {
            "bangumi_id": 1,
            "episode": 1,
            "episodes": [1, 2],
            "title": "Batch E01-E02",
            "source": "nyaa",
            "source_page": "https://nyaa.si/view/123",
            "torrent_url": "https://mikan/batch.torrent",
            "queued_at": "2026-06-07T04:00:00+00:00",
            "last_progress": 0.5,
            "last_downloaded": 1024,
            "last_qbit_state": "downloading",
        }
        batch_two = {
            **batch_one,
            "episode": 2,
            "last_progress": 0.4,
            "last_downloaded": 512,
        }
        completed = {
            "bangumi_id": 1,
            "episode": 3,
            "title": "Completed E03",
            "completed_at": "2026-06-07T05:00:00+00:00",
            "last_extracted_count": 2,
            "total_extracted_count": 2,
        }
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE mikan_download_items (
                    key TEXT PRIMARY KEY,
                    bangumi_id INTEGER,
                    episode INTEGER,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    torrent_url TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0,
                    last_extracted_count INTEGER NOT NULL DEFAULT 0,
                    total_extracted_count INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO mikan_download_items(
                    key, bangumi_id, episode, status, title, torrent_url,
                    updated_at, last_extracted_count, total_extracted_count, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("1:1", 1, 1, "downloading", "Batch E01-E02", "https://mikan/batch.torrent", 200.0, 0, 0, json.dumps(batch_one)),
                    ("1:2", 1, 2, "downloading", "Batch E01-E02", "https://mikan/batch.torrent", 190.0, 0, 0, json.dumps(batch_two)),
                    ("1:3", 1, 3, "completed", "Completed E03", "", 100.0, 2, 2, json.dumps(completed)),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        (self.module.WORK_PATH / "mikan_pending.json").write_text(
            json.dumps(
                {
                    "items": {
                        "stale:1": {
                            "bangumi_id": 999,
                            "episode": 1,
                            "title": "Stale pending JSON should not be used when SQLite is complete",
                            "last_progress": 1.0,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        summary = self.module._mikan_downloads_summary({"mikan_pending_path": "mikan_pending.json"}, page=1, page_size=1)

        self.assertTrue(summary["exists"])
        self.assertEqual(summary["source"], "sqlite")
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["filtered"], 2)
        self.assertEqual(summary["page_count"], 2)
        self.assertEqual(summary["counts"]["downloading"], 1)
        self.assertEqual(summary["counts"]["completed"], 1)
        self.assertEqual(summary["extracted_total"], 2)
        self.assertEqual(summary["recent"][0]["episodes"], [1, 2])
        self.assertEqual(summary["recent"][0]["source"], "nyaa")
        self.assertEqual(summary["recent"][0]["source_page"], "https://nyaa.si/view/123")
        self.assertEqual(len(summary["recent"][0]["children"]), 2)
        self.assertTrue((self.module.WORK_PATH / "mikan_pending.json").exists())

        completed_only = self.module._mikan_downloads_summary(
            {"mikan_pending_path": "mikan_pending.json"},
            page=1,
            page_size=20,
            status_filter="completed",
        )
        self.assertEqual(completed_only["total"], 2)
        self.assertEqual(completed_only["filtered"], 1)
        self.assertEqual(completed_only["page_count"], 1)
        self.assertEqual([row["status"] for row in completed_only["recent"]], ["completed"])

        searched = self.module._mikan_downloads_summary(
            {"mikan_pending_path": "mikan_pending.json"},
            page=1,
            page_size=20,
            search="Completed E03",
        )
        self.assertEqual(searched["filtered"], 1)
        self.assertEqual(searched["recent"][0]["title"], "Completed E03")

        searched_source = self.module._mikan_downloads_summary(
            {"mikan_pending_path": "mikan_pending.json"},
            page=1,
            page_size=20,
            search="nyaa",
        )
        self.assertEqual(searched_source["filtered"], 1)
        self.assertEqual(searched_source["recent"][0]["source"], "nyaa")

    def test_mikan_downloads_overlay_running_extract_job_from_sqlite(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        entry = {
            "bangumi_id": 2299,
            "episode": 12,
            "title": "Higurashi Gou E12",
            "torrent_url": "qbit://09882fd0a78d80cb7659cd177c03ab8a30ecac70",
            "queued_at": "2026-06-23T15:00:00+00:00",
            "last_progress": 1.0,
            "last_downloaded": 345678901,
            "last_qbit_hash": "09882fd0a78d80cb7659cd177c03ab8a30ecac70",
            "last_qbit_name": "[Sakurato] Higurashi no Naku Koro ni Gou [12]",
            "last_extract_deferred_at": "2026-06-23T15:05:00+00:00",
            "last_extract_deferred_reason": "target_video_not_found",
        }
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE mikan_download_items (
                    key TEXT PRIMARY KEY,
                    bangumi_id INTEGER,
                    episode INTEGER,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    torrent_url TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0,
                    last_extracted_count INTEGER NOT NULL DEFAULT 0,
                    total_extracted_count INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL
                );
                CREATE TABLE mikan_extract_jobs (
                    job_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    torrent_hash TEXT NOT NULL DEFAULT '',
                    torrent_name TEXT NOT NULL DEFAULT '',
                    episodes_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_download_items(
                    key, bangumi_id, episode, status, title, torrent_url,
                    updated_at, last_extracted_count, total_extracted_count, raw_json
                )
                VALUES ('2299:12', 2299, 12, 'completed_waiting_extract', 'Higurashi Gou E12',
                        'qbit://09882fd0a78d80cb7659cd177c03ab8a30ecac70', 10, 0, 0, ?)
                """,
                (json.dumps(entry),),
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:09882', 'running', 3, '09882fd0a78d80cb7659cd177c03ab8a30ecac70',
                        '[Sakurato] Higurashi no Naku Koro ni Gou [12]', '[12]', '', 10, 30, 20, 0)
                """
            )
            conn.commit()
        finally:
            conn.close()

        summary = self.module._mikan_downloads_summary(
            {"mikan_pending_path": "mikan_pending.json"},
            status_filter="extracting_subtitles",
        )

        self.assertFalse((self.module.WORK_PATH / "mikan_pending.json").exists())
        self.assertEqual(summary["source"], "sqlite")
        self.assertEqual(summary["counts"]["extracting_subtitles"], 1)
        self.assertEqual(summary["filtered"], 1)
        self.assertEqual(summary["recent"][0]["status"], "extracting_subtitles")
        self.assertEqual(summary["recent"][0]["extract_job_attempts"], 3)

    def test_mikan_downloads_overlay_extract_job_from_sqlite_by_torrent_name(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        title = "[Group] Missing Hash Show - 03 [CHT]"
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE mikan_download_items (
                    key TEXT PRIMARY KEY,
                    bangumi_id INTEGER,
                    episode INTEGER,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    torrent_url TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0,
                    last_extracted_count INTEGER NOT NULL DEFAULT 0,
                    total_extracted_count INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL
                );
                CREATE TABLE mikan_extract_jobs (
                    job_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    torrent_hash TEXT NOT NULL DEFAULT '',
                    torrent_name TEXT NOT NULL DEFAULT '',
                    episodes_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_download_items(
                    key, bangumi_id, episode, status, title, torrent_url,
                    updated_at, last_extracted_count, total_extracted_count, raw_json
                )
                VALUES ('300:3', 300, 3, 'completed', ?, '', 10, 0, 0, ?)
                """,
                (title, json.dumps({"title": title, "episode": 3})),
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:missing-row-hash', 'queued', 1, 'missing-row-hash',
                        ?, '[3]', '', 10, 30, 0, 0)
                """,
                (title,),
            )
            conn.commit()
        finally:
            conn.close()

        summary = self.module._mikan_downloads_summary(
            {"mikan_pending_path": "mikan_pending.json"},
            status_filter="completed_waiting_extract",
        )

        self.assertEqual(summary["filtered"], 1)
        self.assertEqual(summary["recent"][0]["status"], "completed_waiting_extract")
        self.assertEqual(summary["recent"][0]["extract_job_status"], "queued")

    def test_terminal_failed_extract_jobs_are_not_active_recent(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE mikan_extract_jobs (
                    job_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    torrent_hash TEXT NOT NULL DEFAULT '',
                    torrent_name TEXT NOT NULL DEFAULT '',
                    episodes_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:terminal', 'terminal_failed', 77, 'terminal', 'Old terminal failure', '[1]',
                        'permanent', 1, 2, 1, 2)
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    result_json, last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:retryable', 'failed', 2, 'retryable', 'Retryable failure', '[2]', '{"retryable": true}',
                        'target missing', 3, 4, 3, 4)
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:replaced', 'replaced', 2, 'replaced', 'Replaced history', '[3]',
                        'bad source', 5, 6, 5, 6)
                """
            )
            conn.commit()

            summary = self.module._mikan_extract_jobs_from_conn(conn)
        finally:
            conn.close()

        self.assertEqual(summary["counts"]["terminal_failed"], 1)
        self.assertEqual(summary["counts"]["failed"], 1)
        self.assertEqual(summary["counts"]["replaced"], 1)
        self.assertEqual(summary["retryable_count"], 1)
        self.assertEqual(summary["recent"], [])
        self.assertEqual(
            [job["job_key"] for job in summary["recent_failed"]],
            ["hash:retryable", "hash:replaced", "hash:terminal"],
        )
        self.assertEqual([job["job_key"] for job in summary["recent_retryable"]], ["hash:retryable"])
        self.assertEqual([job["job_key"] for job in summary["recent_attention"]], ["hash:terminal"])
        self.assertEqual([job["job_key"] for job in summary["recent_replaced"]], ["hash:replaced"])

    def test_mikan_downloads_can_page_terminal_failed_extract_jobs(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE mikan_download_items (
                    key TEXT PRIMARY KEY,
                    bangumi_id INTEGER,
                    episode INTEGER,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    torrent_url TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0,
                    last_extracted_count INTEGER NOT NULL DEFAULT 0,
                    total_extracted_count INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL
                );
                CREATE TABLE mikan_extract_jobs (
                    job_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    torrent_hash TEXT NOT NULL DEFAULT '',
                    torrent_name TEXT NOT NULL DEFAULT '',
                    episodes_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_download_items(
                    key, bangumi_id, episode, status, title, torrent_url,
                    updated_at, last_extracted_count, total_extracted_count, raw_json
                )
                VALUES ('1:1', 1, 1, 'completed', 'Completed item', '', 100, 1, 1, '{}')
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:bad', 'terminal_failed', 3, 'bad', 'Bad subtitle source', '[7]',
                        'no usable Chinese', 10, 40, 20, 40)
                """
            )
            conn.commit()
        finally:
            conn.close()

        summary = self.module._mikan_downloads_summary(
            {"mikan_pending_path": "mikan_pending.json"},
            status_filter="terminal_failed",
        )

        self.assertEqual(summary["source"], "sqlite")
        self.assertEqual(summary["filtered"], 1)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["recent"][0]["status"], "terminal_failed")
        self.assertEqual(summary["recent"][0]["title"], "Bad subtitle source")
        self.assertEqual(summary["recent"][0]["episodes"], [7])
        self.assertEqual(summary["recent"][0]["last_extract_failure_detail"], "no usable Chinese")

    def test_lite_status_counts_running_extract_overlay(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        entry = {
            "bangumi_id": 2299,
            "episode": 12,
            "title": "Higurashi Gou E12",
            "torrent_url": "qbit://09882fd0a78d80cb7659cd177c03ab8a30ecac70",
            "queued_at": "2026-06-23T15:00:00+00:00",
            "last_progress": 1.0,
            "last_qbit_hash": "09882fd0a78d80cb7659cd177c03ab8a30ecac70",
        }
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE mikan_download_items (
                    key TEXT PRIMARY KEY,
                    bangumi_id INTEGER,
                    episode INTEGER,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    torrent_url TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0,
                    last_extracted_count INTEGER NOT NULL DEFAULT 0,
                    total_extracted_count INTEGER NOT NULL DEFAULT 0,
                    last_qbit_hash TEXT,
                    last_qbit_state TEXT,
                    last_dlspeed INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL
                );
                CREATE TABLE mikan_extract_jobs (
                    job_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    torrent_hash TEXT NOT NULL DEFAULT '',
                    torrent_name TEXT NOT NULL DEFAULT '',
                    episodes_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_download_items(
                    key, bangumi_id, episode, status, title, torrent_url,
                    updated_at, last_extracted_count, total_extracted_count,
                    last_qbit_hash, last_qbit_state, last_dlspeed, raw_json
                )
                VALUES ('2299:12', 2299, 12, 'target_missing', 'Higurashi Gou E12',
                        'qbit://09882fd0a78d80cb7659cd177c03ab8a30ecac70',
                        10, 0, 0, '09882fd0a78d80cb7659cd177c03ab8a30ecac70', '', 0, ?)
                """,
                (json.dumps(entry),),
            )
            conn.execute(
                """
                INSERT INTO mikan_extract_jobs(
                    job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                    last_error, created_at, updated_at, started_at, finished_at
                )
                VALUES ('hash:09882', 'running', 3, '09882fd0a78d80cb7659cd177c03ab8a30ecac70',
                        '[Sakurato] Higurashi no Naku Koro ni Gou [12]', '[12]', '', 10, 30, 20, 0)
                """
            )
            conn.commit()
        finally:
            conn.close()

        payload = self.module.status(lite=True)
        state_db = payload["mikan"]["state_db"]

        self.assertEqual(state_db["counts"]["extracting_subtitles"], 1)
        self.assertEqual(state_db["counts"].get("completed_waiting_extract", 0), 0)
        self.assertEqual(state_db["pipeline"]["extracting"], 1)
        self.assertEqual(state_db["pipeline"]["waiting_extract"], 0)
        self.assertEqual(state_db["active"], 1)
        self.assertEqual(state_db["extract_jobs"]["active"], 1)

    def test_mikan_summary_reports_jobs_even_without_download_items_table(self) -> None:
        db_path = self.module.WORK_PATH / "mikan_state.sqlite3"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE mikan_jobs (
                    job_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    requested_at REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                INSERT INTO mikan_jobs(
                    job_name, status, request_count, worker_id, lease_until, payload_json,
                    requested_at, started_at, updated_at, finished_at, last_error
                )
                VALUES ('redownload_all', 'deferred', 3, '', 0, '{"reason": "queue busy"}', 1, 0, 2, 0, '')
                """
            )
            conn.commit()
        finally:
            conn.close()

        summary = self.module._mikan_summary({"mikan_pending_path": "mikan_pending.json"}, include_downloads=False)

        state_db = summary["state_db"]
        self.assertTrue(state_db["exists"])
        self.assertFalse(state_db["table_exists"])
        self.assertEqual(state_db["jobs"][0]["job_name"], "redownload_all")
        self.assertEqual(state_db["jobs"][0]["status"], "deferred")
        self.assertEqual(state_db["jobs"][0]["payload"]["reason"], "queue busy")

    def test_mikan_pending_path_expands_worker_env_default_syntax(self) -> None:
        pending_path = self.module.WORK_PATH / "mikan_pending.json"
        pending_path.write_text('{"items":{}}', encoding="utf-8")

        summary = self.module._mikan_summary({"mikan_pending_path": "${ANIME_WORK_PATH:-/work}/mikan_pending.json"})

        self.assertTrue(summary["downloads"]["exists"])
        self.assertEqual(Path(summary["downloads"]["path"]), pending_path)
        self.assertNotIn("${ANIME_WORK_PATH", summary["downloads"]["path"])

    def test_mikan_redownload_action_is_registered(self) -> None:
        command = self.module.ACTION_COMMANDS["mikan-redownload-all"]
        self.assertIn("--mikan-redownload-all", command)

    def test_mikan_requeue_failed_extracts_action_is_registered(self) -> None:
        command = self.module.ACTION_COMMANDS["mikan-requeue-failed-extracts"]
        self.assertIn("--mikan-requeue-failed-extracts", command)

    def test_backup_state_action_is_registered(self) -> None:
        command = self.module.ACTION_COMMANDS["backup-state"]
        self.assertIn("/app/backup_state.py", command)

    def test_series_profile_reads_and_mutations_use_worker_mailbox(self) -> None:
        anime_root = self.tmp / "anime"
        series = anime_root / "Example Anime"
        series.mkdir(parents=True)
        database = self.module.WORK_PATH / "series_metadata.sqlite3"
        local_path_key = str(series.resolve()).casefold()
        series_id = self.module.stable_id("series", local_path_key)
        with sqlite3.connect(database) as conn:
            conn.executescript(
                """
                CREATE TABLE series_profiles (
                    local_path_key TEXT PRIMARY KEY,
                    series_id TEXT NOT NULL UNIQUE,
                    local_path TEXT NOT NULL,
                    canonical_title TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    synopsis TEXT NOT NULL DEFAULT '',
                    locked INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE series_glossary (
                    local_path_key TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    target_text TEXT NOT NULL,
                    term_type TEXT NOT NULL,
                    locked INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'manual',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(local_path_key, source_text)
                );
                """
            )
            conn.execute(
                "INSERT INTO series_profiles VALUES (?, ?, ?, 'Example Anime', 'anilist', '123', '[]', '', 1, ?)",
                (local_path_key, series_id, str(series), time.time()),
            )
            conn.execute(
                "INSERT INTO series_glossary VALUES (?, '先輩', '學長', 'name', 1, 'manual', ?, ?)",
                (local_path_key, time.time(), time.time()),
            )
        config = {
            "input_path": str(anime_root),
            "work_path": str(self.module.WORK_PATH),
            "series_metadata_db_path": str(database),
            "control_inbox_path": "control_inbox",
        }
        with patch.object(self.module, "_load_config", return_value=config):
            summary = self.module._series_profiles_summary(page=1, page_size=20, search="Example")
            detail = self.module._series_profile_detail(str(series))
            stable_detail = self.module._series_profile_detail_by_id(summary["items"][0]["series_id"])

            class FakeRequest:
                def __init__(self, payload: dict, key: str) -> None:
                    self.payload = payload
                    self.headers = {"idempotency-key": key}

                async def json(self) -> dict:
                    return self.payload

            commands = [
                asyncio.run(self.module.set_series_profile_lock(FakeRequest({"path": str(series), "locked": False}, "lock"))),
                asyncio.run(self.module.set_series_profile_match(FakeRequest({"path": str(series), "provider": "anilist", "provider_id": "456", "title": "New Title"}, "match"))),
                asyncio.run(self.module.upsert_series_glossary(FakeRequest({"path": str(series), "source_text": "先生", "target_text": "老師", "term_type": "name"}, "term"))),
                asyncio.run(self.module.delete_series_glossary(FakeRequest({"path": str(series), "source_text": "先輩"}, "delete"))),
            ]
            unchanged = self.module._series_profile_detail(str(series))

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["items"][0]["provider_id"], "123")
        self.assertTrue(detail["profile"]["locked"])
        self.assertEqual(stable_detail["profile"]["provider_id"], "123")
        self.assertEqual(detail["glossary"][0]["target_text"], "學長")
        self.assertEqual(
            [item["action"] for item in commands],
            ["series.lock", "series.match", "series.glossary_upsert", "series.glossary_delete"],
        )
        self.assertTrue(unchanged["profile"]["locked"])
        self.assertEqual(unchanged["profile"]["provider_id"], "123")
        self.assertEqual(unchanged["glossary"][0]["source_text"], "先輩")

    def test_series_artwork_serves_only_verified_work_cache_file(self) -> None:
        database = self.module.WORK_PATH / "series_metadata.sqlite3"
        artwork_root = self.module.WORK_PATH / "series_artwork"
        artwork_root.mkdir()
        series_id = "series_" + "a" * 24
        cache_key = f"{series_id}.jpg"
        artwork = artwork_root / cache_key
        artwork.write_bytes(b"\xff\xd8\xffimage")
        with sqlite3.connect(database) as conn:
            conn.execute(
                "CREATE TABLE series_profiles(series_id TEXT PRIMARY KEY, cover_image_cache_key TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO series_profiles VALUES (?, ?)", (series_id, cache_key))
        config = {
            "work_path": str(self.module.WORK_PATH),
            "series_metadata_db_path": str(database),
            "series_artwork_cache_path": str(artwork_root),
        }

        with patch.object(self.module, "_load_config", return_value=config):
            response = self.module.v2_series_artwork(series_id)
            with sqlite3.connect(database) as conn:
                conn.execute(
                    "UPDATE series_profiles SET cover_image_cache_key='../outside.jpg' WHERE series_id=?",
                    (series_id,),
                )
            with self.assertRaises(self.module.HTTPException) as rejected:
                self.module.v2_series_artwork(series_id)

        self.assertEqual(Path(response.path), artwork)
        self.assertEqual(response.media_type, "image/jpeg")
        self.assertIn("max-age", response.headers["Cache-Control"])
        self.assertEqual(rejected.exception.status_code, 404)

    def test_ai_diagnostics_reads_digest_scoped_provenance_and_audio_manifest(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Example" / "Season 1" / "Episode.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"video")
        provenance = self.module.WORK_PATH / "provenance"
        audio = self.module.WORK_PATH / "audio_selection"
        provenance.mkdir()
        audio.mkdir()
        digest = self.module.hashlib.sha1(str(video.resolve()).encode("utf-8")).hexdigest()[:20]
        (provenance / f"{digest}.json").write_text('{"status":"complete","asr":{"model":"large-v3"}}', encoding="utf-8")
        (audio / f"{digest}.json").write_text('{"selected":{"index":2}}', encoding="utf-8")
        config = {
            "input_path": str(anime_root),
            "work_path": str(self.module.WORK_PATH),
            "processing_provenance_path": "provenance",
            "audio_selection_manifest_path": "audio_selection",
        }
        with patch.object(self.module, "_load_config", return_value=config):
            result = self.module._ai_diagnostics_for_video(str(video))

        self.assertEqual(result["provenance"]["asr"]["model"], "large-v3")
        self.assertEqual(result["audio_selection"]["selected"]["index"], 2)

    def test_v2_path_expands_worker_default_without_media_mount_and_rejects_escape(self) -> None:
        config = {"input_path": "${ANIME_INPUT_PATH:-/anime}", "work_path": str(self.module.WORK_PATH)}
        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(self.module, "_load_config", return_value=config),
        ):
            os.environ.pop("ANIME_INPUT_PATH", None)
            accepted = self.module._validated_anime_path("/anime/Series/Season 1/Episode.mkv")
            with self.assertRaises(self.module.HTTPException) as escaped:
                self.module._validated_anime_path("/anime/../etc/passwd")

        self.assertTrue(accepted.as_posix().endswith("/anime/Series/Season 1/Episode.mkv"))
        self.assertEqual(escaped.exception.status_code, 400)

    def test_v2_command_inbox_is_idempotent_and_does_not_write_scanner_database(self) -> None:
        anime_root = self.tmp / "anime"
        target = anime_root / "Series" / "Season 1" / "Episode.mkv"
        config = {
            "input_path": str(anime_root),
            "work_path": str(self.module.WORK_PATH),
            "control_inbox_path": "control_inbox",
            "control_state_path": "control_state.sqlite3",
        }

        class Request:
            headers = {"idempotency-key": "same-operation"}

            async def json(self):
                return {"action": "ai.retry", "target": str(target), "parameters": {}}

        with patch.object(self.module, "_load_config", return_value=config):
            first = asyncio.run(self.module.v2_create_command(Request()))
            second = asyncio.run(self.module.v2_create_command(Request()))

        self.assertEqual(first["command_id"], second["command_id"])
        inbox_files = list((self.module.WORK_PATH / "control_inbox").glob("*.json"))
        self.assertEqual(len(inbox_files), 1)
        command = json.loads(inbox_files[0].read_text(encoding="utf-8"))
        self.assertEqual(command["action"], "ai.retry")
        self.assertFalse((self.module.WORK_PATH / "scanner_state.sqlite3").exists())

    def test_v2_cancel_extract_command_is_accepted_without_media_path_validation(self) -> None:
        config = {
            "input_path": "/anime",
            "work_path": str(self.module.WORK_PATH),
            "control_inbox_path": "control_inbox",
            "control_state_path": "control_state.sqlite3",
        }

        class Request:
            headers = {"idempotency-key": "cancel-extract-hash-test"}

            async def json(self):
                return {
                    "action": "mikan.cancel_extract",
                    "parameters": {"job_key": "hash:test"},
                }

        with patch.object(self.module, "_load_config", return_value=config):
            result = asyncio.run(self.module.v2_create_command(Request()))

        command = json.loads(
            (self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(command["action"], "mikan.cancel_extract")
        self.assertEqual(command["parameters"]["job_key"], "hash:test")
        self.assertIn("mikan.requeue_extract", self.module.V2_COMMAND_ACTIONS)
        self.assertIn("review.dismiss", self.module.V2_COMMAND_ACTIONS)

    def test_anytime_eprocess_crossings_are_exact_and_can_regress_after_a_miss(self) -> None:
        log_e = self.module._ai_delivery_anytime_log_e
        lower = self.module._ai_delivery_anytime_lower_bound
        threshold = self.module.AI_DELIVERY_ANYTIME_LOG_THRESHOLD
        for misses, sample in ((0, 38_856), (1, 62_786), (2, 85_517)):
            self.assertLess(log_e(0.9999, sample - 1 - misses, misses), threshold)
            self.assertGreaterEqual(log_e(0.9999, sample - misses, misses), threshold)
            self.assertLess(lower(sample - 1 - misses, misses), 0.9999)
            self.assertGreaterEqual(lower(sample - misses, misses), 0.9999)
        self.assertGreaterEqual(log_e(0.9999, 38_856, 0), threshold)
        self.assertLess(log_e(0.9999, 38_856, 1), threshold)

    def test_anytime_eprocess_log_domain_handles_extreme_counts(self) -> None:
        log_e = self.module._ai_delivery_anytime_log_e
        self.assertAlmostEqual(log_e(0.9999, 1_000_000_000, 0), 90004.2571858, places=6)
        self.assertAlmostEqual(
            log_e(0.9999, 999_900_000, 100_000),
            -19316.6612865,
            places=6,
        )

    def test_expected_deadline_corruption_is_a_matured_miss(self) -> None:
        verification = json.dumps(
            {
                "publication_semantics_verified": True,
                "publication_contract": self.module.AI_DELIVERY_PUBLICATION_CONTRACT,
                "publication_kind": "translated_trilingual",
                "output_languages": ["ja", "zh-CN", "zh-TW"],
                "expected_policy_revision": "policy-v1",
                "manifest_policy_revision": "policy-v1",
                "policy_revision_matched": True,
            }
        )
        eligible = 1_000.0
        expected_due = eligible + self.module.AI_DELIVERY_DEADLINE_SECONDS
        rows = [
            (
                "extended", "succeeded", eligible, expected_due + 100_000,
                expected_due - 1, expected_due - 1, expected_due - 1,
                1, "", "policy-v1", verification,
            ),
            (
                "malformed", "excluded", eligible + 1, "not-a-number",
                0, eligible + 2, eligible + 2, "bad-int",
                "media_missing_before_attempt", "policy-v1", "{}",
            ),
        ]
        summary = self.module._summarize_ai_delivery_rows(
            rows,
            expected_due_from=expected_due,
            expected_due_to=expected_due + 10,
            policy_revision="policy-v1",
        )
        self.assertEqual(summary["denominator"], 2)
        self.assertEqual(summary["numerator"], 0)
        self.assertEqual(summary["invalid_contract_misses"], 2)
        self.assertEqual(summary["invalid_exclusions"], 1)

    def test_publication_v2_counts_only_strict_traditional_chinese_delivery(self) -> None:
        self.assertEqual(
            self.module.AI_DELIVERY_MEASUREMENT_REVISION,
            "ai-delivery-99.99-strict-traditional-chinese-source-priority-full-inventory-continuous-anytime-eprocess-v5",
        )
        self.assertEqual(
            self.module.AI_DELIVERY_PUBLICATION_CONTRACT,
            "ai-publication-semantics-v2",
        )
        self.assertEqual(
            self.module.AI_DELIVERY_TRADITIONAL_CHINESE_PUBLICATION_KINDS,
            {"translated_trilingual", "adopted_zh_tw", "converted_zh_cn"},
        )
        eligible = 1_000.0
        expected_due = eligible + self.module.AI_DELIVERY_DEADLINE_SECONDS

        def verification(kind: str, languages: list[str], *, contract: str | None = None) -> str:
            return json.dumps(
                {
                    "publication_semantics_verified": True,
                    "publication_contract": contract or self.module.AI_DELIVERY_PUBLICATION_CONTRACT,
                    "publication_kind": kind,
                    "output_languages": languages,
                    "expected_policy_revision": "policy-v1",
                    "manifest_policy_revision": "policy-v1",
                    "policy_revision_matched": True,
                }
            )

        evidence = [
            ("translated", "translated_trilingual", ["ja", "zh-CN", "zh-TW"], None),
            ("adopted", "adopted_zh_tw", ["zh-TW"], None),
            ("converted", "converted_zh_cn", ["zh-TW"], None),
            ("source-only", "source_language", ["en"], None),
            ("old-contract", "translated_trilingual", ["ja", "zh-CN", "zh-TW"], "ai-publication-semantics-v1"),
            ("wrong-adopted-language", "adopted_zh_tw", ["zh-CN"], None),
            ("incomplete-trilingual", "translated_trilingual", ["zh-CN", "zh-TW"], None),
        ]
        rows = [
            (
                obligation_id,
                "succeeded",
                eligible,
                expected_due,
                expected_due - 1,
                expected_due - 1,
                expected_due - 1,
                1,
                "",
                "policy-v1",
                verification(kind, languages, contract=contract),
            )
            for obligation_id, kind, languages, contract in evidence
        ]

        summary = self.module._summarize_ai_delivery_rows(
            rows,
            expected_due_from=expected_due,
            expected_due_to=expected_due + 1,
            policy_revision="policy-v1",
        )

        self.assertEqual(summary["denominator"], 7)
        self.assertEqual(summary["numerator"], 3)
        self.assertEqual(summary["misses"], 4)
        self.assertEqual(summary["invalid_success_evidence"], 4)
        breakdown = summary["publication_breakdown"]
        self.assertEqual(breakdown["translated_chinese"]["verified_on_time"], 3)
        self.assertEqual(
            breakdown["translated_chinese"]["by_publication_kind"],
            {"adopted_zh_tw": 1, "converted_zh_cn": 1, "translated_trilingual": 1},
        )
        self.assertEqual(
            breakdown["translated_chinese"]["required_output_language"],
            "zh-TW",
        )
        self.assertEqual(breakdown["source_language"]["verified_on_time"], 0)
        self.assertFalse(
            breakdown["source_language"]["counts_as_traditional_chinese_success"]
        )

    def test_historical_epoch_recount_restarts_chain_after_old_ledger_deletion(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 2_000,
                inventory_completed_at=now - 1,
            )
            connection.execute(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, state, eligible_at, due_at, updated_at,
                    canonical_path, media_mtime_ns, media_fingerprint,
                    media_size, policy_revision
                ) VALUES ('old-obligation', 'open', ?, ?, ?, '/anime/old.mkv',
                          7, 'old-fingerprint', 11, 'policy-v1')
                """,
                (now - 1_900, now - 1_900 + 259_200, now - 1_900),
            )
            connection.execute(
                """
                INSERT INTO ai_inventory_epochs(
                    epoch_id, schema_version, measurement_revision, policy_revision,
                    root_signature, state, started_at, updated_at, completed_at,
                    observed_count, classified_count, delivery_required_count,
                    tracked_count, untracked_count, coverage_complete, dirty_generation
                ) VALUES ('old-epoch', 1, ?, 'policy-v1', 'root-v1', 'completed',
                          ?, ?, ?, 1, 1, 1, 1, 0, 1, 0)
                """,
                (
                    self.module.AI_DELIVERY_MEASUREMENT_REVISION,
                    now - 1_501,
                    now - 1_500,
                    now - 1_500,
                ),
            )
            connection.execute(
                """
                INSERT INTO ai_media_inventory(
                    inventory_id, epoch_id, canonical_path, media_fingerprint,
                    media_size, media_mtime_ns, policy_revision, classification,
                    disposition, requires_ledger, obligation_id
                ) VALUES ('old-item', 'old-epoch', '/anime/old.mkv',
                          'old-fingerprint', 11, 7, 'policy-v1', 'needs_ai',
                          'delivery_required', 1, 'old-obligation')
                """
            )
        before = self.module._ai_delivery_slo_summary(now=now)
        self.assertEqual(before["continuous_coverage_since"], now - 1_500)
        self.assertEqual(before["coverage_chain_epoch_count"], 2)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "DELETE FROM ai_delivery_obligations WHERE obligation_id='old-obligation'"
            )
        after = self.module._ai_delivery_slo_summary(now=now)
        self.assertEqual(after["continuous_coverage_since"], now - 1)
        self.assertEqual(after["coverage_chain_epoch_count"], 1)

    def test_clopper_pearson_lower_bound_is_exact_and_stable_at_boundaries(self) -> None:
        lower_bound = self.module._clopper_pearson_lower_bound

        self.assertIsNone(lower_bound(0, 0))
        self.assertEqual(lower_bound(0, 10), 0.0)
        self.assertAlmostEqual(lower_bound(10, 10), 0.05 ** (1 / 10), places=14)
        self.assertAlmostEqual(
            lower_bound(5, 10),
            0.22244110100812908,
            places=13,
        )
        self.assertAlmostEqual(
            lower_bound(9, 10),
            0.6058366975634952,
            places=13,
        )
        with self.assertRaises(ValueError):
            lower_bound(11, 10)
        with self.assertRaises(ValueError):
            lower_bound(1, 1, confidence_level=1.0)

        self.assertEqual(self.module.AI_DELIVERY_SLO_MINIMUM_ZERO_MISS_SAMPLE, 29_956)
        self.assertLess(lower_bound(29_955, 29_955), self.module.AI_DELIVERY_SLO_TARGET)
        self.assertGreaterEqual(lower_bound(29_956, 29_956), self.module.AI_DELIVERY_SLO_TARGET)

    def test_ai_delivery_slo_fails_closed_for_old_schema(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE ai_delivery_obligations(obligation_id TEXT PRIMARY KEY, state TEXT)"
            )

        summary = self.module.v2_ai_delivery_slo()

        self.assertEqual(summary["sample_state"], "unavailable")
        self.assertEqual(summary["denominator"], 0)
        self.assertEqual(summary["numerator"], 0)
        self.assertIsNone(summary["error_budget_remaining"])
        self.assertEqual(summary["target"], 0.9999)
        self.assertEqual(summary["minimum_sample"], 10_000)
        self.assertIsNone(summary["target_met"])
        self.assertEqual(summary["confidence_level"], 0.95)
        self.assertEqual(
            summary["confidence_method"],
            "clopper_pearson_exact_one_sided",
        )
        self.assertIsNone(summary["confidence_lower_bound"])
        self.assertIsNone(summary["confidence_target_met"])
        self.assertEqual(summary["minimum_zero_miss_sample"], 29_956)
        self.assertIsNone(summary["coverage_active_queue_total"])
        self.assertIsNone(summary["coverage_active_queue_tracked"])
        self.assertIsNone(summary["coverage_active_queue_untracked"])
        self.assertIsNone(summary["coverage_active_queue_complete"])
        self.assertIsNone(summary["measurement_revision"])

    def test_ai_delivery_slo_missing_measurement_revision_is_unavailable(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 40 * 86400,
                include_measurement_revision=False,
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["sample_state"], "unavailable")
        self.assertIsNone(summary["measurement_revision"])
        self.assertFalse(summary["full_window"])
        self.assertEqual(summary["denominator"], 0)

    def test_ai_delivery_slo_wrong_measurement_revision_is_unavailable(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 40 * 86400,
                measurement_revision="legacy-partial-proof-v0",
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["sample_state"], "unavailable")
        self.assertEqual(summary["measurement_revision"], "legacy-partial-proof-v0")
        self.assertFalse(summary["full_window"])
        self.assertEqual(summary["denominator"], 0)

    def test_ai_delivery_slo_fails_closed_when_attempt_count_is_missing(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE ai_delivery_meta(key TEXT PRIMARY KEY, value TEXT, updated_at REAL)"
            )
            connection.executemany(
                "INSERT INTO ai_delivery_meta VALUES (?, ?, ?)",
                [
                    ("schema_version", "1", now),
                    ("instrumented_at", str(now), now),
                    (
                        "measurement_revision",
                        self.module.AI_DELIVERY_MEASUREMENT_REVISION,
                        now,
                    ),
                ],
            )
            connection.execute(
                """
                CREATE TABLE ai_delivery_obligations(
                    obligation_id TEXT PRIMARY KEY,
                    canonical_path TEXT NOT NULL,
                    media_mtime_ns INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    eligible_at REAL NOT NULL,
                    due_at REAL NOT NULL,
                    verified_at REAL NOT NULL DEFAULT 0,
                    terminal_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    exclusion_code TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE ai_candidate_queue(
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["sample_state"], "unavailable")
        self.assertEqual(summary["denominator"], 0)
        self.assertIsNone(summary["confidence_lower_bound"])
        self.assertIsNone(summary["confidence_target_met"])
        self.assertIsNone(summary["coverage_active_queue_complete"])

    def test_ai_delivery_slo_fails_closed_when_queue_table_is_missing(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now,
                include_queue=False,
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["sample_state"], "unavailable")
        self.assertIsNone(summary["coverage_active_queue_total"])
        self.assertIsNone(summary["coverage_active_queue_tracked"])
        self.assertIsNone(summary["coverage_active_queue_untracked"])
        self.assertIsNone(summary["coverage_active_queue_complete"])

    def test_ai_delivery_slo_fails_closed_when_queue_mtime_is_missing(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now,
                include_queue=False,
            )
            connection.execute(
                "CREATE TABLE ai_candidate_queue(path TEXT PRIMARY KEY, status TEXT NOT NULL)"
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["sample_state"], "unavailable")
        self.assertIsNone(summary["coverage_active_queue_total"])
        self.assertIsNone(summary["coverage_active_queue_complete"])

    def test_ai_delivery_slo_reports_complete_active_queue_coverage(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        cutoff = now - 30 * 86400
        active_statuses = ("queued", "running", "failed_retry", "paused")
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=cutoff - 72 * 3600,
                inventory_completed_at=now,
            )
            connection.executemany(
                "INSERT INTO ai_candidate_queue(path, mtime_ns, status) VALUES (?, ?, ?)",
                [
                    (f"/anime/{status}.mkv", index, status)
                    for index, status in enumerate(active_statuses, start=1)
                ]
                + [("/anime/done.mkv", 99, "done")],
            )
            connection.executemany(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, canonical_path, media_mtime_ns, state,
                    eligible_at, due_at, updated_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?)
                """,
                [
                    (
                        f"active-{index}",
                        f"/anime/{status}.mkv",
                        index,
                        now - 100,
                        now + 72 * 3600,
                        now - 100,
                    )
                    for index, status in enumerate(active_statuses, start=1)
                ],
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["coverage_active_queue_total"], 4)
        self.assertEqual(summary["coverage_active_queue_tracked"], 4)
        self.assertEqual(summary["coverage_active_queue_untracked"], 0)
        self.assertTrue(summary["coverage_active_queue_complete"])
        self.assertEqual(summary["sample_state"], "warming")

    def test_ai_delivery_slo_coverage_incomplete_overrides_attainment(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        cutoff = now - 30 * 86400
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=cutoff - 72 * 3600,
                inventory_completed_at=now,
            )
            connection.executemany(
                "INSERT INTO ai_candidate_queue(path, mtime_ns, status) VALUES (?, ?, ?)",
                [
                    ("/anime/tracked.mkv", 101, "queued"),
                    ("/anime/untracked.mkv", 202, "failed_retry"),
                    ("/anime/done.mkv", 303, "done"),
                ],
            )
            connection.executemany(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, canonical_path, media_mtime_ns, state,
                    eligible_at, due_at, verified_at, terminal_at, updated_at,
                    attempt_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "mature-success", "/anime/historical.mkv", 1,
                        "succeeded", now - 1000, now - 100, now - 200,
                        now - 200, now - 200, 1,
                    ),
                    (
                        "tracked-open", "/anime/tracked.mkv", 101,
                        "open", now - 100, now + 72 * 3600, 0, 0,
                        now - 100, 0,
                    ),
                    (
                        "wrong-state", "/anime/untracked.mkv", 202,
                        "succeeded", now - 100, now + 72 * 3600, 0, 0,
                        now - 100, 1,
                    ),
                ],
            )

        with (
            patch.object(self.module, "AI_DELIVERY_SLO_MINIMUM_SAMPLE", 1),
            patch.object(
                self.module,
                "_clopper_pearson_lower_bound",
                return_value=1.0,
            ),
        ):
            summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["coverage_active_queue_total"], 2)
        self.assertEqual(summary["coverage_active_queue_tracked"], 1)
        self.assertEqual(summary["coverage_active_queue_untracked"], 1)
        self.assertEqual(
            summary["coverage_active_queue_untracked_breakdown"],
            {
                "by_reason": {"matching_obligation_not_open": 1},
                "by_status": {"failed_retry": 1},
            },
        )
        self.assertFalse(summary["coverage_active_queue_complete"])
        self.assertEqual(summary["sample_state"], "coverage_incomplete")
        self.assertEqual(summary["state"], "coverage_incomplete")
        self.assertEqual(summary["numerator"], 0)
        self.assertEqual(summary["denominator"], 0)
        self.assertIsNone(summary["target_met"])
        self.assertIsNone(summary["confidence_target_met"])

    def test_ai_delivery_slo_mtime_mismatch_is_untracked(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now,
                inventory_completed_at=now,
            )
            connection.execute(
                "INSERT INTO ai_candidate_queue VALUES ('/anime/changed.mkv', 222, 'paused')"
            )
            connection.execute(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, canonical_path, media_mtime_ns, state,
                    eligible_at, due_at, updated_at
                ) VALUES ('old-media', '/anime/changed.mkv', 111, 'open', ?, ?, ?)
                """,
                (now - 100, now + 72 * 3600, now - 100),
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["coverage_active_queue_total"], 1)
        self.assertEqual(summary["coverage_active_queue_tracked"], 0)
        self.assertEqual(summary["coverage_active_queue_untracked"], 1)
        self.assertEqual(
            summary["coverage_active_queue_untracked_breakdown"]["by_reason"],
            {"media_revision_mismatch": 1},
        )
        self.assertFalse(summary["coverage_active_queue_complete"])
        self.assertEqual(summary["sample_state"], "coverage_incomplete")
        self.assertIsNone(summary["target_met"])
        self.assertIsNone(summary["confidence_target_met"])

    def test_ai_delivery_slo_classifies_every_untracked_active_queue_reason(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 100,
                inventory_completed_at=now - 1,
            )
            connection.executemany(
                "INSERT INTO ai_candidate_queue(path, mtime_ns, status) VALUES (?, ?, ?)",
                [
                    ("/anime/missing.mkv", 1, "queued"),
                    ("/anime/media.mkv", 2, "paused"),
                    ("/anime/policy.mkv", 3, "failed_retry"),
                    ("/anime/terminal.mkv", 4, "running"),
                    ("/anime/tracked.mkv", 5, "queued"),
                ],
            )
            connection.executemany(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, canonical_path, media_mtime_ns, state,
                    eligible_at, due_at, updated_at, policy_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("old-media", "/anime/media.mkv", 1, "open", now - 10, now + 10, now, "policy-v1"),
                    ("old-policy", "/anime/policy.mkv", 3, "open", now - 10, now + 10, now, "policy-v0"),
                    ("terminal", "/anime/terminal.mkv", 4, "succeeded", now - 10, now + 10, now, "policy-v1"),
                    ("tracked", "/anime/tracked.mkv", 5, "open", now - 10, now + 10, now, "policy-v1"),
                ],
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["coverage_active_queue_total"], 5)
        self.assertEqual(summary["coverage_active_queue_tracked"], 1)
        self.assertEqual(summary["coverage_active_queue_untracked"], 4)
        self.assertEqual(
            summary["coverage_active_queue_untracked_breakdown"],
            {
                "by_reason": {
                    "matching_obligation_not_open": 1,
                    "media_revision_mismatch": 1,
                    "missing_obligation": 1,
                    "policy_revision_mismatch": 1,
                },
                "by_status": {
                    "failed_retry": 1,
                    "paused": 1,
                    "queued": 1,
                    "running": 1,
                },
            },
        )

    def test_ai_delivery_slo_reports_warming_for_empty_valid_schema(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now,
                inventory_completed_at=now,
            )

        summary = self.module.v2_ai_delivery_slo()

        self.assertEqual(summary["sample_state"], "warming")
        self.assertEqual(
            summary["measurement_revision"],
            self.module.AI_DELIVERY_MEASUREMENT_REVISION,
        )
        self.assertEqual(summary["denominator"], 0)
        self.assertIsNone(summary["error_budget_remaining"])
        self.assertEqual(summary["target"], 0.9999)
        self.assertEqual(summary["minimum_sample"], 10_000)
        self.assertIsNone(summary["target_met"])
        self.assertEqual(summary["confidence_level"], 0.95)
        self.assertIsNone(summary["confidence_lower_bound"])
        self.assertIsNone(summary["confidence_target_met"])
        self.assertEqual(summary["minimum_zero_miss_sample"], 29_956)
        self.assertEqual(summary["coverage_active_queue_total"], 0)
        self.assertEqual(summary["coverage_active_queue_tracked"], 0)
        self.assertEqual(summary["coverage_active_queue_untracked"], 0)
        self.assertTrue(summary["coverage_active_queue_complete"])
        self.assertTrue(summary["coverage_inventory_complete"])
        self.assertEqual(summary["coverage_inventory_total"], 0)
        self.assertTrue(summary["coverage_complete"])

        with sqlite3.connect(database) as connection:
            connection.executemany(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, state, eligible_at, due_at, verified_at,
                    terminal_at, updated_at, exclusion_code, attempt_count
                ) VALUES (?, 'succeeded', ?, ?, ?, ?, ?, '', 1)
                """,
                (
                    (
                        f"early-{index}",
                        now - 72 * 3600 - 100,
                        now - 100,
                        now - 200,
                        now - 200,
                        now - 200,
                    )
                    for index in range(29_956)
                ),
            )

        summary = self.module._ai_delivery_slo_summary(now=now + 1)

        self.assertEqual(summary["sample_state"], "warming")
        self.assertIsNone(summary["target_met"])
        self.assertEqual(summary["denominator"], 0)
        self.assertIsNone(summary["confidence_lower_bound"])
        self.assertIsNone(summary["confidence_target_met"])

    def test_ai_delivery_slo_requires_full_inventory_schema(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = time.time()
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now,
                include_inventory=False,
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["sample_state"], "unavailable")
        self.assertFalse(summary["coverage_inventory_available"])
        self.assertIsNone(summary["coverage_inventory_complete"])
        self.assertFalse(summary["coverage_complete"])

    def test_ai_delivery_slo_queue_zero_but_untracked_inventory_is_incomplete(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 100,
                inventory_completed_at=now - 1,
            )
            connection.execute(
                """
                INSERT INTO ai_media_inventory(
                    inventory_id, epoch_id, canonical_path, media_fingerprint,
                    media_size, media_mtime_ns, policy_revision, classification,
                    disposition, requires_ledger, obligation_id
                ) VALUES ('missing-ledger', 'test-inventory', '/anime/untracked.mkv',
                          'fp-untracked', 100, 200, 'policy-v1', 'needs_ai',
                          'delivery_required', 1, 'expected-obligation')
                """
            )
            connection.execute(
                """
                UPDATE ai_inventory_epochs
                SET observed_count=1, classified_count=1, delivery_required_count=1,
                    tracked_count=0, untracked_count=1, coverage_complete=0
                WHERE epoch_id='test-inventory'
                """
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["coverage_active_queue_total"], 0)
        self.assertTrue(summary["coverage_active_queue_complete"])
        self.assertEqual(summary["coverage_inventory_total"], 1)
        self.assertEqual(summary["coverage_inventory_untracked"], 1)
        self.assertFalse(summary["coverage_inventory_complete"])
        self.assertFalse(summary["coverage_complete"])
        self.assertEqual(summary["sample_state"], "coverage_incomplete")
        self.assertIsNone(summary["target_met"])

    def test_ai_delivery_slo_rejects_stale_or_newer_failed_inventory_epoch(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 100_000,
                inventory_completed_at=now - self.module.AI_INVENTORY_MAX_AGE_SECONDS - 1,
            )

        stale = self.module._ai_delivery_slo_summary(now=now)
        self.assertEqual(stale["coverage_inventory_state"], "inventory_stale")
        self.assertEqual(stale["sample_state"], "coverage_incomplete")

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE ai_inventory_epochs SET completed_at=?, updated_at=? WHERE epoch_id='test-inventory'",
                (now - 10, now - 10),
            )
            connection.execute(
                """
                INSERT INTO ai_inventory_epochs(
                    epoch_id, schema_version, measurement_revision, policy_revision,
                    root_signature, state, started_at, updated_at, completed_at,
                    failure_code
                ) VALUES ('newer-failed', 1, ?, 'policy-v1', 'root-v1',
                          'failed', ?, ?, 0, 'walk_error')
                """,
                (self.module.AI_DELIVERY_MEASUREMENT_REVISION, now - 5, now - 5),
            )

        failed = self.module._ai_delivery_slo_summary(now=now)
        self.assertEqual(failed["coverage_inventory_state"], "inventory_failed")
        self.assertFalse(failed["coverage_inventory_complete"])
        self.assertIsNone(failed["confidence_target_met"])

        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM ai_inventory_epochs WHERE epoch_id='newer-failed'")
            connection.execute(
                """
                INSERT INTO ai_inventory_epochs(
                    epoch_id, schema_version, measurement_revision, policy_revision,
                    root_signature, state, started_at, updated_at, completed_at,
                    dirty_generation
                ) VALUES ('stale-running', 1, ?, 'policy-v1', 'root-v1',
                          'running', ?, ?, 0, 0)
                """,
                (
                    self.module.AI_DELIVERY_MEASUREMENT_REVISION,
                    now - 5,
                    now - self.module.AI_INVENTORY_RUNNING_STALE_SECONDS - 1,
                ),
            )

        running = self.module._ai_delivery_slo_summary(now=now)
        self.assertEqual(running["coverage_inventory_state"], "inventory_running_stale")
        self.assertFalse(running["coverage_inventory_complete"])

    def test_ai_delivery_slo_reports_running_epoch_before_first_completion(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 100,
                inventory_completed_at=now - 90,
            )
            connection.execute("DELETE FROM ai_inventory_epochs")
            connection.execute(
                """
                INSERT INTO ai_inventory_epochs(
                    epoch_id, schema_version, measurement_revision,
                    policy_revision, root_signature, state,
                    started_at, updated_at, completed_at
                ) VALUES ('running-first', 1, ?, 'policy-v1', 'root-v1',
                          'running', ?, ?, 0)
                """,
                (
                    self.module.AI_DELIVERY_MEASUREMENT_REVISION,
                    now - 50,
                    now - 1,
                ),
            )

        running = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(running["coverage_inventory_state"], "inventory_running")
        self.assertEqual(running["coverage_inventory_reason"], "matching_epoch_running")
        self.assertEqual(running["coverage_inventory_epoch_id"], "running-first")
        self.assertFalse(running["coverage_inventory_complete"])
        self.assertFalse(running["coverage_complete"])

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE ai_inventory_epochs SET updated_at=? WHERE epoch_id='running-first'",
                (now - self.module.AI_INVENTORY_RUNNING_STALE_SECONDS - 1,),
            )

        stale = self.module._ai_delivery_slo_summary(now=now)
        self.assertEqual(stale["coverage_inventory_state"], "inventory_running_stale")
        self.assertEqual(
            stale["coverage_inventory_reason"],
            "matching_epoch_running_stale",
        )

    def test_ai_delivery_slo_tolerates_fractionally_future_running_heartbeat(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 100,
                inventory_completed_at=now - 90,
            )
            connection.execute("DELETE FROM ai_inventory_epochs")
            connection.execute(
                """
                INSERT INTO ai_inventory_epochs(
                    epoch_id, schema_version, measurement_revision,
                    policy_revision, root_signature, state,
                    started_at, updated_at, completed_at
                ) VALUES ('running-race', 1, ?, 'policy-v1', 'root-v1',
                          'running', ?, ?, 0)
                """,
                (
                    self.module.AI_DELIVERY_MEASUREMENT_REVISION,
                    now - 50,
                    now + self.module.AI_INVENTORY_CLOCK_SKEW_TOLERANCE_SECONDS / 2,
                ),
            )

        running = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(running["coverage_inventory_state"], "inventory_running")
        self.assertEqual(running["coverage_inventory_reason"], "matching_epoch_running")
        self.assertEqual(running["coverage_inventory_age_seconds"], 0.0)

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE ai_inventory_epochs SET updated_at=? WHERE epoch_id='running-race'",
                (now + self.module.AI_INVENTORY_CLOCK_SKEW_TOLERANCE_SECONDS + 1,),
            )

        future = self.module._ai_delivery_slo_summary(now=now)
        self.assertEqual(future["coverage_inventory_state"], "inventory_running_stale")
        self.assertEqual(future["coverage_inventory_reason"], "matching_epoch_running_stale")

    def test_ai_delivery_slo_rejects_inventory_dirtied_after_completed_epoch(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 100,
                inventory_completed_at=now - 10,
            )
            connection.execute(
                "INSERT INTO ai_delivery_meta VALUES ('inventory_dirty_at', ?, ?)",
                (str(now - 10), now - 10),
            )
            connection.execute(
                "UPDATE ai_delivery_meta SET value='1', updated_at=? "
                "WHERE key='inventory_dirty_generation'",
                (now - 5,),
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["coverage_inventory_state"], "inventory_dirty")
        self.assertFalse(summary["coverage_inventory_complete"])
        self.assertFalse(summary["coverage_complete"])
        self.assertEqual(summary["sample_state"], "coverage_incomplete")
        self.assertIsNone(summary["confidence_target_met"])

    def test_ai_delivery_slo_reports_legacy_preinstrumented_ai_as_grandfathered(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=now - 100,
                inventory_completed_at=now - 1,
            )
            connection.execute(
                """
                INSERT INTO ai_media_inventory(
                    inventory_id, epoch_id, canonical_path, media_fingerprint,
                    media_size, media_mtime_ns, policy_revision, classification,
                    disposition, requires_ledger, obligation_id
                ) VALUES ('legacy-ai', 'test-inventory', '/anime/legacy.mkv',
                          'fp-legacy', 100, 200, 'policy-v1', 'finished',
                          'legacy_preinstrumented_ai', 0, '')
                """
            )
            connection.execute(
                """
                UPDATE ai_inventory_epochs
                SET observed_count=1, classified_count=1,
                    legacy_preinstrumented_ai_count=1, coverage_complete=1
                WHERE epoch_id='test-inventory'
                """
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertTrue(summary["coverage_inventory_complete"])
        self.assertEqual(summary["coverage_inventory_legacy_grandfathered"], 1)
        self.assertEqual(summary["denominator"], 0)
        self.assertIsNone(summary["success_rate"])

    def test_ai_delivery_slo_counts_only_matured_unexcluded_obligations(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        cutoff = now - 30 * 86400
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=cutoff - 72 * 3600,
                inventory_completed_at=now,
            )
            connection.executemany(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, state, eligible_at, due_at, verified_at,
                    terminal_at, updated_at, exclusion_code, attempt_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "on-time", "succeeded", now - 1000, now - 100,
                        now - 200, now - 200, now - 200, "", 1,
                    ),
                    (
                        "late", "succeeded", now - 1000, now - 200,
                        now - 100, now - 100, now - 100, "", 1,
                    ),
                    ("overdue", "open", now - 1000, now - 300, 0, 0, now - 250, "", 1),
                    (
                        "excluded",
                        "excluded",
                        now - 1000,
                        now - 400,
                        0,
                        now - 401,
                        now - 401,
                        "official_subtitle_present_before_attempt",
                        0,
                    ),
                    (
                        "attempted-exclusion",
                        "excluded",
                        now - 1000,
                        now - 450,
                        0,
                        now - 451,
                        now - 451,
                        "official_subtitle_present_before_attempt",
                        1,
                    ),
                    (
                        "corrupt-exclusion",
                        "excluded",
                        now - 1000,
                        now - 475,
                        0,
                        0,
                        now - 476,
                        "official_subtitle_present_before_attempt",
                        0,
                    ),
                    (
                        "late-exclusion",
                        "excluded",
                        now - 1000,
                        now - 480,
                        0,
                        now - 479,
                        now - 479,
                        "official_subtitle_present_before_attempt",
                        0,
                    ),
                    (
                        "backdated-exclusion", "excluded", now - 500,
                        now - 490, 0, now - 501, now - 500,
                        "official_subtitle_present_before_attempt", 0,
                    ),
                    (
                        "post-deadline-write", "excluded", now - 1000,
                        now - 510, 0, now - 520, now - 509,
                        "official_subtitle_present_before_attempt", 0,
                    ),
                    (
                        "invalid-exclusion", "excluded", now - 1000,
                        now - 530, 0, now - 531, now - 531, "manual", 0,
                    ),
                    ("future", "open", now - 1000, now + 10, 0, 0, now, "", 1),
                    (
                        "old", "succeeded", cutoff - 1000, cutoff - 1,
                        cutoff - 2, cutoff - 2, cutoff - 2, "", 1,
                    ),
                ],
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["window_days"], 30)
        self.assertEqual(summary["target"], 0.9999)
        self.assertEqual(summary["minimum_sample"], 10_000)
        self.assertEqual(summary["sample_state"], "warming")
        self.assertIsNone(summary["target_met"])
        self.assertEqual(summary["numerator"], 0)
        self.assertEqual(summary["denominator"], 0)
        self.assertEqual(summary["misses"], 0)
        self.assertIsNone(summary["success_rate"])
        self.assertEqual(
            summary["publication_breakdown"]["translated_chinese"]["verified_on_time"],
            0,
        )
        self.assertEqual(
            summary["publication_breakdown"]["source_language"]["verified_on_time"],
            0,
        )

    def test_ai_delivery_slo_publication_breakdown_is_fail_closed(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        cutoff = now - 30 * 86400
        source_verification = json.dumps(
            {
                "publication_semantics_verified": True,
                "publication_contract": self.module.AI_DELIVERY_PUBLICATION_CONTRACT,
                "publication_kind": "source_language",
                "output_languages": ["en"],
                "expected_policy_revision": "policy-v1",
                "manifest_policy_revision": "policy-v1",
                "policy_revision_matched": True,
            }
        )
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=cutoff - 72 * 3600,
                inventory_completed_at=now,
            )
            connection.executemany(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, state, eligible_at, due_at, verified_at,
                    terminal_at, updated_at, exclusion_code, attempt_count,
                    verification_json
                ) VALUES (?, 'succeeded', ?, ?, ?, ?, ?, '', 1, ?)
                """,
                [
                    (
                        "source-en", now - 1000, now - 100,
                        now - 200, now - 200, now - 200, source_verification,
                    ),
                    (
                        "malformed", now - 1000, now - 100,
                        now - 200, now - 200, now - 200,
                        '{"publication_kind":"arbitrary_success"}',
                    ),
                ],
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["denominator"], 0)
        self.assertEqual(summary["numerator"], 0)
        self.assertEqual(summary["misses"], 0)
        breakdown = summary["publication_breakdown"]
        self.assertEqual(breakdown["translated_chinese"]["verified_on_time"], 0)
        self.assertEqual(breakdown["source_language"]["verified_on_time"], 0)
        self.assertEqual(breakdown["source_language"]["by_output_language"], {})
        self.assertEqual(breakdown["invalid_success_evidence"], 0)
        self.assertEqual(breakdown["unclassified_misses"], 0)

    def test_ai_delivery_slo_requires_ten_thousand_samples_and_meets_exactly_at_99_99_percent(self) -> None:
        database = self.module.WORK_PATH / "scanner_state.sqlite3"
        now = 2_000_000_000.0
        cutoff = now - 30 * 86400
        with sqlite3.connect(database) as connection:
            self._create_ai_delivery_slo_schema(
                connection,
                instrumented_at=cutoff - 72 * 3600,
                inventory_completed_at=now,
            )
            connection.executemany(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, state, eligible_at, due_at, verified_at,
                    terminal_at, updated_at, exclusion_code, attempt_count
                ) VALUES (?, 'succeeded', ?, ?, ?, ?, ?, '', 1)
                """,
                (
                    (
                        f"sample-{index}", now - 1000, now - 100,
                        now - 200, now - 200, now - 200,
                    )
                    for index in range(9_999)
                ),
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["denominator"], 0)
        self.assertEqual(summary["sample_state"], "warming")
        self.assertIsNone(summary["target_met"])

        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, state, eligible_at, due_at, verified_at,
                    terminal_at, updated_at, exclusion_code, attempt_count
                ) VALUES ('sample-9999', 'succeeded', ?, ?, ?, ?, ?, '', 1)
                """,
                (now - 1000, now - 100, now - 200, now - 200, now - 200),
            )
            connection.execute(
                "UPDATE ai_delivery_obligations SET state = 'open', verified_at = 0 WHERE obligation_id = 'sample-0'"
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["denominator"], 0)
        self.assertEqual(summary["numerator"], 0)
        self.assertIsNone(summary["success_rate"])
        self.assertEqual(summary["sample_state"], "warming")
        self.assertIsNone(summary["target_met"])
        self.assertEqual(summary["minimum_zero_miss_sample"], 29_956)

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE ai_delivery_obligations SET state = 'open', verified_at = 0 WHERE obligation_id = 'sample-1'"
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["numerator"], 0)
        self.assertIsNone(summary["success_rate"])
        self.assertEqual(summary["sample_state"], "warming")
        self.assertIsNone(summary["target_met"])

        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                UPDATE ai_delivery_obligations
                   SET state = 'succeeded', verified_at = ?
                 WHERE obligation_id IN ('sample-0', 'sample-1')
                """,
                (now - 200,),
            )
            connection.executemany(
                """
                INSERT INTO ai_delivery_obligations(
                    obligation_id, state, eligible_at, due_at, verified_at,
                    terminal_at, updated_at, exclusion_code, attempt_count
                ) VALUES (?, 'succeeded', ?, ?, ?, ?, ?, '', 1)
                """,
                (
                    (
                        f"sample-{index}", now - 1000, now - 100,
                        now - 200, now - 200, now - 200,
                    )
                    for index in range(10_000, 29_956)
                ),
            )

        summary = self.module._ai_delivery_slo_summary(now=now)

        self.assertEqual(summary["numerator"], 0)
        self.assertEqual(summary["denominator"], 0)
        self.assertEqual(summary["sample_state"], "warming")
        self.assertIsNone(summary["target_met"])
        self.assertIsNone(summary["confidence_lower_bound"])
        self.assertIsNone(summary["confidence_target_met"])

    def test_v2_overview_contract_stays_under_twenty_kibibytes(self) -> None:
        config = {"work_path": str(self.module.WORK_PATH), "input_path": "/anime"}
        with (
            patch.object(self.module, "_load_config", return_value=config),
            patch.object(self.module, "_fast_current_ai", return_value=None),
            patch.object(
                self.module,
                "_mikan_lite_state",
                return_value={"counts": {}, "pipeline": {}, "extract_jobs": {"counts": {}, "active": 0}},
            ),
            patch.object(self.module, "_fast_queue_counts", return_value={"queued": 7}),
            patch.object(self.module, "_eta_summary", return_value={"remaining": 7, "eta_seconds": 60}),
            patch.object(
                self.module,
                "_mikan_extract_latency_summary",
                return_value={"sample_count": 5, "p95_seconds": 12, "target_seconds": 15, "meets_target": True},
            ),
            patch.object(self.module, "list_reviews", return_value=([], 2)),
            patch.object(self.module, "_io_policy_summary", return_value={"busy": False}),
            patch.object(self.module, "_health_summary", return_value={"overall": "ok"}),
            patch.object(self.module, "_worker_summary", return_value={"running": True}),
            patch.object(self.module, "_disk_summary", return_value={"work": {"free_gb": 100}}),
            patch.object(
                self.module,
                "_resource_telemetry_summary",
                return_value={
                    "sampled_at": 123,
                    "stale": False,
                    "refreshing": False,
                    "cpu": {"available": True, "utilization_percent": 20.0},
                    "ram": {"available": True, "utilization_percent": 30.0},
                    "gpu": {"available": False, "error_code": "nvidia_smi_missing"},
                },
            ),
            patch.object(
                self.module,
                "_resource_admission_summary",
                return_value={
                    "available": True,
                    "schema": "resource-admission-state-v1",
                    "decision_id": "decision-1",
                    "allow_new_job": True,
                },
            ),
            patch.object(self.module, "_stream_state_version", return_value={"revision": 1}),
            patch.object(
                self.module,
                "_ai_delivery_slo_summary",
                return_value={
                    "sample_state": "warming",
                    "denominator": 0,
                    "target": 0.9999,
                    "minimum_sample": 10_000,
                    "target_met": None,
                    "confidence_level": 0.95,
                    "confidence_method": "clopper_pearson_exact_one_sided",
                    "confidence_lower_bound": None,
                    "confidence_target_met": None,
                    "minimum_zero_miss_sample": 29_956,
                    "coverage_active_queue_total": 7,
                    "coverage_active_queue_tracked": 7,
                    "coverage_active_queue_untracked": 0,
                    "coverage_active_queue_complete": True,
                },
            ),
        ):
            payload = self.module.v2_overview()

        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), 20 * 1024)
        self.assertEqual(payload["reviews"]["open"], 2)
        self.assertEqual(payload["eta"]["seconds"], 60)
        self.assertTrue(payload["mikan"]["extract_start_latency"]["meets_target"])
        self.assertEqual(payload["resources"]["telemetry"]["cpu"]["utilization_percent"], 20.0)
        self.assertEqual(
            payload["resources"]["telemetry"]["gpu"]["error_code"],
            "nvidia_smi_missing",
        )
        self.assertEqual(payload["resources"]["admission"]["decision_id"], "decision-1")
        self.assertIn("ai_scheduler", payload)
        self.assertEqual(payload["ai_delivery_slo"]["sample_state"], "warming")
        self.assertEqual(payload["ai_delivery_slo"]["target"], 0.9999)
        self.assertEqual(payload["ai_delivery_slo"]["minimum_sample"], 10_000)
        self.assertIsNone(payload["ai_delivery_slo"]["target_met"])
        self.assertEqual(payload["ai_delivery_slo"]["confidence_level"], 0.95)
        self.assertEqual(
            payload["ai_delivery_slo"]["confidence_method"],
            "clopper_pearson_exact_one_sided",
        )
        self.assertIsNone(payload["ai_delivery_slo"]["confidence_lower_bound"])
        self.assertIsNone(payload["ai_delivery_slo"]["confidence_target_met"])
        self.assertEqual(payload["ai_delivery_slo"]["minimum_zero_miss_sample"], 29_956)
        self.assertEqual(payload["ai_delivery_slo"]["coverage_active_queue_total"], 7)
        self.assertEqual(payload["ai_delivery_slo"]["coverage_active_queue_tracked"], 7)
        self.assertEqual(payload["ai_delivery_slo"]["coverage_active_queue_untracked"], 0)
        self.assertTrue(payload["ai_delivery_slo"]["coverage_active_queue_complete"])

    def test_v2_overview_reuses_short_review_count_cache(self) -> None:
        config = {"work_path": str(self.module.WORK_PATH)}
        with patch.object(
            self.module,
            "list_reviews",
            return_value=([], 17),
        ) as reviews:
            first = self.module._fast_open_review_count(config)
            second = self.module._fast_open_review_count(config)

        self.assertEqual(first, 17)
        self.assertEqual(second, 17)
        reviews.assert_called_once()

    def test_disk_summary_reuses_short_mount_usage_cache(self) -> None:
        usage = types.SimpleNamespace(
            free=100 * 1024**3,
            total=200 * 1024**3,
        )
        with patch.object(
            self.module.shutil,
            "disk_usage",
            return_value=usage,
        ) as disk_usage:
            first = self.module._disk_summary()
            second = self.module._disk_summary()

        self.assertEqual(first, second)
        self.assertEqual(disk_usage.call_count, 3)
        self.assertTrue(first["work"]["available"])
        self.assertEqual(first["work"]["utilization_percent"], 50.0)
        self.assertIn("sampled_at", first["work"])

    def test_gpu_telemetry_parses_metrics_and_only_exposes_process_counts(self) -> None:
        gpu_query = types.SimpleNamespace(
            returncode=0,
            stdout=(
                "0, GPU-a, NVIDIA GeForce RTX 3060, 41, 2048, 12288, 10240, 62\n"
                "1, GPU-b, NVIDIA RTX A2000, 17, 1024, 6144, 5120, 55\n"
            ),
        )
        process_query = types.SimpleNamespace(
            returncode=0,
            stdout="GPU-a, 101\nGPU-a, 102\nGPU-a, 102\nGPU-b, 201\n",
        )
        with (
            patch.object(self.module.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            patch.object(self.module, "_run_nvidia_smi", side_effect=[gpu_query, process_query]) as run,
        ):
            result = self.module._collect_gpu_telemetry()

        self.assertTrue(result["available"])
        self.assertEqual(result["aggregate"]["device_count"], 2)
        self.assertEqual(result["aggregate"]["utilization_percent"], 41.0)
        self.assertEqual(result["aggregate"]["memory_used_mib"], 3072.0)
        self.assertEqual(result["aggregate"]["memory_total_mib"], 18432.0)
        self.assertEqual(result["aggregate"]["process_count"], 3)
        self.assertEqual(result["devices"][0]["process_count"], 2)
        self.assertNotIn("_uuid", result["devices"][0])
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("\"pid\"", encoded)
        self.assertNotIn("command", encoded.casefold())
        self.assertEqual(run.call_count, 2)

    def test_gpu_telemetry_reports_missing_nvidia_smi_without_fake_zeroes(self) -> None:
        with patch.object(self.module.shutil, "which", return_value=None):
            result = self.module._collect_gpu_telemetry()

        self.assertEqual(result, {"available": False, "error_code": "nvidia_smi_missing"})
        self.assertNotIn("utilization_percent", result)
        self.assertNotIn("memory_used_mib", result)

    def test_gpu_telemetry_uses_bounded_read_only_worker_container_fallback(self) -> None:
        self.module.DOCKER_SOCKET.touch()
        gpu_query = types.SimpleNamespace(
            returncode=0,
            stdout="0, GPU-a, NVIDIA RTX 3060, 22, 3072, 12288, 9216, 48\n",
        )
        process_query = types.SimpleNamespace(returncode=0, stdout="GPU-a, 101\n")
        with (
            patch.object(self.module.shutil, "which", return_value=None),
            patch.object(
                self.module,
                "_run_worker_nvidia_smi",
                side_effect=[gpu_query, process_query],
            ) as worker_query,
        ):
            result = self.module._collect_gpu_telemetry()

        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "nvidia-smi-worker-container")
        self.assertEqual(result["aggregate"]["process_count"], 1)
        self.assertEqual(worker_query.call_count, 2)

        with patch.object(
            self.module,
            "_docker_request",
            side_effect=[{"Id": "exec-1"}, "gpu output", {"ExitCode": 0}],
        ) as docker_request:
            completed = self.module._run_worker_nvidia_smi(["--query-gpu=index"])

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "gpu output")
        create_body = docker_request.call_args_list[0].args[2]
        self.assertEqual(create_body["Cmd"], ["nvidia-smi", "--query-gpu=index"])
        for call in docker_request.call_args_list:
            self.assertEqual(
                call.kwargs["timeout_seconds"],
                self.module.NVIDIA_SMI_TIMEOUT_SECONDS,
            )

    def test_gpu_telemetry_reports_bounded_nvidia_smi_timeout(self) -> None:
        timeout = self.module.subprocess.TimeoutExpired(
            cmd=["nvidia-smi"],
            timeout=self.module.NVIDIA_SMI_TIMEOUT_SECONDS,
        )
        with (
            patch.object(self.module.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            patch.object(self.module, "_run_nvidia_smi", side_effect=timeout),
        ):
            result = self.module._collect_gpu_telemetry()

        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "nvidia_smi_timeout")
        self.assertGreaterEqual(self.module.NVIDIA_SMI_TIMEOUT_SECONDS, 0.25)
        self.assertLessEqual(self.module.NVIDIA_SMI_TIMEOUT_SECONDS, 3.0)

    def test_gpu_telemetry_marks_malformed_query_unavailable(self) -> None:
        malformed = types.SimpleNamespace(
            returncode=0,
            stdout="0, GPU-a, NVIDIA RTX 3060, not-a-number, 0, 12288, 12288, 38\n",
        )
        with (
            patch.object(self.module.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            patch.object(self.module, "_run_nvidia_smi", return_value=malformed),
        ):
            result = self.module._collect_gpu_telemetry()

        self.assertEqual(
            result,
            {"available": False, "error_code": "nvidia_smi_parse_error"},
        )

    def test_gpu_process_timeout_keeps_metrics_and_marks_count_unavailable(self) -> None:
        gpu_query = types.SimpleNamespace(
            returncode=0,
            stdout="0, GPU-a, NVIDIA RTX 3060, 0, 0, 12288, 12288, 38\n",
        )
        timeout = self.module.subprocess.TimeoutExpired(
            cmd=["nvidia-smi"],
            timeout=self.module.NVIDIA_SMI_TIMEOUT_SECONDS,
        )
        with (
            patch.object(self.module.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            patch.object(self.module, "_run_nvidia_smi", side_effect=[gpu_query, timeout]),
        ):
            result = self.module._collect_gpu_telemetry()

        self.assertTrue(result["available"])
        self.assertEqual(result["aggregate"]["utilization_percent"], 0.0)
        self.assertIsNone(result["aggregate"]["process_count"])
        self.assertIsNone(result["devices"][0]["process_count"])
        self.assertEqual(result["process_error_code"], "nvidia_smi_process_timeout")

    def test_stdlib_ram_probe_parses_proc_meminfo(self) -> None:
        meminfo = (
            "MemTotal:       16777216 kB\n"
            "MemFree:         1048576 kB\n"
            "MemAvailable:    4194304 kB\n"
            "Buffers:          524288 kB\n"
            "Cached:          2097152 kB\n"
        )
        with (
            patch.object(self.module, "psutil", None),
            patch.object(self.module.Path, "read_text", return_value=meminfo),
        ):
            result = self.module._collect_ram_telemetry()

        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "procfs")
        self.assertEqual(result["total_bytes"], 16 * 1024**3)
        self.assertEqual(result["available_bytes"], 4 * 1024**3)
        self.assertEqual(result["used_bytes"], 12 * 1024**3)
        self.assertEqual(result["utilization_percent"], 75.0)

    def test_stdlib_cpu_probe_warms_up_then_reports_interval_utilization(self) -> None:
        self.module._PROC_CPU_SAMPLE = None
        samples = [
            "cpu 100 0 100 700 100 0 0 0 0 0\n",
            "cpu 200 0 100 800 100 0 0 0 0 0\n",
        ]
        with (
            patch.object(self.module, "psutil", None),
            patch.object(self.module.Path, "read_text", side_effect=samples),
        ):
            first = self.module._collect_cpu_telemetry()
            second = self.module._collect_cpu_telemetry()

        self.assertEqual(first, {"available": False, "error_code": "cpu_warming_up"})
        self.assertTrue(second["available"])
        self.assertEqual(second["provider"], "procfs")
        self.assertEqual(second["utilization_percent"], 50.0)

    def test_cpu_and_ram_permission_failures_are_explicitly_unavailable(self) -> None:
        def denied(*_args, **_kwargs):
            raise PermissionError("denied")

        denied_psutil = types.SimpleNamespace(
            cpu_percent=denied,
            cpu_count=lambda **_kwargs: 1,
            virtual_memory=denied,
        )
        with patch.object(self.module, "psutil", denied_psutil):
            cpu = self.module._collect_cpu_telemetry()
            ram = self.module._collect_ram_telemetry()

        self.assertEqual(cpu, {"available": False, "error_code": "cpu_permission_denied"})
        self.assertEqual(ram, {"available": False, "error_code": "ram_permission_denied"})

    def test_resource_telemetry_cache_is_short_and_does_not_fork_when_fresh(self) -> None:
        sample = {
            "sampled_at": int(time.time()),
            "cpu": {"available": True, "utilization_percent": 12.5},
            "ram": {"available": True, "utilization_percent": 25.0},
            "gpu": {"available": False, "error_code": "nvidia_smi_missing"},
        }
        self.module._RESOURCE_TELEMETRY_CACHE.update(
            {"expires_at": 0.0, "refreshing": False, "value": None}
        )
        with patch.object(self.module, "_collect_resource_telemetry", return_value=sample) as collect:
            self.module._resource_telemetry_refresh_worker()
        with patch.object(self.module.threading, "Thread") as thread:
            first = self.module._resource_telemetry_summary()
            second = self.module._resource_telemetry_summary()

        self.assertEqual(first, second)
        self.assertFalse(first["stale"])
        self.assertFalse(first["refreshing"])
        collect.assert_called_once_with()
        thread.assert_not_called()
        self.assertGreaterEqual(self.module.RESOURCE_TELEMETRY_CACHE_TTL_SECONDS, 2.0)
        self.assertLessEqual(self.module.RESOURCE_TELEMETRY_CACHE_TTL_SECONDS, 5.0)

    def test_resource_telemetry_initial_probe_is_nonblocking_and_deduplicated(self) -> None:
        self.module._RESOURCE_TELEMETRY_CACHE.update(
            {"expires_at": 0.0, "refreshing": False, "value": None}
        )
        thread_instance = types.SimpleNamespace(start=lambda: None)
        with patch.object(self.module.threading, "Thread", return_value=thread_instance) as thread:
            first = self.module._resource_telemetry_summary()
            second = self.module._resource_telemetry_summary()

        self.assertEqual(first["cpu"]["error_code"], "probe_pending")
        self.assertFalse(first["cpu"]["available"])
        self.assertTrue(first["refreshing"])
        self.assertTrue(second["refreshing"])
        thread.assert_called_once()

    def test_resource_admission_summary_reads_only_fresh_strict_worker_state(self) -> None:
        now = time.time()
        state_path = self.module.WORK_PATH / "nested" / "admission.json"
        state_path.parent.mkdir()
        state_path.write_text(
            json.dumps(self.resource_admission_payload(updated_at=now)),
            encoding="utf-8",
        )

        result = self.module._resource_admission_summary(
            {
                "work_path": str(self.module.WORK_PATH),
                "resource_admission_state_path": "nested/admission.json",
            },
            now=now + 1,
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["schema"], "resource-admission-state-v1")
        self.assertEqual(result["decision_id"], "a" * 32)
        self.assertTrue(result["allow_new_job"])
        self.assertEqual(result["selected_route"]["model"], "large-v3")
        self.assertEqual(result["effective"], {
            "batch_size": 8,
            "context_max_blocks": 4,
            "context_max_chars": 4000,
            "concurrency": 1,
            "whisperx_batch_size": 8,
            "transformers_whisper_batch_size": 8,
        })
        self.assertEqual(result["retry_after_seconds"], 0.0)
        self.assertNotIn("telemetry", result)
        self.assertNotIn("last_oom", result)

    def test_resource_admission_summary_fails_closed_for_missing_stale_and_bad_state(self) -> None:
        now = time.time()
        config = {"work_path": str(self.module.WORK_PATH)}
        missing = self.module._resource_admission_summary(config, now=now)
        self.assertEqual(missing["error_code"], "state_missing")

        path = self.module.WORK_PATH / "resource_admission_state.json"
        stale_payload = self.resource_admission_payload(updated_at=now - 31)
        path.write_text(json.dumps(stale_payload), encoding="utf-8")
        stale = self.module._resource_admission_summary(config, now=now)
        self.assertFalse(stale["available"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["error_code"], "state_stale")
        self.assertNotIn("decision_id", stale)

        bad_payload = self.resource_admission_payload(updated_at=now)
        bad_payload["unexpected"] = True
        path.write_text(json.dumps(bad_payload), encoding="utf-8")
        bad = self.module._resource_admission_summary(config, now=now)
        self.assertEqual(bad["error_code"], "state_schema_mismatch")

        outside = self.module._resource_admission_summary(
            {**config, "resource_admission_state_path": "../escape.json"},
            now=now,
        )
        self.assertEqual(outside["error_code"], "state_path_invalid")

    def test_resource_admission_oom_only_state_is_valid_but_has_no_decision(self) -> None:
        now = time.time()
        payload = self.resource_admission_payload(updated_at=now)
        payload["decision"] = None
        payload["launch_plan"] = None
        (self.module.WORK_PATH / "resource_admission_state.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

        result = self.module._resource_admission_summary(
            {"work_path": str(self.module.WORK_PATH)},
            now=now,
        )

        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "state_decision_missing")

    def test_v2_overview_prioritizes_scheduler_failure_over_queue_wait(self) -> None:
        config = {"work_path": str(self.module.WORK_PATH), "input_path": "/anime"}
        with (
            patch.object(self.module, "_load_config", return_value=config),
            patch.object(self.module, "_fast_current_ai", return_value=None),
            patch.object(
                self.module,
                "_mikan_lite_state",
                return_value={"counts": {}, "pipeline": {}, "extract_jobs": {"counts": {}, "active": 0}},
            ),
            patch.object(self.module, "_fast_queue_counts", return_value={"queued": 7}),
            patch.object(self.module, "_eta_summary", return_value={"remaining": 7}),
            patch.object(self.module, "_mikan_extract_latency_summary", return_value={}),
            patch.object(self.module, "list_reviews", return_value=([], 0)),
            patch.object(self.module, "_io_policy_summary", return_value={"busy": False}),
            patch.object(
                self.module,
                "_ai_scheduler_summary",
                return_value={"state": "error", "problem": True, "reason_code": "scanner_database_disk_io"},
            ),
            patch.object(self.module, "_health_summary", return_value={"overall": "error"}),
            patch.object(self.module, "_worker_summary", return_value={"running": True}),
            patch.object(self.module, "_disk_summary", return_value={"work": {"free_gb": 100}}),
            patch.object(self.module, "_stream_state_version", return_value={"revision": 1}),
        ):
            payload = self.module.v2_overview()

        self.assertEqual(payload["bottleneck"], "ai_scheduler_error")
        self.assertTrue(payload["ai_scheduler"]["problem"])

    def test_v2_extract_latency_reports_completion_to_first_claim_p95(self) -> None:
        database = self.module.WORK_PATH / "mikan_state.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                """
                CREATE TABLE mikan_extract_jobs (
                    job_key TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    torrent_json TEXT NOT NULL
                )
                """
            )
            now = time.time()
            for index, latency in enumerate((2, 4, 6, 8, 12, 14, 18)):
                completed = now - 100 - index
                connection.execute(
                    "INSERT INTO mikan_extract_jobs(job_key, started_at, torrent_json) VALUES(?, ?, ?)",
                    (
                        f"job-{index}",
                        completed + latency,
                        json.dumps({"completion_on": completed}),
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        self.module._MIKAN_EXTRACT_LATENCY_CACHE.clear()

        summary = self.module._mikan_extract_latency_summary({"mikan_pending_path": "mikan_pending.json"})

        self.assertEqual(summary["sample_count"], 7)
        self.assertEqual(summary["p50_seconds"], 8)
        self.assertEqual(summary["p95_seconds"], 18)
        self.assertFalse(summary["meets_target"])

    def test_v2_ai_review_resolution_queues_worker_verified_remediation(self) -> None:
        anime_root = self.tmp / "anime"
        target = anime_root / "Series" / "Episode.mkv"
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "review-operation"}

            async def json(self):
                return {"action": "ai.retranslate", "target": str(target)}

        review_id = "review_" + "a" * 24
        self._write_quality_review(review_id=review_id, video=target)
        with patch.object(self.module, "_load_config", return_value=config):
            result = asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        command_file = self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json"
        command = json.loads(command_file.read_text(encoding="utf-8"))
        self.assertEqual(command["action"], "review.resolve_ai")
        self.assertEqual(command["parameters"]["review_id"], review_id)
        self.assertEqual(command["parameters"]["remediation"], "ai.retranslate")

    def _write_quality_review(
        self,
        *,
        review_id: str,
        video: Path,
        diagnosis: dict | None = None,
        status: str = "open",
    ) -> None:
        database = self.module.WORK_PATH / "control_state.sqlite3"
        payload = {"video": str(video), "reports": []}
        payload.update(diagnosis or {})
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_items (
                    review_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    diagnosis_json TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolution_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    resolved_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                INSERT INTO review_items(
                    review_id, kind, target_key, severity, summary,
                    diagnosis_json, candidates_json, status, created_at, updated_at
                ) VALUES (?, 'subtitle_quality', ?, 'error', 'Translation quality failed', ?, '[]', ?, 1, 1)
                """,
                (review_id, str(video), json.dumps(payload), status),
            )

    def test_v2_review_summary_is_compact_and_detail_deduplicates_quality_issues(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Season 1" / "Series - S01E01.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        review_id = "review_" + "3" * 24
        issue = {"code": "residual_japanese_kana", "indexes": [7], "message": "kana remains"}
        self._write_quality_review(
            review_id=review_id,
            video=video,
            diagnosis={
                "series_title": "Series",
                "reports": [
                    {"language": "zh-CN", "issues": [issue]},
                    {"language": "zh-TW", "issues": [issue]},
                ],
                "line_previews": [{
                    "index": 7,
                    "timing": "00:00:10,000 --> 00:00:12,000",
                    "source_ja": "ありがとう",
                    "output_zh": "ありがとう",
                    "issue_codes": ["residual_japanese_kana"],
                }],
                "media_file": {
                    "path": str(video),
                    "timestamp": 1234,
                    "kind": "created",
                    "size": 456,
                },
            },
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        with patch.object(self.module, "_load_config", return_value=config):
            summary = self.module.v2_review_items(view="summary", state="needs_action", limit=30)
            detail = self.module.v2_review_item_detail(review_id)

        self.assertEqual(summary["total"], 1)
        self.assertNotIn("diagnosis", summary["items"][0])
        self.assertNotIn("candidates", summary["items"][0])
        self.assertEqual(summary["items"][0]["affected_indexes"], [7])
        self.assertEqual(summary["items"][0]["media_file"], {
            "timestamp": 1234.0,
            "kind": "created",
            "size": 456,
        })
        self.assertEqual(summary["items"][0]["recommended_action"]["action"], "ai.retranslate_lines")
        reports = detail["item"]["diagnosis"]["reports"]
        self.assertEqual(sum(len(report["issues"]) for report in reports), 1)
        self.assertEqual(detail["item"]["diagnosis"]["line_previews"][0]["source_ja"], "ありがとう")

    def test_asr_prompt_echo_quality_review_recommends_retranscription(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Season 1" / "Series - S01E02.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        review_id = "review_" + "e" * 24
        self._write_quality_review(
            review_id=review_id,
            video=video,
            diagnosis={
                "reports": [{
                    "language": "ja",
                    "issues": [{
                        "code": "asr_prompt_echo",
                        "indexes": [1, 2, 3],
                        "message": "echoed transcription instruction",
                    }],
                }],
            },
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        with patch.object(self.module, "_load_config", return_value=config):
            detail = self.module.v2_review_item_detail(review_id)

        action = detail["item"]["recommended_action"]
        self.assertEqual(action["action"], "ai.retranscribe")
        self.assertIn("重新轉錄", action["label"])

    def test_failed_line_repair_with_asr_gate_recommends_retranscription(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Season 1" / "Series - S01E03.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        review_id = "review_" + "f" * 24
        self._write_quality_review(
            review_id=review_id,
            video=video,
            diagnosis={
                "reports": [{
                    "issues": [{
                        "code": "residual_japanese_kana",
                        "indexes": [9],
                        "message": "kana remains",
                    }],
                }],
            },
        )
        database = self.module.WORK_PATH / "control_state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE control_commands (
                    command_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    review_id TEXT NOT NULL DEFAULT '',
                    target TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO control_commands VALUES (?, 'review.resolve_ai', ?, ?, '{}', 'failed', '{}', ?, 10, 11, 12)",
                (
                    "cmd_" + "f" * 24,
                    review_id,
                    str(video),
                    "Japanese ASR diagnostic rejected cached source; retranscription is required",
                ),
            )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        with patch.object(self.module, "_load_config", return_value=config):
            detail = self.module.v2_review_item_detail(review_id)["item"]
            summary = self.module.v2_review_items(view="summary", state="needs_action")["items"][0]

        self.assertEqual(detail["recommended_action"]["action"], "ai.retranscribe")
        self.assertEqual(summary["recommended_action"]["action"], "ai.retranscribe")
        self.assertTrue(detail["batch_eligible"])

    def test_v2_review_processing_filter_reads_durable_command_state(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Episode.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        review_id = "review_" + "4" * 24
        self._write_quality_review(review_id=review_id, video=video)
        database = self.module.WORK_PATH / "control_state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE control_commands (
                    command_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    review_id TEXT NOT NULL DEFAULT '',
                    target TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO control_commands VALUES (?, 'review.resolve_ai', ?, ?, '{}', 'running', '{}', '', 5, 6, 0)",
                ("cmd_" + "5" * 24, review_id, str(video)),
            )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        with patch.object(self.module, "_load_config", return_value=config):
            processing = self.module.v2_review_items(view="summary", state="processing")
            needs_action = self.module.v2_review_items(view="summary", state="needs_action")

        self.assertEqual(processing["total"], 1)
        self.assertEqual(processing["items"][0]["state"], "processing")
        self.assertEqual(processing["items"][0]["action_state"]["status"], "running")
        self.assertEqual(needs_action["total"], 0)

    def test_v2_review_completed_queue_result_remains_processing(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Episode.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        review_id = "review_" + "8" * 24
        self._write_quality_review(review_id=review_id, video=video)
        database = self.module.WORK_PATH / "control_state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE review_items SET updated_at=1 WHERE review_id=?", (review_id,))
            connection.execute(
                """
                CREATE TABLE control_commands (
                    command_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    review_id TEXT NOT NULL DEFAULT '',
                    target TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO control_commands VALUES (?, 'review.resolve_ai', ?, ?, '{}', 'completed', ?, '', 5, 6, 7)",
                ("cmd_" + "8" * 24, review_id, str(video), json.dumps({"queued": True})),
            )
        queue_database = self.module.WORK_PATH / "scanner_state.sqlite3"
        with sqlite3.connect(queue_database) as connection:
            connection.execute(
                "CREATE TABLE ai_candidate_queue (path TEXT PRIMARY KEY, status TEXT, source TEXT, last_error TEXT, updated_at REAL)"
            )
            connection.execute(
                "INSERT INTO ai_candidate_queue VALUES (?, 'queued', 'auto_review_remediation', '', 8)",
                (str(video),),
            )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        with patch.object(self.module, "_load_config", return_value=config):
            processing = self.module.v2_review_items(view="summary", state="processing")
            needs_action = self.module.v2_review_items(view="summary", state="needs_action")

        self.assertEqual(processing["total"], 1)
        self.assertEqual(processing["items"][0]["state"], "processing")
        self.assertEqual(processing["items"][0]["action_state"]["status"], "queued")
        self.assertEqual(needs_action["total"], 0)
        self.assertEqual(processing["state_counts"]["processing"], 1)
        self.assertEqual(processing["state_counts"]["needs_action"], 0)

        with sqlite3.connect(queue_database) as connection:
            connection.execute(
                "UPDATE ai_candidate_queue SET status='paused', last_error='bounded repair failed', updated_at=9 WHERE path=?",
                (str(video),),
            )
        with patch.object(self.module, "_load_config", return_value=config):
            processing = self.module.v2_review_items(view="summary", state="processing")
            needs_action = self.module.v2_review_items(view="summary", state="needs_action")

        self.assertEqual(processing["total"], 0)
        self.assertEqual(needs_action["total"], 1)
        self.assertEqual(needs_action["items"][0]["state"], "needs_action")
        self.assertEqual(needs_action["items"][0]["action_state"]["status"], "failed")
        self.assertEqual(needs_action["items"][0]["action_state"]["error"], "bounded repair failed")
        self.assertEqual(needs_action["state_counts"]["processing"], 0)
        self.assertEqual(needs_action["state_counts"]["needs_action"], 1)

    def test_v2_open_review_ignores_terminal_command_from_before_reopen(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Episode.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        review_id = "review_" + "5" * 24
        self._write_quality_review(review_id=review_id, video=video)
        database = self.module.WORK_PATH / "control_state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE review_items SET updated_at=100 WHERE review_id=?", (review_id,))
            connection.execute(
                """
                CREATE TABLE control_commands (
                    command_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    review_id TEXT NOT NULL DEFAULT '',
                    target TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO control_commands VALUES (?, 'review.resolve_ai', ?, ?, '{}', 'completed', '{}', '', 10, 20, 50)",
                ("cmd_" + "6" * 24, review_id, str(video)),
            )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        with patch.object(self.module, "_load_config", return_value=config):
            reopened = self.module.v2_review_item_detail(review_id)["item"]

        self.assertEqual(reopened["state"], "needs_action")
        self.assertEqual(reopened["action_state"]["status"], "idle")
        self.assertEqual(reopened["action_state"]["command_id"], "")

        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO control_commands VALUES (?, 'review.resolve_ai', ?, ?, '{}', 'failed', '{}', 'retry failed', 120, 130, 150)",
                ("cmd_" + "7" * 24, review_id, str(video)),
            )

        with patch.object(self.module, "_load_config", return_value=config):
            current = self.module.v2_review_item_detail(review_id)["item"]

        self.assertEqual(current["state"], "needs_action")
        self.assertEqual(current["action_state"]["status"], "failed")
        self.assertEqual(current["action_state"]["error"], "retry failed")

    def test_v2_batch_review_only_queues_safe_items(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Episode.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        quality_id = "review_" + "6" * 24
        ambiguous_id = "review_" + "7" * 24
        self._write_quality_review(
            review_id=quality_id,
            video=video,
            diagnosis={"reports": [{"issues": [{"code": "prompt_leak", "indexes": [3]}]}]},
        )
        self._write_target_review(review_id=ambiguous_id, candidates=[])
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "safe-batch-review"}

            async def json(self):
                return {"review_ids": [quality_id, ambiguous_id], "action": "safe.default"}

        with patch.object(self.module, "_load_config", return_value=config):
            result = asyncio.run(self.module.v2_batch_resolve_review_items(Request()))

        self.assertEqual(result["queued_count"], 1)
        self.assertEqual(result["queued"][0]["review_id"], quality_id)
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(result["rejected"][0]["review_id"], ambiguous_id)
        command_path = self.module.WORK_PATH / "control_inbox" / f"{result['queued'][0]['command_id']}.json"
        command = json.loads(command_path.read_text(encoding="utf-8"))
        self.assertEqual(command["parameters"]["remediation"], "ai.retranslate_lines")
        self.assertEqual(command["parameters"]["lines"], "3")

    def test_v2_batch_review_preflights_mixed_actions_without_partial_queue(self) -> None:
        anime_root = self.tmp / "anime"
        translate_video = anime_root / "Series" / "Translate.mkv"
        retranscribe_video = anime_root / "Series" / "Retranscribe.mkv"
        translate_video.parent.mkdir(parents=True)
        translate_video.write_bytes(b"")
        retranscribe_video.write_bytes(b"")
        translate_id = "review_" + "a" * 24
        retranscribe_id = "review_" + "b" * 24
        self._write_quality_review(
            review_id=translate_id,
            video=translate_video,
            diagnosis={"reports": [{"issues": [{"code": "residual_japanese_kana", "indexes": [3]}]}]},
        )
        self._write_quality_review(
            review_id=retranscribe_id,
            video=retranscribe_video,
            diagnosis={"reports": [{"issues": [{"code": "asr_prompt_echo", "indexes": [1]}]}]},
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "mixed-batch-review"}

            async def json(self):
                return {
                    "review_ids": [translate_id, retranscribe_id],
                    "action": "safe.default",
                }

        with patch.object(self.module, "_load_config", return_value=config):
            result = asyncio.run(self.module.v2_batch_resolve_review_items(Request()))

        self.assertEqual(result["queued_count"], 0)
        self.assertEqual(result["rejected_count"], 2)
        inbox = self.module.WORK_PATH / "control_inbox"
        self.assertEqual(list(inbox.glob("*.json")) if inbox.exists() else [], [])

    def test_v2_batch_review_uses_failed_asr_gate_remediation(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Needs Retranscription.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        review_id = "review_" + "c" * 24
        self._write_quality_review(
            review_id=review_id,
            video=video,
            diagnosis={
                "reports": [{
                    "issues": [{
                        "code": "residual_japanese_kana",
                        "indexes": [7],
                    }],
                }],
            },
        )
        database = self.module.WORK_PATH / "control_state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE control_commands (
                    command_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    review_id TEXT NOT NULL DEFAULT '',
                    target TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO control_commands VALUES (?, 'review.resolve_ai', ?, ?, '{}', 'failed', '{}', ?, 10, 11, 12)",
                (
                    "cmd_" + "c" * 24,
                    review_id,
                    str(video),
                    "Japanese ASR diagnostic rejected cached source; retranscription is required",
                ),
            )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "failed-asr-batch-review"}

            async def json(self):
                return {"review_ids": [review_id], "action": "ai.retranscribe"}

        with patch.object(self.module, "_load_config", return_value=config):
            result = asyncio.run(self.module.v2_batch_resolve_review_items(Request()))

        self.assertEqual(result["queued_count"], 1)
        command_path = self.module.WORK_PATH / "control_inbox" / f"{result['queued'][0]['command_id']}.json"
        command = json.loads(command_path.read_text(encoding="utf-8"))
        self.assertEqual(command["parameters"]["remediation"], "ai.retranscribe")

    def test_v2_line_repair_rejects_indexes_not_reported_by_review(self) -> None:
        anime_root = self.tmp / "anime"
        video = anime_root / "Series" / "Episode.mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"")
        review_id = "review_" + "8" * 24
        self._write_quality_review(
            review_id=review_id,
            video=video,
            diagnosis={"reports": [{"issues": [{"code": "prompt_leak", "indexes": [3]}]}]},
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "reject-unreported-line"}

            async def json(self):
                return {"action": "ai.retranslate_lines", "target": str(video), "indexes": [999]}

        with patch.object(self.module, "_load_config", return_value=config):
            with self.assertRaises(self.module.HTTPException) as raised:
                asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("reported", raised.exception.detail)

    def _write_target_review(
        self,
        *,
        review_id: str,
        candidates: list[dict],
        status: str = "open",
        diagnosis: dict | None = None,
    ) -> None:
        database = self.module.WORK_PATH / "control_state.sqlite3"
        diagnosis_payload = {"bangumi_ids": [2402], "torrent_hash": "a" * 40}
        diagnosis_payload.update(diagnosis or {})
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_items (
                    review_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    diagnosis_json TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolution_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    resolved_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                INSERT INTO review_items(
                    review_id, kind, target_key, severity, summary,
                    diagnosis_json, candidates_json, status, created_at, updated_at
                ) VALUES (?, 'target_ambiguity', 'torrent:1', 'error', 'Choose target', ?, ?, ?, 1, 1)
                """,
                (
                    review_id,
                    json.dumps(diagnosis_payload),
                    json.dumps(candidates),
                    status,
                ),
            )

    def test_v2_review_items_collapses_legacy_duplicate_torrent_hash_cards(self) -> None:
        anime_root = self.tmp / "anime"
        season_two = anime_root / "Bofuri" / "Season 2" / "Bofuri - S02E01.mkv"
        season_two.parent.mkdir(parents=True)
        season_two.write_bytes(b"")
        self._write_target_review(
            review_id="review_" + "1" * 24,
            candidates=[],
        )
        self._write_target_review(
            review_id="review_" + "2" * 24,
            candidates=[{
                "path": str(season_two),
                "season": 2,
                "score": 1661,
                "reasons": ["episode", "title_contains"],
            }],
        )

        listing = self.module.v2_review_items(limit=20)

        self.assertEqual(listing["total"], 1)
        self.assertEqual(len(listing["items"]), 1)
        self.assertEqual(listing["items"][0]["duplicate_count"], 2)
        self.assertEqual(listing["items"][0]["candidates"][0]["season"], 2)

    def test_v2_target_review_summary_exposes_source_lifecycle_without_full_diagnosis(self) -> None:
        review_id = "review_" + "8" * 24
        self._write_target_review(
            review_id=review_id,
            candidates=[],
            diagnosis={
                "source_lifecycle": "redownload_available",
                "source_torrent_in_qbit": False,
                "source_files_present": False,
                "source_redownload_available": True,
            },
        )

        listing = self.module.v2_review_items(view="summary", state="needs_action")
        detail = self.module.v2_review_item_detail(review_id)

        summary = listing["items"][0]
        self.assertNotIn("diagnosis", summary)
        self.assertEqual(summary["source_lifecycle"], "redownload_available")
        self.assertFalse(summary["source_torrent_in_qbit"])
        self.assertTrue(summary["source_redownload_available"])
        self.assertEqual(
            detail["item"]["diagnosis"]["source_lifecycle"],
            "redownload_available",
        )

    def test_v2_target_review_uses_exact_stored_candidate_and_derives_season(self) -> None:
        anime_root = self.tmp / "anime"
        candidate = anime_root / "Non Non Biyori" / "Season 3" / "Non Non Biyori - S03E01.mkv"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"")
        review_id = "review_" + "b" * 24
        self._write_target_review(
            review_id=review_id,
            candidates=[{
                "path": str(candidate),
                "season": 3,
                "score": 1661,
                "reasons": ["episode", "title_contains"],
                "file_info": {
                    "path": str(candidate),
                    "timestamp": 2345,
                    "kind": "modified",
                    "size": 789,
                },
            }],
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "resolve-nonstop-season-3"}

            async def json(self):
                return {"candidate_path": str(candidate), "source_id": 2402}

        with patch.object(self.module, "_load_config", return_value=config):
            detail = self.module.v2_review_item_detail(review_id)
            result = asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        command_file = self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json"
        command = json.loads(command_file.read_text(encoding="utf-8"))
        self.assertEqual(command["action"], "review.resolve_target")
        self.assertEqual(command["parameters"]["candidate_path"], str(candidate))
        self.assertEqual(command["parameters"]["series_path"], str(anime_root / "Non Non Biyori"))
        self.assertEqual(command["parameters"]["season"], 3)
        self.assertEqual(command["parameters"]["source_id"], "2402")
        self.assertEqual(detail["item"]["candidates"][0]["file_info"], {
            "timestamp": 2345.0,
            "kind": "modified",
            "size": 789,
        })

    def test_v2_target_review_deduplicates_identical_candidate_paths(self) -> None:
        anime_root = self.tmp / "anime"
        candidate = anime_root / "Series" / "Season 1" / "Series - S01E01.mkv"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"")
        review_id = "review_" + "9" * 24
        self._write_target_review(
            review_id=review_id,
            candidates=[
                {
                    "path": str(candidate),
                    "score": 1200,
                    "margin": 120,
                    "reasons": ["episode", "series_mapping:pending"],
                },
                {
                    "path": str(candidate),
                    "score": 1300,
                    "margin": 180,
                    "reasons": ["episode", "title_exact"],
                },
            ],
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        with patch.object(self.module, "_load_config", return_value=config):
            detail = self.module.v2_review_item_detail(review_id)["item"]

        self.assertEqual(len(detail["candidates"]), 1)
        self.assertEqual(detail["candidates"][0]["score"], 1300)
        self.assertEqual(
            detail["candidates"][0]["reasons"],
            ["episode", "series_mapping:pending", "title_exact"],
        )
        self.assertTrue(detail["batch_eligible"])

    def test_review_candidate_dedupe_does_not_synthesize_score_and_margin(self) -> None:
        path = "/anime/Series/Season 1/Series - S01E01.mkv"
        candidates = self.module._deduplicate_review_candidates([
            {
                "path": path,
                "score": 1200,
                "margin": 0,
                "reasons": ["series_mapping:pending"],
            },
            {
                "path": path,
                "score": 0,
                "margin": 120,
                "reasons": ["title_exact"],
            },
        ])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["score"], 1200)
        self.assertEqual(candidates[0]["margin"], 0)

    def test_review_candidate_dedupe_preserves_unselectable_fail_closed(self) -> None:
        path = "/anime/Series/Season 1/Series - S01E01.mkv"
        candidates = self.module._deduplicate_review_candidates([
            {"path": path, "score": 1300, "margin": 180, "reasons": ["title_exact"]},
            {"path": path, "score": 1200, "margin": 0, "reasons": ["release_year_conflict"], "selectable": False},
        ])

        self.assertIs(candidates[0]["selectable"], False)
        self.assertFalse(self.module._review_candidate_has_semantic_evidence(candidates[0]))

    def test_review_candidate_dedupe_keeps_posix_case_distinct(self) -> None:
        candidates = self.module._deduplicate_review_candidates([
            {"path": "/anime/Foo/Season 1/Foo.mkv", "reasons": ["title_exact"]},
            {"path": "/anime/foo/Season 1/Foo.mkv", "reasons": ["title_exact"]},
        ])

        self.assertEqual(len(candidates), 2)

    def test_v2_target_review_can_enqueue_idempotent_dismiss(self) -> None:
        review_id = "review_" + "c" * 24
        self._write_target_review(review_id=review_id, candidates=[])
        config = {"input_path": "/anime", "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "dismiss-source-review"}

            async def json(self):
                return {"action": "review.dismiss"}

        with patch.object(self.module, "_load_config", return_value=config):
            first = asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))
            second = asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        self.assertEqual(first["command_id"], second["command_id"])
        command_files = list((self.module.WORK_PATH / "control_inbox").glob("*.json"))
        self.assertEqual(len(command_files), 1)
        command = json.loads(command_files[0].read_text(encoding="utf-8"))
        self.assertEqual(command["action"], "review.dismiss")
        self.assertEqual(command["target"], review_id)
        self.assertEqual(command["parameters"], {"review_id": review_id})

    def test_v2_quality_review_cannot_be_dismissed(self) -> None:
        anime_root = self.tmp / "anime"
        target = anime_root / "Series" / "Episode.mkv"
        review_id = "review_" + "d" * 24
        self._write_quality_review(review_id=review_id, video=target)
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "reject-quality-dismiss"}

            async def json(self):
                return {"action": "review.dismiss"}

        with patch.object(self.module, "_load_config", return_value=config):
            with self.assertRaises(self.module.HTTPException) as raised:
                asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("source pairing", raised.exception.detail)
        self.assertFalse((self.module.WORK_PATH / "control_inbox").exists())

    def test_v2_dismissed_review_summary_exposes_handled_reason(self) -> None:
        review_id = "review_" + "e" * 24
        self._write_target_review(review_id=review_id, candidates=[], status="resolved")
        database = self.module.WORK_PATH / "control_state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE review_items SET resolution_json=? WHERE review_id=?",
                (json.dumps({"dismissed": True, "suppress_reopen": True}), review_id),
            )

        listing = self.module.v2_review_items(state="resolved", view="summary")

        self.assertEqual(listing["total"], 1)
        self.assertTrue(listing["items"][0]["dismissed"])

    def test_v2_target_review_detail_backfills_source_and_qbit_times(self) -> None:
        review_id = "review_" + "d" * 24
        self._write_target_review(review_id=review_id, candidates=[])
        state_db = self.module.WORK_PATH / "mikan_state.sqlite3"
        with sqlite3.connect(state_db) as connection:
            connection.execute(
                """
                CREATE TABLE mikan_extract_jobs (
                    torrent_hash TEXT,
                    torrent_name TEXT,
                    torrent_json TEXT,
                    pending_entries_json TEXT,
                    updated_at REAL
                )
                """
            )
            connection.execute(
                "INSERT INTO mikan_extract_jobs VALUES (?, ?, ?, ?, ?)",
                (
                    "a" * 40,
                    "[Group] Test - 09",
                    json.dumps({
                        "creation_date": 1_699_999_900,
                        "added_on": 1_700_000_100,
                        "completion_on": 1_700_000_900,
                    }),
                    json.dumps([{
                        "source": "mikan",
                        "pub_date": "2023-11-14T22:13:20+00:00",
                    }]),
                    1_700_001_000,
                ),
            )

        detail = self.module.v2_review_item_detail(review_id)
        diagnosis = detail["item"]["diagnosis"]

        self.assertEqual(diagnosis["source_published_at"], 1_700_000_000.0)
        self.assertEqual(diagnosis["source_published_precision"], "time")
        self.assertEqual(diagnosis["torrent_created_at"], 1_699_999_900.0)
        self.assertEqual(diagnosis["torrent_added_at"], 1_700_000_100.0)
        self.assertEqual(diagnosis["torrent_completed_at"], 1_700_000_900.0)
        self.assertTrue(diagnosis["source_timing_available"])

    def test_v2_target_review_detail_backfills_torrent_creation_from_qbit(self) -> None:
        review_id = "review_" + "e" * 24
        self._write_target_review(review_id=review_id, candidates=[])
        with patch.object(
            self.module,
            "_qbit_torrent_creation_timestamp",
            return_value=1_699_999_900.0,
        ) as creation_time:
            detail = self.module.v2_review_item_detail(review_id)

        self.assertEqual(detail["item"]["diagnosis"]["torrent_created_at"], 1_699_999_900.0)
        creation_time.assert_called_once_with(ANY, "a" * 40)

    def test_review_source_publication_rejects_qbit_recovered_synthetic_date(self) -> None:
        timestamp = self.module._review_source_publication_timestamp([{
            "source": "qbit-recovered",
            "pub_date": "2023-11-14T22:28:20+00:00",
        }])

        self.assertEqual(timestamp, 0.0)

    def test_review_source_publication_recovers_mikan_url_date(self) -> None:
        timestamp = self.module._review_source_publication_timestamp([{
            "source": "mikan",
            "torrent_url": "https://mikanani.me/Download/20250913/abcdef.torrent",
            "pub_date": "",
        }])

        self.assertEqual(
            timestamp,
            datetime(2025, 9, 13, tzinfo=timezone.utc).timestamp(),
        )
        self.assertEqual(
            self.module._review_source_publication_precision([{
                "source": "mikan",
                "torrent_url": "https://mikanani.me/Download/20250913/abcdef.torrent",
                "pub_date": "",
            }]),
            "date",
        )

    def test_qbit_torrent_creation_timestamp_reads_general_properties(self) -> None:
        class FakeResponse:
            def __init__(self, body: bytes) -> None:
                self.body = body
                self.status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self, _limit: int = -1) -> bytes:
                return self.body

        class FakeOpener:
            def __init__(self) -> None:
                self.responses = [
                    FakeResponse(b"Ok."),
                    FakeResponse(b'{"creation_date":1700000000}'),
                ]

            def open(self, *_args, **_kwargs):
                return self.responses.pop(0)

        config = {
            "qbit_base_url": "http://qbit:8080",
            "qbit_username": "user",
            "qbit_password": "pass",
            "qbit_timeout_seconds": 3,
        }
        self.module._QBIT_TORRENT_TIME_CACHE.clear()
        with patch.object(self.module.urllib_request, "build_opener", return_value=FakeOpener()):
            timestamp = self.module._qbit_torrent_creation_timestamp(config, "a" * 40)

        self.assertEqual(timestamp, 1_700_000_000.0)

    def test_target_review_date_guidance_recommends_only_unique_close_candidate(self) -> None:
        item = {
            "kind": "target_ambiguity",
            "status": "open",
            "diagnosis": {
                "torrent_name": "[Group] Example - 09 [WebRip 1080p]",
                "source_published_at": 1_733_754_600,
            },
            "candidates": [
                {
                    "path": "/anime/Example/Season 3/Example - S03E09.mkv",
                    "reasons": ["title_contains"],
                    "file_info": {"timestamp": 1_733_754_600, "kind": "modified", "size": 1},
                },
                {
                    "path": "/anime/Example/Season 2/Example - S02E09.mkv",
                    "reasons": ["title_contains"],
                    "file_info": {"timestamp": 1_646_922_600, "kind": "modified", "size": 1},
                },
            ],
        }

        guided = self.module._apply_target_review_date_guidance(item)

        self.assertTrue(guided["candidates"][0]["date_recommended"])
        self.assertEqual(guided["candidates"][0]["source_date_distance_days"], 0.0)
        self.assertFalse(guided["diagnosis"].get("candidate_date_conflict", False))

    def test_target_review_date_guidance_blocks_all_clearly_old_candidates(self) -> None:
        item = {
            "kind": "target_ambiguity",
            "status": "open",
            "diagnosis": {
                "torrent_name": "[Group] Ave Mujica - 09 [WebRip 1080p]",
                "source_published_at": 1_741_302_341,
            },
            "candidates": [
                {
                    "path": "/anime/BanG Dream/Season 3/BanG Dream - S03E09.mkv",
                    "reasons": ["title_contains"],
                    "file_info": {"timestamp": 1_583_850_301, "kind": "modified", "size": 1},
                },
                {
                    "path": "/anime/BanG Dream/Season 2/BanG Dream - S02E09.mkv",
                    "reasons": ["title_contains"],
                    "file_info": {"timestamp": 1_551_362_401, "kind": "modified", "size": 1},
                },
            ],
        }

        guided = self.module._apply_target_review_date_guidance(item)

        self.assertTrue(guided["diagnosis"]["candidate_date_conflict"])
        self.assertEqual(guided["diagnosis"]["source_release_year"], 2025)
        self.assertTrue(all(candidate["selectable"] is False for candidate in guided["candidates"]))
        self.assertFalse(guided["batch_eligible"])

    def test_target_review_date_guidance_does_not_reject_late_bluray_release(self) -> None:
        item = {
            "kind": "target_ambiguity",
            "status": "open",
            "diagnosis": {
                "torrent_name": "[Group] Archive Show - 09 [BDRip 1080p]",
                "source_published_at": 1_741_302_341,
            },
            "candidates": [{
                "path": "/anime/Archive Show/Season 1/Archive Show - S01E09.mkv",
                "reasons": ["title_contains"],
                "file_info": {"timestamp": 1_583_850_301, "kind": "modified", "size": 1},
            }],
        }

        guided = self.module._apply_target_review_date_guidance(item)

        self.assertFalse(guided["diagnosis"].get("candidate_date_conflict", False))
        self.assertNotIn("selectable", guided["candidates"][0])

    def test_v2_target_review_resolution_rejects_date_blocked_candidate(self) -> None:
        anime_root = self.tmp / "anime"
        candidate = anime_root / "BanG Dream" / "Season 3" / "BanG Dream - S03E09.mkv"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"")
        review_id = "review_" + "e" * 24
        self._write_target_review(
            review_id=review_id,
            candidates=[{
                "path": str(candidate),
                "season": 3,
                "reasons": ["title_contains"],
                "file_info": {"timestamp": 1_583_850_301, "kind": "modified", "size": 1},
            }],
        )
        with sqlite3.connect(self.module.WORK_PATH / "control_state.sqlite3") as connection:
            connection.execute(
                "UPDATE review_items SET diagnosis_json=? WHERE review_id=?",
                (
                    json.dumps({
                        "bangumi_ids": [2402],
                        "torrent_hash": "e" * 40,
                        "torrent_name": "[Group] Ave Mujica - 09 [WebRip 1080p]",
                        "source_published_at": 1_741_302_341,
                    }),
                    review_id,
                ),
            )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "reject-date-blocked-candidate"}

            async def json(self):
                return {"candidate_path": str(candidate), "source_id": 2402}

        with patch.object(self.module, "_load_config", return_value=config):
            with self.assertRaises(self.module.HTTPException) as raised:
                asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("exact candidate", raised.exception.detail)

    def test_v2_target_review_rejects_path_not_stored_in_candidates(self) -> None:
        anime_root = self.tmp / "anime"
        candidate = anime_root / "Non Non Biyori" / "Season 3" / "Non Non Biyori - S03E01.mkv"
        forged = anime_root / "Other Series" / "Season 3" / "Other - S03E01.mkv"
        candidate.parent.mkdir(parents=True)
        forged.parent.mkdir(parents=True)
        candidate.write_bytes(b"")
        forged.write_bytes(b"")
        review_id = "review_" + "c" * 24
        self._write_target_review(
            review_id=review_id,
            candidates=[{"path": str(candidate), "season": 3, "reasons": ["title_contains"]}],
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "reject-forged-review-target"}

            async def json(self):
                return {"candidate_path": str(forged), "source_id": 2402}

        with patch.object(self.module, "_load_config", return_value=config):
            with self.assertRaises(self.module.HTTPException) as raised:
                asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("exact candidate", raised.exception.detail)
        self.assertFalse((self.module.WORK_PATH / "control_inbox").exists())

    def test_v2_target_review_legacy_payload_must_identify_one_candidate(self) -> None:
        anime_root = self.tmp / "anime"
        series = anime_root / "Non Non Biyori"
        candidate = series / "Season 3" / "Non Non Biyori - S03E01.mkv"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"")
        review_id = "review_" + "d" * 24
        self._write_target_review(
            review_id=review_id,
            candidates=[{"path": str(candidate), "season": 3, "reasons": ["title_exact"]}],
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "legacy-exact-review-target"}

            async def json(self):
                return {"series_path": str(series), "season": 3, "source_id": 2402}

        with patch.object(self.module, "_load_config", return_value=config):
            result = asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        command_file = self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json"
        command = json.loads(command_file.read_text(encoding="utf-8"))
        self.assertEqual(command["parameters"]["candidate_path"], str(candidate))

    def test_v2_target_review_blocks_candidates_without_title_evidence(self) -> None:
        anime_root = self.tmp / "anime"
        wrong = anime_root / "No-Rin" / "Season 1" / "No-Rin - S01E01.mkv"
        wrong.parent.mkdir(parents=True)
        wrong.write_bytes(b"")
        review_id = "review_" + "e" * 24
        self._write_target_review(
            review_id=review_id,
            candidates=[{"path": str(wrong), "season": 1, "score": 1000, "reasons": ["episode"]}],
        )
        config = {"input_path": str(anime_root), "work_path": str(self.module.WORK_PATH)}

        with patch.object(self.module, "_load_config", return_value=config):
            listing = self.module.v2_review_items(limit=20)

            class Request:
                headers = {"idempotency-key": "reject-no-title-evidence"}

                async def json(self):
                    return {"candidate_path": str(wrong), "source_id": 2402}

            with self.assertRaises(self.module.HTTPException) as raised:
                asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        self.assertEqual(listing["items"][0]["candidates"], [])
        self.assertEqual(listing["items"][0]["diagnosis"]["rejected_candidate_count"], 1)
        self.assertEqual(listing["items"][0]["problem"]["code"], "target_ambiguity")
        self.assertTrue(listing["items"][0]["problem"]["requires_user_action"])
        self.assertEqual(raised.exception.status_code, 400)

    def test_v2_target_review_can_request_worker_candidate_rebuild(self) -> None:
        review_id = "review_" + "f" * 24
        self._write_target_review(review_id=review_id, candidates=[])
        config = {"input_path": "/anime", "work_path": str(self.module.WORK_PATH)}
        series_id = "series_" + "1" * 24

        class Request:
            headers = {"idempotency-key": "rebuild-bofuri-season-2"}

            async def json(self):
                return {
                    "action": "target.rebuild_candidates",
                    "series_id": series_id,
                    "season": 2,
                }

        with patch.object(self.module, "_load_config", return_value=config):
            result = asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        command_file = self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json"
        command = json.loads(command_file.read_text(encoding="utf-8"))
        self.assertEqual(command["action"], "review.rebuild_target_candidates")
        self.assertEqual(command["target"], review_id)
        self.assertEqual(command["parameters"]["series_id"], series_id)
        self.assertEqual(command["parameters"]["season"], 2)
        self.assertNotIn("candidate_path", command["parameters"])

    def test_v2_target_review_can_request_automatic_safe_candidate_rebuild(self) -> None:
        review_id = "review_" + "a" * 24
        self._write_target_review(review_id=review_id, candidates=[])
        config = {"input_path": "/anime", "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "auto-rebuild-bofuri"}

            async def json(self):
                return {"action": "target.auto_rebuild_candidates"}

        with patch.object(self.module, "_load_config", return_value=config):
            result = asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        command_file = self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json"
        command = json.loads(command_file.read_text(encoding="utf-8"))
        self.assertEqual(command["action"], "review.auto_rebuild_target_candidates")
        self.assertEqual(command["target"], review_id)
        self.assertEqual(command["parameters"], {"review_id": review_id})

    def test_v2_target_review_rebuild_rejects_unstable_series_id(self) -> None:
        review_id = "review_" + "0" * 24
        self._write_target_review(review_id=review_id, candidates=[])
        config = {"input_path": "/anime", "work_path": str(self.module.WORK_PATH)}

        class Request:
            headers = {"idempotency-key": "reject-arbitrary-series-path"}

            async def json(self):
                return {
                    "action": "target.rebuild_candidates",
                    "series_id": "/anime/Bofuri",
                    "season": 2,
                }

        with patch.object(self.module, "_load_config", return_value=config):
            with self.assertRaises(self.module.HTTPException) as raised:
                asyncio.run(self.module.v2_resolve_review_item(review_id, Request()))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("stable series_id", raised.exception.detail)

    def test_retranslate_lines_queue_action_uses_worker_mailbox(self) -> None:
        class Request:
            headers = {"idempotency-key": "targeted-line-retranslation"}

            async def json(self):
                return {"path": "/anime/Example/Season 1/Episode.mkv", "lines": "2,5-8"}

        result = asyncio.run(self.module.queue_action("retranslate-lines", Request()))

        self.assertTrue(result["started"])
        command_path = self.module.WORK_PATH / "control_inbox" / f"{result['command_id']}.json"
        command = json.loads(command_path.read_text(encoding="utf-8"))
        self.assertEqual(command["action"], "ai.retranslate_lines")
        self.assertEqual(command["parameters"]["lines"], "2,5-8")

    def test_retry_all_failures_uses_short_timeout(self) -> None:
        self.assertEqual(
            self.module._background_action_timeout_seconds("retry-all-failures"),
            self.module.SHORT_ACTION_EXEC_TIMEOUT_SECONDS,
        )

    def test_mikan_process_completed_action_is_registered(self) -> None:
        command = self.module.ACTION_COMMANDS["mikan-process-completed"]
        self.assertIn("--mikan-process-completed", command)

    def test_ai_refresh_queue_state_action_is_registered(self) -> None:
        command = self.module.ACTION_COMMANDS["ai-refresh-queue-state"]
        self.assertIn("--refresh-ai-queue-state", command)

    def test_short_mikan_actions_use_short_exec_timeout(self) -> None:
        self.assertEqual(
            self.module._background_action_timeout_seconds("mikan-process-completed"),
            self.module.SHORT_ACTION_EXEC_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            self.module._background_action_timeout_seconds("mikan-requeue-failed-extracts"),
            self.module.SHORT_ACTION_EXEC_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            self.module._background_action_timeout_seconds("mikan-redownload-all"),
            self.module.DOCKER_EXEC_TIMEOUT_SECONDS,
        )

    def test_ai_refresh_queue_state_uses_long_exec_timeout(self) -> None:
        self.assertEqual(
            self.module._background_action_timeout_seconds("ai-refresh-queue-state"),
            self.module.DOCKER_EXEC_TIMEOUT_SECONDS,
        )

    def test_action_snapshot_hides_stale_finished_result(self) -> None:
        original_state = dict(self.module.ACTION_STATE)
        try:
            self.module.ACTION_STATE.update(
                {
                    "running": False,
                    "action": "ai-refresh-queue-state",
                    "started_at": 100.0,
                    "finished_at": 200.0,
                    "ok": False,
                    "output": "",
                    "error": "old failure",
                }
            )
            with patch.object(self.module.time, "time", return_value=200.0 + self.module.ACTION_RESULT_DISPLAY_SECONDS + 1):
                snapshot = self.module._action_snapshot()

            self.assertIsNone(snapshot["action"])
            self.assertEqual(snapshot["error"], "")
            self.assertIsNone(snapshot["ok"])
        finally:
            self.module.ACTION_STATE.clear()
            self.module.ACTION_STATE.update(original_state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
