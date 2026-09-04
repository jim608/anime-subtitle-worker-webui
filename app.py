from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import posixpath
from typing import Any
import base64
import copy
import csv
import hashlib
import http.cookiejar
import json
import math
import os
import re
import secrets
import shutil
import socket
import sqlite3
import statistics
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

try:
    import psutil
except ImportError:  # pragma: no cover - optional, /proc is used on Linux
    psutil = None  # type: ignore[assignment]

from control_api import (
    CommandConflictError,
    configured_path,
    enqueue_atomic_command,
    list_reviews,
    read_auto_remediation_status,
    read_command,
    read_review,
    review_active_queue_targets,
    review_autopilot_revision_attempts_allowed,
    review_command_states,
    review_queue_states,
    review_state_counts,
    stable_id,
)

try:
    from fastapi.middleware.gzip import GZipMiddleware
except ImportError:  # pragma: no cover - lightweight unit-test FastAPI stub
    GZipMiddleware = None  # type: ignore[assignment]

try:
    from fastapi.responses import StreamingResponse
except ImportError:  # pragma: no cover - compatibility with older FastAPI/test stubs
    try:
        from starlette.responses import StreamingResponse
    except ImportError:  # pragma: no cover
        class StreamingResponse:  # type: ignore[no-redef]
            def __init__(self, content: Any, media_type: str | None = None, headers: dict[str, str] | None = None) -> None:
                self.content = content
                self.media_type = media_type
                self.headers = headers or {}


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/config/config.yaml"))
WORK_PATH = Path(os.environ.get("WORK_PATH", "/work"))
LOG_PATH = Path(os.environ.get("LOG_PATH", "/logs"))
DOCKER_SOCKET = Path(os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock"))
WORKER_CONTAINER_NAME = os.environ.get("WORKER_CONTAINER_NAME", "anime-subtitle-worker")
WEBUI_CONTAINER_NAME = os.environ.get("WEBUI_CONTAINER_NAME", "anime-subtitle-worker-webui")
SCANNER_RECOVERY_REQUEST_NAME = "scanner_state_recovery_required.json"
SCANNER_RECOVERY_HELPER_NAME = "anime-subtitle-scanner-recovery"
SCANNER_RECOVERY_POLL_SECONDS = 2.0
QUEUE_STALE_RUNNING_SECONDS = int(os.environ.get("QUEUE_STALE_RUNNING_SECONDS", "21600"))
DOCKER_API_TIMEOUT_SECONDS = 10.0
DOCKER_RESTART_STOP_TIMEOUT_SECONDS = 10
DOCKER_EXEC_TIMEOUT_SECONDS = 7200.0
SHORT_ACTION_EXEC_TIMEOUT_SECONDS = 1800.0
ACTION_RESULT_DISPLAY_SECONDS = 900.0
SQLITE_BUSY_TIMEOUT_SECONDS = 10.0
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")
_MIKAN_DOWNLOADS_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_MIKAN_SQLITE_DOWNLOADS_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_MIKAN_LITE_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_MIKAN_EXTRACT_JOBS_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_WORKER_SUMMARY_CACHE: dict[str, Any] = {}
_LIBRARY_SUMMARY_CACHE: dict[tuple[int, str], Any] = {}
_EVENTS_SUMMARY_CACHE: dict[tuple[Any, ...], Any] = {}
_ETA_SUMMARY_CACHE: dict[str, Any] = {}
_MIKAN_EXTRACT_LATENCY_CACHE: dict[str, Any] = {}
_FAST_QUEUE_COUNTS_CACHE: dict[str, Any] = {}
_AI_DELIVERY_SLO_SUMMARY_CACHE: dict[str, Any] = {}
_DATABASE_HEALTH_CACHE: dict[str, Any] = {}
_VERSION_SUMMARY_CACHE: dict[str, Any] = {}
_HEALTH_SUMMARY_CACHE: dict[str, Any] = {}
_OPEN_REVIEW_COUNT_CACHE: dict[str, Any] = {}
_DISK_SUMMARY_CACHE: dict[str, Any] = {}
_REVIEW_AUTOMATION_COUNTS_CACHE: dict[tuple[str, int, str], dict[str, Any]] = {}
_REVIEW_AUTOMATION_COUNTS_CACHE_LOCK = threading.Lock()
_AI_QUALITY_REVIEW_AUTOPILOT_PREFIX = (
    "review-autopilot:asr-full-retranscribe-v1:review.resolve_ai:"
)
_AI_QUALITY_REVIEW_AUTOPILOT_MAX_ATTEMPTS = 3
_RESOURCE_TELEMETRY_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "refreshing": False,
    "value": None,
}
_RESOURCE_TELEMETRY_CACHE_LOCK = threading.Lock()
_COMPLETED_DELIVERY_STATUS_CACHE: dict[str, dict[str, Any]] = {}
_COMPLETED_DELIVERY_STATUS_CACHE_LOCK = threading.Lock()
_PROC_CPU_SAMPLE: tuple[float, float] | None = None
_PROC_CPU_SAMPLE_LOCK = threading.Lock()
_SUBTITLE_QUALITY_CACHE: dict[str, Any] = {}
_AI_COMPLETION_TIME_CACHE: dict[str, Any] = {}
_CONFIG_CACHE: dict[str, Any] = {"signature": None, "value": None}
_CONFIG_CACHE_LOCK = threading.Lock()
_QBIT_TORRENT_TIME_CACHE: dict[str, dict[str, float]] = {}
_QBIT_TORRENT_TIME_CACHE_LOCK = threading.Lock()
SUMMARY_CACHE_TTL_SECONDS = 2.0
FAST_QUEUE_COUNTS_CACHE_TTL_SECONDS = 1.0
AI_DELIVERY_SLO_SUMMARY_CACHE_TTL_SECONDS = 2.0
RESOURCE_TELEMETRY_CACHE_TTL_SECONDS = 3.0
RESOURCE_OVERVIEW_MAX_AGE_SECONDS = 45.0
RESOURCE_ADMISSION_STATE_SCHEMA = "resource-admission-state-v1"
RESOURCE_ADMISSION_STATE_NAME = "resource_admission_state.json"
RESOURCE_ADMISSION_STATE_MAX_BYTES = 128 * 1024
RESOURCE_ADMISSION_MAX_AGE_LIMIT_SECONDS = 300.0
NVIDIA_SMI_TIMEOUT_SECONDS = 1.5
DATABASE_HEALTH_CACHE_TTL_SECONDS = 60.0
FAST_WORKER_SUMMARY_CACHE_TTL_SECONDS = 15.0
SQLITE_DOWNLOADS_CACHE_TTL_SECONDS = 3.0
SUBTITLE_FILE_PROBE_CACHE_TTL_SECONDS = 60.0
REVIEW_AUTOMATION_COUNTS_CACHE_TTL_SECONDS = 5.0
AI_CONTROL_NAME = "ai_control.json"
AI_SCHEDULER_STATE_NAME = "ai_scheduler_state.json"
DEPLOYMENT_HOLD_NAME = "deployment_hold.json"
MIKAN_REDOWNLOAD_CANCEL_NAME = "mikan_redownload_all.cancel.json"
MIKAN_REDOWNLOAD_ACTIVE_STALE_SECONDS = 900.0
AI_DELIVERY_SLO_WINDOW_DAYS = 30
AI_DELIVERY_SLO_TARGET = 0.9999
AI_DELIVERY_DEADLINE_SECONDS = 72 * 60 * 60
AI_DELIVERY_SLO_MINIMUM_SAMPLE = 10000
AI_DELIVERY_MEASUREMENT_REVISION = (
    "ai-delivery-99.99-strict-traditional-chinese-source-priority-full-inventory-continuous-anytime-eprocess-v5"
)
AI_DELIVERY_PUBLICATION_CONTRACT = "ai-publication-semantics-v2"
AI_INVENTORY_SCHEMA_VERSION = 1
AI_INVENTORY_MAX_AGE_SECONDS = 28_800
AI_INVENTORY_RUNNING_STALE_SECONDS = 7_200
AI_INVENTORY_CLOCK_SKEW_TOLERANCE_SECONDS = 1.0
AI_DELIVERY_ANYTIME_ALPHA = 0.05
AI_DELIVERY_ANYTIME_LOG_THRESHOLD = math.log(1.0 / AI_DELIVERY_ANYTIME_ALPHA)
AI_DELIVERY_ANYTIME_BETTING_FRACTIONS = (0.5, 0.9)
AI_DELIVERY_ANYTIME_METHOD = "two_strategy_fixed_betting_eprocess_cs_v1"
AI_DELIVERY_DUE_TOLERANCE_SECONDS = 1e-6
AI_DELIVERY_TRANSLATED_LANGUAGES = ("ja", "zh-CN", "zh-TW")
AI_DELIVERY_TRADITIONAL_CHINESE_LANGUAGES = ("zh-TW",)
AI_DELIVERY_TRADITIONAL_CHINESE_PUBLICATION_KINDS = frozenset(
    {"translated_trilingual", "adopted_zh_tw", "converted_zh_cn"}
)
COMPLETED_DELIVERY_SCHEMA_VERSION = 1
COMPLETED_DELIVERY_CONTRACT = "completed-mkv-delivery-v1"
COMPLETED_DELIVERY_RECEIPT_MAX_BYTES = 1024 * 1024
AI_DELIVERY_SLO_CONFIDENCE_LEVEL = 0.95
AI_DELIVERY_SLO_CONFIDENCE_METHOD = "clopper_pearson_exact_one_sided"
AI_DELIVERY_EXCLUSION_CODES = (
    "official_subtitle_present_before_attempt",
    "local_chinese_subtitle_present_before_attempt",
    "embedded_chinese_subtitle_present_before_attempt",
    "standalone_theme_policy",
    "unsupported_media_before_attempt",
    "media_missing_before_attempt",
    "superseded_before_attempt",
)

try:
    DOCKER_API_TIMEOUT_SECONDS = max(
        1.0,
        float(os.environ.get("DOCKER_API_TIMEOUT_SECONDS", DOCKER_API_TIMEOUT_SECONDS)),
    )
except (TypeError, ValueError):
    pass

try:
    DOCKER_EXEC_TIMEOUT_SECONDS = max(
        DOCKER_API_TIMEOUT_SECONDS,
        float(os.environ.get("DOCKER_EXEC_TIMEOUT_SECONDS", DOCKER_EXEC_TIMEOUT_SECONDS)),
    )
except (TypeError, ValueError):
    pass

try:
    SHORT_ACTION_EXEC_TIMEOUT_SECONDS = max(
        DOCKER_API_TIMEOUT_SECONDS,
        float(os.environ.get("SHORT_ACTION_EXEC_TIMEOUT_SECONDS", SHORT_ACTION_EXEC_TIMEOUT_SECONDS)),
    )
except (TypeError, ValueError):
    pass

try:
    ACTION_RESULT_DISPLAY_SECONDS = max(
        0.0,
        float(os.environ.get("ACTION_RESULT_DISPLAY_SECONDS", ACTION_RESULT_DISPLAY_SECONDS)),
    )
except (TypeError, ValueError):
    pass

try:
    SQLITE_BUSY_TIMEOUT_SECONDS = max(
        1.0,
        float(os.environ.get("WEBUI_SQLITE_BUSY_TIMEOUT_SECONDS", SQLITE_BUSY_TIMEOUT_SECONDS)),
    )
except (TypeError, ValueError):
    pass

try:
    NVIDIA_SMI_TIMEOUT_SECONDS = min(
        3.0,
        max(
            0.25,
            float(os.environ.get("NVIDIA_SMI_TIMEOUT_SECONDS", NVIDIA_SMI_TIMEOUT_SECONDS)),
        ),
    )
except (TypeError, ValueError):
    pass

EDITABLE_FIELDS: dict[str, dict[str, Any]] = {
    "translator_model": {"type": "str", "label": "Translator model"},
    "transcription_backend": {
        "type": "select",
        "options": ["faster-whisper", "whisperx", "vibevoice", "transformers-whisper"],
        "label": "Default transcription backend",
    },
    "japanese_transcription_backend": {
        "type": "select",
        "options": ["faster-whisper", "whisperx", "vibevoice", "transformers-whisper"],
        "label": "Japanese transcription backend",
    },
    "japanese_transcription_model": {"type": "str", "label": "Japanese ASR model"},
    "non_japanese_transcription_backend": {
        "type": "select",
        "options": ["faster-whisper", "whisperx", "vibevoice", "transformers-whisper"],
        "label": "Non-Japanese transcription backend",
    },
    "non_japanese_transcription_model": {"type": "str", "label": "Non-Japanese ASR model"},
    "transformers_whisper_chunk_length_s": {
        "type": "float",
        "min": 1,
        "label": "Transformers Whisper chunk seconds",
    },
    "transformers_whisper_batch_size": {"type": "int", "min": 1, "label": "Transformers Whisper batch size"},
    "transformers_whisper_torch_dtype": {
        "type": "select",
        "options": ["auto", "float16", "bfloat16", "float32"],
        "label": "Transformers Whisper dtype",
    },
    "transformers_whisper_trust_remote_code": {
        "type": "bool",
        "label": "Transformers Whisper trust remote code",
    },
    "transformers_whisper_punctuator": {
        "type": "bool",
        "label": "Transformers Whisper punctuator",
    },
    "transformers_whisper_attn_implementation": {
        "type": "select",
        "options": ["", "sdpa", "flash_attention_2", "eager"],
        "label": "Transformers Whisper attention",
    },
    "transformers_whisper_task": {
        "type": "select",
        "options": ["auto", "automatic-speech-recognition", "kotoba-whisper"],
        "label": "Transformers Whisper task",
    },
    "transformers_whisper_stable_ts": {
        "type": "bool",
        "label": "Transformers Whisper stable timestamps",
    },
    "max_concurrent_videos": {"type": "int", "min": 1, "label": "AI concurrent videos"},
    "auto_ai_max_videos_per_cycle": {"type": "int", "min": 0, "label": "AI batch limit"},
    "require_ai_subtitles": {"type": "bool", "label": "Require AI subtitles"},
    "auto_enable_ai_fallback": {"type": "bool", "label": "Enable AI fallback"},
    "auto_mikan_parallel_with_ai": {"type": "bool", "label": "Run Mikan with AI"},
    "auto_ai_run_before_mikan": {"type": "bool", "label": "Run AI before Mikan"},
    "auto_ai_max_attempts": {"type": "int", "min": 0, "label": "AI automatic retry limit"},
    "ai_queue_running_stale_seconds": {"type": "int", "min": 60, "label": "AI stale running seconds"},
    "ai_queue_stage_stale_seconds": {"type": "int", "min": 60, "label": "AI stage stale seconds"},
    "batch_size": {"type": "int", "min": 1, "label": "Translation batch size"},
    "max_retries": {"type": "int", "min": 1, "label": "Translation retries"},
    "translation_request_hard_timeout_seconds": {"type": "int", "min": 1, "label": "Translation hard timeout seconds"},
    "translation_context_enabled": {"type": "bool", "label": "Enable episode translation context"},
    "translation_context_retry_without_context": {"type": "bool", "label": "Retry translation without context"},
    "translation_context_auto_disable": {"type": "bool", "label": "Auto-disable bad translation context"},
    "translation_context_fast_retry_without_context_on_format_error": {
        "type": "bool",
        "label": "Fast retry without context on format error",
    },
    "translation_context_retry_without_context_on_timeout": {
        "type": "bool",
        "label": "Retry without context on timeout",
    },
    "translation_split_batch_on_timeout": {
        "type": "bool",
        "label": "Split translation batch on timeout",
    },
    "translation_split_batch_on_format_error": {
        "type": "bool",
        "label": "Split malformed translation batches immediately",
    },
    "translation_context_max_blocks": {"type": "int", "min": 1, "label": "Context source blocks"},
    "translation_context_max_chars": {"type": "int", "min": 1000, "label": "Context source chars"},
    "translation_context_max_output_chars": {"type": "int", "min": 200, "label": "Context output chars"},
    "translation_metadata_context_enabled": {"type": "bool", "label": "Enable metadata translation context"},
    "metadata_context_max_chars": {"type": "int", "min": 200, "label": "Metadata context chars"},
    "series_metadata_match_min_confidence": {"type": "float", "min": 0, "max": 1, "label": "Series match minimum confidence"},
    "series_metadata_auto_seed_terms": {"type": "bool", "label": "Seed series terminology from metadata"},
    "series_metadata_sync_enabled": {"type": "bool", "label": "Synchronize existing series indexes"},
    "series_metadata_sync_interval_seconds": {"type": "int", "min": 300, "label": "Series index sync interval"},
    "series_metadata_sync_startup_delay_seconds": {"type": "int", "min": 0, "label": "Series index sync startup delay"},
    "series_metadata_enrich_enabled": {"type": "bool", "label": "Background AniList enrichment"},
    "series_metadata_enrich_per_cycle": {"type": "int", "min": 0, "label": "Series enrichment batch size"},
    "series_metadata_enrich_delay_seconds": {"type": "float", "min": 0, "label": "Series enrichment request delay"},
    "notification_webhook_url": {"type": "str", "label": "Failure notification webhook URL"},
    "notification_min_interval_seconds": {"type": "int", "min": 0, "label": "Notification deduplication interval"},
    "storage_io_pressure_enabled": {"type": "bool", "label": "Adaptive storage I/O pressure control"},
    "storage_io_pressure_some_avg10_threshold": {"type": "float", "min": 0, "label": "I/O pressure some avg10 threshold"},
    "storage_io_pressure_full_avg10_threshold": {"type": "float", "min": 0, "label": "I/O pressure full avg10 threshold"},
    "storage_io_pressure_backoff_seconds": {"type": "float", "min": 0, "label": "Library scan I/O backoff"},
    "whisper_model_cache_enabled": {"type": "bool", "label": "Reuse Whisper model within each video"},
    "language_detect_sample_count": {"type": "int", "min": 1, "label": "Language detection samples"},
    "audio_content_probe_enabled": {"type": "bool", "label": "Probe audio stream language by content"},
    "audio_content_probe_max_streams": {"type": "int", "min": 1, "label": "Maximum audio streams to probe"},
    "asr_selective_retry_enabled": {"type": "bool", "label": "Repair only low-confidence ASR ranges"},
    "state_backup_enabled": {"type": "bool", "label": "Enable scheduled state backups"},
    "state_backup_interval_hours": {"type": "int", "min": 1, "label": "State backup interval hours"},
    "state_backup_retention_count": {"type": "int", "min": 1, "label": "State backup retention count"},
    "database_maintenance_enabled": {"type": "bool", "label": "Enable idle database maintenance"},
    "database_maintenance_interval_hours": {"type": "int", "min": 1, "label": "Database maintenance interval hours"},
    "database_maintenance_startup_delay_seconds": {"type": "int", "min": 0, "label": "Database maintenance startup delay"},
    "database_maintenance_min_reclaim_mib": {"type": "float", "min": 0, "label": "Database maintenance minimum reclaim MiB"},
    "database_maintenance_min_freelist_ratio": {"type": "float", "min": 0, "max": 1, "label": "Database maintenance minimum reclaim ratio"},
    "scanner_candidate_min_age_seconds": {"type": "int", "min": 0, "label": "New file settle seconds"},
    "scanner_skip_standalone_op_ed": {"type": "bool", "label": "Skip standalone OP/ED videos"},
    "watch_interval_seconds": {"type": "int", "min": 1, "label": "AI watch interval"},
    "scanner_background_scan_interval_seconds": {"type": "int", "min": 60, "label": "Library reconciliation interval"},
    "scanner_background_scan_startup_delay_seconds": {"type": "int", "min": 0, "label": "Library scan startup delay"},
    "scanner_walk_yield_every_entries": {"type": "int", "min": 1, "label": "Library scan I/O yield entries"},
    "scanner_walk_yield_seconds": {"type": "float", "min": 0, "label": "Library scan I/O yield seconds"},
    "scanner_queue_oldest_every_n_cycles": {"type": "int", "min": 0, "label": "Oldest AI queue fairness interval"},
    "mikan_watch_interval_seconds": {"type": "int", "min": 1, "label": "Mikan watch interval"},
    "mikan_completed_poll_interval_seconds": {"type": "int", "min": 1, "label": "Mikan completed poll interval"},
    "mikan_extract_workers": {"type": "int", "min": 1, "label": "Subtitle extraction workers"},
    "mikan_extract_workers_during_ai": {"type": "int", "min": 1, "label": "Subtitle extraction workers during AI"},
    "mikan_extract_job_timeout_seconds": {"type": "int", "min": 60, "label": "Subtitle extraction timeout seconds"},
    "mikan_extract_lease_seconds": {"type": "int", "min": 60, "label": "Subtitle extraction lease seconds"},
    "mikan_episode_index_ttl_seconds": {"type": "int", "min": 60, "label": "Mikan episode index refresh interval"},
    "mikan_operation_lock_wait_seconds": {"type": "int", "min": 0, "label": "Mikan lock wait seconds"},
    "mikan_no_candidate_retry_seconds": {"type": "int", "min": 1, "label": "Mikan no-source retry"},
    "mikan_no_candidate_retry_max_seconds": {"type": "int", "min": 1, "label": "Mikan no-source retry maximum"},
    "mikan_download_start_timeout_seconds": {"type": "int", "min": 1, "label": "Mikan download start grace"},
    "mikan_download_metadata_timeout_seconds": {"type": "int", "min": 1, "label": "Mikan metadata grace"},
    "mikan_download_unhealthy_timeout_seconds": {"type": "int", "min": 1, "label": "Mikan unhealthy grace"},
    "mikan_download_stall_timeout_seconds": {"type": "int", "min": 1, "label": "Mikan download stall timeout"},
    "mikan_extract_job_timeout_per_video_seconds": {"type": "int", "min": 30, "label": "Collection extract timeout per video"},
    "mikan_extract_job_timeout_max_seconds": {"type": "int", "min": 60, "label": "Maximum collection extract timeout"},
    "mikan_extract_timeout_retry_seconds": {"type": "int", "min": 1, "label": "Timed-out extract resume delay"},
    "mikan_enabled": {"type": "bool", "label": "Enable Mikan"},
    "mikan_extract_completed": {"type": "bool", "label": "Extract completed Mikan downloads"},
    "ass_primary_font_size": {"type": "int", "min": 1, "label": "ASS primary font size"},
    "ass_secondary_font_size": {"type": "int", "min": 1, "label": "ASS secondary font size"},
    "ass_margin_v": {"type": "int", "min": 0, "label": "ASS bottom margin"},
    "ass_primary_outline": {"type": "float", "min": 0, "label": "ASS primary outline"},
    "ass_secondary_outline": {"type": "float", "min": 0, "label": "ASS secondary outline"},
    "disk_min_free_gb": {"type": "float", "min": 0, "label": "Minimum free disk GB"},
    "safety_check_enabled": {"type": "bool", "label": "Enable startup safety checks"},
}

ACTION_COMMANDS: dict[str, list[str]] = {
    "refresh-ass": ["python", "/app/main.py", "--config", "/app/config.yaml", "--refresh-ass"],
    "cleanup-generated": ["python", "/app/main.py", "--config", "/app/config.yaml", "--cleanup-generated-artifacts"],
    "ai-refresh-queue-state": ["python", "/app/main.py", "--config", "/app/config.yaml", "--refresh-ai-queue-state"],
    "mikan-process-completed": ["python", "/app/main.py", "--config", "/app/config.yaml", "--mikan-process-completed"],
    "mikan-reset-all": ["python", "/app/main.py", "--config", "/app/config.yaml", "--mikan-reset-all"],
    "mikan-redownload-all": ["python", "/app/main.py", "--config", "/app/config.yaml", "--mikan-redownload-all"],
    "mikan-requeue-failed-extracts": ["python", "/app/main.py", "--config", "/app/config.yaml", "--mikan-requeue-failed-extracts"],
    "backup-state": ["python", "/app/backup_state.py", "--config", "/app/config.yaml"],
    "database-maintenance": [
        "python", "/app/database_maintenance.py", "--config", "/app/config.yaml",
        "--apply", "--wait-seconds", "900",
    ],
    "series-sync": ["python", "/app/series_metadata_sync.py", "--config", "/app/config.yaml"],
}
CONFIG_PRESETS: dict[str, dict[str, Any]] = {
    "stable": {
        "max_concurrent_videos": 1,
        "auto_ai_max_videos_per_cycle": 1,
        "auto_ai_max_attempts": 3,
        "scanner_candidate_min_age_seconds": 120,
        "scanner_skip_standalone_op_ed": True,
        "watch_interval_seconds": 60,
        "scanner_background_scan_interval_seconds": 21600,
        "scanner_background_scan_startup_delay_seconds": 600,
        "scanner_walk_yield_every_entries": 256,
        "scanner_walk_yield_seconds": 0.025,
        "scanner_queue_oldest_every_n_cycles": 12,
        "database_maintenance_enabled": True,
        "mikan_extract_workers": 2,
        "mikan_extract_workers_during_ai": 1,
        "mikan_episode_index_ttl_seconds": 21600,
        "auto_mikan_parallel_with_ai": True,
        "ai_queue_running_stale_seconds": 21600,
        "ai_queue_stage_stale_seconds": 900,
        "batch_size": 6,
        "translation_request_hard_timeout_seconds": 120,
        "translation_context_retry_without_context": True,
        "translation_context_auto_disable": True,
        "translation_context_fast_retry_without_context_on_format_error": True,
        "translation_context_retry_without_context_on_timeout": True,
        "translation_split_batch_on_timeout": True,
        "translation_split_batch_on_format_error": True,
        "translation_context_max_blocks": 40,
        "translation_context_max_chars": 3000,
        "translation_context_max_output_chars": 800,
        "metadata_context_max_chars": 800,
    },
    "fast": {
        "max_concurrent_videos": 2,
        "auto_ai_max_videos_per_cycle": 0,
        "auto_ai_max_attempts": 3,
        "scanner_candidate_min_age_seconds": 60,
        "scanner_skip_standalone_op_ed": True,
        "scanner_queue_oldest_every_n_cycles": 12,
        "database_maintenance_enabled": True,
        "watch_interval_seconds": 30,
        "scanner_background_scan_interval_seconds": 21600,
        "scanner_background_scan_startup_delay_seconds": 600,
        "scanner_walk_yield_every_entries": 256,
        "scanner_walk_yield_seconds": 0.025,
        "mikan_extract_workers": 2,
        "mikan_extract_workers_during_ai": 1,
        "mikan_episode_index_ttl_seconds": 21600,
        "auto_mikan_parallel_with_ai": True,
        "ai_queue_running_stale_seconds": 14400,
        "ai_queue_stage_stale_seconds": 900,
        "batch_size": 6,
        "translation_request_hard_timeout_seconds": 120,
        "translation_context_retry_without_context": True,
        "translation_context_auto_disable": True,
        "translation_context_fast_retry_without_context_on_format_error": True,
        "translation_context_retry_without_context_on_timeout": True,
        "translation_split_batch_on_timeout": True,
        "translation_split_batch_on_format_error": True,
        "translation_context_max_blocks": 40,
        "translation_context_max_chars": 3000,
        "translation_context_max_output_chars": 800,
        "metadata_context_max_chars": 800,
    },
    "full-library": {
        "max_concurrent_videos": 1,
        "auto_ai_max_videos_per_cycle": 0,
        "require_ai_subtitles": True,
        "auto_enable_ai_fallback": True,
        "auto_mikan_parallel_with_ai": True,
        "scanner_candidate_min_age_seconds": 120,
        "scanner_skip_standalone_op_ed": True,
        "watch_interval_seconds": 30,
        "scanner_background_scan_interval_seconds": 21600,
        "scanner_background_scan_startup_delay_seconds": 600,
        "scanner_walk_yield_every_entries": 256,
        "scanner_walk_yield_seconds": 0.025,
        "mikan_extract_workers": 2,
        "mikan_extract_workers_during_ai": 1,
        "mikan_episode_index_ttl_seconds": 21600,
        "ai_queue_running_stale_seconds": 21600,
        "ai_queue_stage_stale_seconds": 900,
        "batch_size": 6,
        "translation_request_hard_timeout_seconds": 120,
        "translation_context_retry_without_context": True,
        "translation_context_auto_disable": True,
        "translation_context_fast_retry_without_context_on_format_error": True,
        "translation_context_retry_without_context_on_timeout": True,
        "translation_split_batch_on_timeout": True,
        "translation_split_batch_on_format_error": True,
        "translation_context_max_blocks": 40,
        "translation_context_max_chars": 3000,
        "translation_context_max_output_chars": 800,
        "metadata_context_max_chars": 800,
    },
}
ACTION_LOCK = threading.Lock()
ACTION_STATE: dict[str, Any] = {
    "running": False,
    "action": None,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "output": "",
    "error": "",
}

app = FastAPI(title="Anime Subtitle Worker WebUI")
if GZipMiddleware is not None and hasattr(app, "add_middleware"):
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

_SCANNER_RECOVERY_WATCH_LOCK = threading.Lock()
_SCANNER_RECOVERY_WATCH_STARTED = False


def _read_scanner_recovery_request() -> dict[str, Any] | None:
    path = WORK_PATH / SCANNER_RECOVERY_REQUEST_NAME
    try:
        if path.stat().st_size > 64 * 1024:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _launch_scanner_recovery_helper(request: dict[str, Any]) -> bool:
    if str(request.get("status") or "") not in {
        "pending",
        "helper_started",
        "backup_verified",
        "restoring",
    }:
        return False
    recovery_id = str(request.get("recovery_id") or "")
    hold_path = WORK_PATH / "deployment_hold.json"
    try:
        hold = json.loads(hold_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(hold, dict)
        or not bool(hold.get("active"))
        or str(hold.get("deployment_id") or "") != recovery_id
        or str(hold.get("reason") or "") != "scanner-state-corruption"
    ):
        return False

    helper_path = f"/containers/{urllib_parse.quote(SCANNER_RECOVERY_HELPER_NAME, safe='')}"
    try:
        helper = _docker_request("GET", helper_path + "/json", timeout_seconds=3)
    except HTTPException as exc:
        if "Docker API error 404" not in str(exc.detail):
            raise
        helper = None
    if isinstance(helper, dict):
        state = helper.get("State") if isinstance(helper.get("State"), dict) else {}
        if bool(state.get("Running")):
            return True
        _docker_request(
            "DELETE",
            helper_path + "?force=1",
            parse_json=False,
            timeout_seconds=5,
        )

    worker = _docker_request(
        "GET",
        f"/containers/{urllib_parse.quote(WORKER_CONTAINER_NAME, safe='')}/json",
        timeout_seconds=5,
    )
    worker_config = worker.get("Config") if isinstance(worker, dict) else {}
    image = str(worker_config.get("Image") or "") if isinstance(worker_config, dict) else ""
    if not image:
        raise RuntimeError("Worker image is unavailable for scanner recovery")
    create_path = (
        "/containers/create?name="
        + urllib_parse.quote(SCANNER_RECOVERY_HELPER_NAME, safe="")
    )
    created = _docker_request(
        "POST",
        create_path,
        body={
            "Image": image,
            "Cmd": [
                "python",
                "/app/scanner_state_auto_recovery.py",
                "--work-root",
                "/work",
                "--worker-container",
                WORKER_CONTAINER_NAME,
                "--webui-container",
                WEBUI_CONTAINER_NAME,
            ],
            "Labels": {
                "anime-subtitle-worker.role": "scanner-state-auto-recovery",
                "anime-subtitle-worker.recovery-id": recovery_id,
            },
            "HostConfig": {
                "AutoRemove": True,
                "Binds": [f"{DOCKER_SOCKET}:{DOCKER_SOCKET}"],
                "VolumesFrom": [f"{WORKER_CONTAINER_NAME}:rw"],
            },
        },
        timeout_seconds=10,
    )
    container_id = str(created.get("Id") or "") if isinstance(created, dict) else ""
    if not container_id:
        raise RuntimeError("Docker did not return a scanner recovery helper id")
    _docker_request(
        "POST",
        f"/containers/{urllib_parse.quote(container_id, safe='')}/start",
        timeout_seconds=10,
    )
    return True


def _scanner_recovery_watch_loop() -> None:
    while True:
        request = _read_scanner_recovery_request()
        if request is not None:
            try:
                _launch_scanner_recovery_helper(request)
            except Exception:
                pass
        time.sleep(SCANNER_RECOVERY_POLL_SECONDS)


def _start_scanner_recovery_watch() -> None:
    global _SCANNER_RECOVERY_WATCH_STARTED
    with _SCANNER_RECOVERY_WATCH_LOCK:
        if _SCANNER_RECOVERY_WATCH_STARTED:
            return
        _SCANNER_RECOVERY_WATCH_STARTED = True
    threading.Thread(
        target=_scanner_recovery_watch_loop,
        name="scanner-state-recovery-watch",
        daemon=True,
    ).start()


if hasattr(app, "on_event"):
    app.on_event("startup")(_start_scanner_recovery_watch)

CSRF_NONCE = secrets.token_urlsafe(32)
V2_COMMAND_ACTIONS = {
    "system.health_probe",
    "system.ai_scheduler_retry",
    "system.ai_failed_retry_sweep",
    "ai.retry",
    "ai.force",
    "ai.pause",
    "ai.skip",
    "ai.prioritize",
    "ai.recover",
    "ai.retranslate",
    "ai.retranslate_lines",
    "ai.retranscribe",
    "ai.canary_once",
    "system.ai_queue_pause",
    "system.ai_queue_resume",
    "system.retry_all_failures",
    "mikan.requeue_failed_extracts",
    "mikan.requeue_extract",
    "mikan.cancel_extract",
    "mikan.process_completed",
    "mikan.request_reset_all",
    "mikan.request_redownload_all",
    "mikan.cancel_redownload",
    "system.ai_retry_all_failures",
    "system.refresh_ass",
    "system.cleanup_generated",
    "system.refresh_ai_queue_state",
    "system.backup_state",
    "system.database_maintenance",
    "series.sync",
    "series.lock",
    "series.match",
    "series.glossary_upsert",
    "series.glossary_delete",
    "review.dismiss",
    "review.resolve_ai",
    "review.resolve_target",
}

AI_CANARY_ONCE_CLIENT_PARAMETERS = frozenset({
    "expected_failure_revision",
    "expected_failure_code",
    "expected_media_mtime_ns",
})
AI_CANARY_ONCE_SERVER_PARAMETERS = frozenset({
    "campaign_key",
    "max_items",
    "max_in_flight",
    "max_consecutive_failures",
    "strategy_version",
    "target_path",
})
AI_CANARY_ONCE_FAILURE_CODES = frozenset({
    "transient_oom",
    "transient_timeout",
    "transient_connection",
    "translation_safe_omission",
})

LEGACY_QUEUE_COMMAND_ACTIONS = {
    "priority": "ai.prioritize",
    "force-ai": "ai.force",
    "recover-running": "ai.recover",
    "retry": "ai.retry",
    "clear-failure": "ai.retry",
    "pause": "ai.pause",
    "skip": "ai.skip",
    "retranslate": "ai.retranslate",
    "retranslate-lines": "ai.retranslate_lines",
    "retranscribe": "ai.retranscribe",
}

LEGACY_BACKGROUND_COMMAND_ACTIONS = {
    "ai-requeue-failed": "system.ai_retry_all_failures",
    "retry-all-failures": "system.retry_all_failures",
    "refresh-ass": "system.refresh_ass",
    "cleanup-generated": "system.cleanup_generated",
    "ai-refresh-queue-state": "system.refresh_ai_queue_state",
    "mikan-process-completed": "mikan.process_completed",
    "mikan-reset-all": "mikan.request_reset_all",
    "mikan-redownload-all": "mikan.request_redownload_all",
    "mikan-requeue-failed-extracts": "mikan.requeue_failed_extracts",
    "backup-state": "system.backup_state",
    "database-maintenance": "system.database_maintenance",
    "series-sync": "series.sync",
}


def _frontend_static_dir() -> Path:
    dist_dir = APP_DIR / "dist"
    if (dist_dir / "index.html").exists():
        return dist_dir
    return STATIC_DIR


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    password = os.environ.get("WEBUI_PASSWORD", "")
    if not password:
        return await call_next(request)

    username = os.environ.get("WEBUI_USERNAME", "admin")
    auth_header = request.headers.get("authorization", "")
    expected = "Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    if not secrets.compare_digest(auth_header, expected):
        return PlainTextResponse(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="anime-subtitle-worker-webui"'},
        )
    return await call_next(request)


@app.middleware("http")
async def v2_request_security(request: Request, call_next):
    method = str(getattr(request, "method", "GET") or "GET").upper()
    url = getattr(request, "url", None)
    path = str(getattr(url, "path", "") or "")
    headers = getattr(request, "headers", {})
    if path.startswith("/api/v2/") and method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = str(headers.get("origin", "") or "").strip()
        host = str(headers.get("host", "") or "").strip().casefold()
        allowed = {
            item.strip().casefold()
            for item in os.environ.get("WEBUI_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        }
        if origin:
            parsed = urlsplit(origin)
            origin_host = str(parsed.netloc or "").casefold()
            if origin.casefold() not in allowed and origin_host != host:
                return PlainTextResponse("Origin not allowed", status_code=403)
        if not secrets.compare_digest(str(headers.get("x-csrf-token", "") or ""), CSRF_NONCE):
            return PlainTextResponse("Invalid CSRF token", status_code=403)
    response = await call_next(request)
    response_headers = getattr(response, "headers", None)
    if response_headers is not None:
        response_headers["X-Content-Type-Options"] = "nosniff"
        response_headers["X-Frame-Options"] = "SAMEORIGIN"
        response_headers["Referrer-Policy"] = "same-origin"
        response_headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response_headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'"
        )
        body = getattr(response, "body", None)
        if path.startswith("/api/v2/") and isinstance(body, (bytes, bytearray)) and body:
            response_headers["ETag"] = f'"{hashlib.sha256(bytes(body)).hexdigest()[:24]}"'
    return response


@app.get("/")
def index() -> FileResponse:
    index_path = _frontend_static_dir() / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=503, detail="Frontend assets are not built. Run npm run build.")
    return FileResponse(index_path)


@app.get("/static/{name:path}")
def static_file(name: str) -> FileResponse:
    static_root = _frontend_static_dir().resolve()
    path = (static_root / name).resolve()
    if not path.exists() or not path.is_file() or static_root not in path.parents:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)


@app.get("/api/status")
def status(lite: bool = False) -> dict[str, Any]:
    config = _load_config()
    version = _version_summary()
    health = _health_summary(config, fast=lite)
    if lite:
        current_ai = _fast_current_ai(config)
        lite_state = _mikan_lite_state(config)
        mikan_operation = _mikan_operation_state()
        counts = lite_state.get("counts") if isinstance(lite_state.get("counts"), dict) else {}
        extract_jobs = lite_state.get("extract_jobs") if isinstance(lite_state.get("extract_jobs"), dict) else _mikan_extract_jobs_empty()
        pipeline = (
            lite_state.get("pipeline")
            if isinstance(lite_state.get("pipeline"), dict)
            else _mikan_pipeline_counts(counts, extract_jobs.get("counts") or {})
        )
        if int(pipeline.get("extracting") or 0) > 0:
            mikan_operation["busy"] = True
            active_operations = list(mikan_operation.get("active_operations") or [])
            if "subtitle_extract" not in active_operations:
                active_operations.append("subtitle_extract")
            mikan_operation["active_operations"] = active_operations
        return {
            "now": int(time.time()),
            "version": version,
            "health": health,
            "worker": _worker_summary(fast=True),
            "queue_counts": _fast_queue_counts(),
            "current_ai": current_ai,
            "io_policy": _io_policy_summary(config, current_ai),
            "ai_control": _ai_control_summary(),
            "ai_scheduler": _ai_scheduler_summary(config),
            "deployment_hold": _deployment_hold_summary(),
            "mikan": {
                **mikan_operation,
                "state_db": {
                    "exists": bool(lite_state.get("exists")),
                    "total": int(lite_state.get("total") or 0),
                    "active": sum(
                        int(pipeline.get(key) or 0)
                        for key in ("queued_downloads", "downloading", "extracting", "waiting_extract", "candidate_retry")
                    ),
                    "stalled": int(lite_state.get("stalled") or 0),
                    "zero_speed_downloading": int(lite_state.get("zero_speed_downloading") or 0),
                    "counts": counts,
                    "pipeline": pipeline,
                    "extract_jobs": extract_jobs,
                },
            },
            "action": _action_snapshot(),
        }
    return {
        "now": int(time.time()),
        "version": version,
        "health": health,
        "paths": {
            "config": str(CONFIG_PATH),
            "work": str(WORK_PATH),
            "logs": str(LOG_PATH),
            "docker_socket": str(DOCKER_SOCKET),
        },
        "docker_available": DOCKER_SOCKET.exists(),
        "worker": _worker_summary(),
        "io_policy": _io_policy_summary(config, _fast_current_ai(config)),
        "ai_control": _ai_control_summary(),
        "ai_scheduler": _ai_scheduler_summary(config),
        "deployment_hold": _deployment_hold_summary(),
        "config": {key: config.get(key) for key in EDITABLE_FIELDS},
        "library": _library_summary(),
        "eta": _eta_summary(),
        "mikan": _mikan_summary(config, include_downloads=False),
        "events": _events_summary(limit=12),
        "action": _action_snapshot(),
        "disk": _disk_summary(),
        "logs": {
            "app": _log_file_info("app.log"),
            "failed": _log_file_info("failed.log"),
        },
    }


@app.get("/api/v2/bootstrap")
def v2_bootstrap() -> dict[str, Any]:
    return {
        "api_version": 2,
        "csrf_token": CSRF_NONCE,
        "revision": _v2_revision(),
        "features": [
            "atomic_commands",
            "cursor_pagination",
            "quality_review",
            "target_mapping_review",
            "revision_stream",
        ],
    }


@app.get("/api/v2/overview")
def v2_overview() -> dict[str, Any]:
    return _v2_overview_payload()


@app.get("/api/v2/metrics/ai-delivery-slo")
def v2_ai_delivery_slo() -> dict[str, Any]:
    return _ai_delivery_slo_summary()


@app.get("/api/v2/worker/runtime-log")
def v2_worker_runtime_log(tail: int = 120) -> dict[str, Any]:
    return _worker_runtime_log(max(20, min(int(tail), 500)))


@app.get("/api/v2/ai/tasks")
def v2_ai_tasks(
    cursor: str | None = None,
    limit: int = 40,
    status_filter: str | None = None,
    search: str = "",
    mode: str = "active",
    fields: str = "compact",
) -> dict[str, Any]:
    page_size = max(1, min(100, int(limit)))
    offset = _decode_cursor(cursor)
    if offset % page_size:
        raise HTTPException(status_code=400, detail="Cursor does not match page size")
    config = _load_config()
    payload = _dashboard_tasks_summary(
        limit=page_size,
        status_filter=status_filter or None,
        search=search.strip(),
        page=(offset // page_size) + 1,
        page_size=page_size,
        mode=mode,
        stale_running_seconds=_config_stale_running_seconds(config),
        max_concurrent_videos=_config_max_concurrent_videos(config),
    )
    raw_items = [item for item in payload.get("tasks") or [] if isinstance(item, dict)]
    items = (
        _completed_delivery_task_details(raw_items, config)
        if str(fields).casefold() == "detail"
        else [_compact_ai_task(item) for item in raw_items]
    )
    next_offset = offset + len(raw_items)
    return {
        "items": items,
        "counts": payload.get("counts") or {},
        "total": int(payload.get("filtered") or 0),
        "next_cursor": _encode_cursor(next_offset) if next_offset < int(payload.get("filtered") or 0) else None,
        "revision": _v2_revision(),
    }


@app.get("/api/v2/mikan/items")
def v2_mikan_items(
    cursor: str | None = None,
    limit: int = 40,
    status_filter: str | None = None,
    search: str = "",
    fields: str = "compact",
) -> dict[str, Any]:
    page_size = max(1, min(100, int(limit)))
    offset = _decode_cursor(cursor)
    if offset % page_size:
        raise HTTPException(status_code=400, detail="Cursor does not match page size")
    config = _load_config()
    payload = _mikan_downloads_summary(
        config,
        page=(offset // page_size) + 1,
        page_size=page_size,
        status_filter=status_filter,
        search=search.strip(),
    )
    if str(fields).casefold() != "detail":
        payload = _compact_mikan_downloads_payload(payload)
    items = [item for item in payload.get("recent") or [] if isinstance(item, dict)]
    total = int(payload.get("filtered") or payload.get("total") or 0)
    next_offset = offset + len(items)
    return {
        "items": items,
        "counts": payload.get("counts") or {},
        "total": total,
        "next_cursor": _encode_cursor(next_offset) if next_offset < total else None,
        "revision": _v2_revision(),
    }


@app.post("/api/v2/commands", status_code=202)
async def v2_create_command(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    action = str(payload.get("action") or "").strip().casefold()
    if action not in V2_COMMAND_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported command action: {action or '-'}")
    headers = getattr(request, "headers", {})
    idempotency_key = str(headers.get("idempotency-key", "") or payload.get("idempotency_key") or "").strip()
    if not idempotency_key or len(idempotency_key) > 200:
        raise HTTPException(status_code=400, detail="A valid Idempotency-Key is required")
    target = str(payload.get("target") or "").strip()
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    else:
        parameters = dict(parameters)
    if action == "ai.canary_once":
        reserved = sorted(set(parameters) & AI_CANARY_ONCE_SERVER_PARAMETERS)
        if reserved:
            raise HTTPException(
                status_code=400,
                detail=f"AI canary server parameters cannot be overridden: {', '.join(reserved)}",
            )
        unexpected = sorted(set(parameters) - AI_CANARY_ONCE_CLIENT_PARAMETERS)
        if unexpected:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported AI canary parameters: {', '.join(unexpected)}",
            )
        expected_failure_revision = parameters.get("expected_failure_revision")
        if (
            not isinstance(expected_failure_revision, str)
            or re.fullmatch(r"[0-9a-f]{24}", expected_failure_revision) is None
        ):
            raise HTTPException(
                status_code=400,
                detail="AI canary expected_failure_revision must be 24 lowercase hexadecimal characters",
            )
        expected_failure_code = parameters.get("expected_failure_code")
        if (
            not isinstance(expected_failure_code, str)
            or expected_failure_code not in AI_CANARY_ONCE_FAILURE_CODES
        ):
            raise HTTPException(status_code=400, detail="Invalid AI canary expected_failure_code")
        expected_media_mtime_ns = parameters.get("expected_media_mtime_ns")
        if (
            isinstance(expected_media_mtime_ns, bool)
            or not isinstance(expected_media_mtime_ns, int)
            or expected_media_mtime_ns <= 0
        ):
            raise HTTPException(
                status_code=400,
                detail="AI canary expected_media_mtime_ns must be a positive integer",
            )
        parameters = {
            "expected_failure_revision": expected_failure_revision,
            "expected_failure_code": expected_failure_code,
            "expected_media_mtime_ns": expected_media_mtime_ns,
            "campaign_key": idempotency_key,
            "max_items": 1,
            "max_in_flight": 1,
            "max_consecutive_failures": 1,
            "strategy_version": "canary-once-v1",
        }
    if action == "system.ai_failed_retry_sweep":
        operation = str(parameters.get("operation") or "preview").strip().casefold()
        if operation not in {"preview", "start", "pause", "resume", "cancel"}:
            raise HTTPException(status_code=400, detail="Invalid AI failed retry sweep operation")
        if operation in {"preview", "start"}:
            try:
                max_items = int(parameters.get("max_items", 1) or 0)
                interval_seconds = int(parameters.get("interval_seconds", 300) or 0)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Invalid AI failed retry sweep limits") from exc
            if not 1 <= max_items <= 5 or not 300 <= interval_seconds <= 86400:
                raise HTTPException(status_code=400, detail="Unsafe AI failed retry sweep limits")
        if operation == "start":
            parameters["campaign_key"] = idempotency_key
    if action.startswith("ai."):
        if not target:
            raise HTTPException(status_code=400, detail="AI commands require a target path")
        target = _validated_anime_path_text(target)
    elif action.startswith("series.") and action != "series.sync":
        if not target:
            raise HTTPException(status_code=400, detail="Series commands require a target path")
        target = _validated_anime_path_text(target)
    config = _load_config()
    try:
        return enqueue_atomic_command(
            config=config,
            work_path=_configured_work_path(config),
            expand=_expand_config_env,
            action=action,
            target=target,
            parameters=parameters,
            idempotency_key=idempotency_key,
        )
    except CommandConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v2/commands/{command_id}")
def v2_command_status(command_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"cmd_[0-9a-f]{24}", str(command_id or "")):
        raise HTTPException(status_code=400, detail="Invalid command id")
    config = _load_config()
    command = read_command(_control_state_db_path(config), command_id)
    if command is not None:
        return command
    inbox_path = _control_inbox_dir(config) / f"{command_id}.json"
    if inbox_path.is_file():
        return {"command_id": command_id, "status": "accepted"}
    raise HTTPException(status_code=404, detail="Command not found")


@app.get("/api/v2/review-items")
def v2_review_items(
    cursor: str | None = None,
    limit: int = 30,
    status: str = "open",
    kind: str = "",
    state: str = "",
    search: str = "",
    sort: str = "priority",
    view: str = "detail",
) -> dict[str, Any]:
    page_size = max(1, min(100, int(limit)))
    offset = _decode_cursor(cursor)
    config = _load_config()
    normalized_view = str(view or "detail").strip().casefold()
    if normalized_view not in {"summary", "detail"}:
        raise HTTPException(status_code=400, detail="Review view must be summary or detail")
    normalized_state = str(state or "").strip().casefold()
    if normalized_state not in {"", "needs_action", "processing", "resolved"}:
        raise HTTPException(status_code=400, detail="Invalid review state")
    normalized_sort = str(sort or "priority").strip().casefold()
    if normalized_sort not in {"priority", "latest", "oldest"}:
        raise HTTPException(status_code=400, detail="Invalid review sort")
    active_queue_targets = review_active_queue_targets(WORK_PATH / "scanner_state.sqlite3")
    items, total = list_reviews(
        _control_state_db_path(config),
        status=str(status or "").strip(),
        kind=str(kind or "").strip(),
        limit=page_size,
        offset=offset,
        state=normalized_state,
        search=str(search or "").strip()[:160],
        sort=normalized_sort,
        active_queue_targets=active_queue_targets,
    )
    items = _prepare_review_items_with_action_state(items, config=config)
    if normalized_view == "summary":
        items = [_review_summary_payload(item) for item in items]
    next_offset = offset + len(items)
    return {
        "items": items,
        "total": total,
        "next_cursor": _encode_cursor(next_offset) if next_offset < total else None,
        "state_counts": _review_state_counts(
            config,
            active_queue_targets=active_queue_targets,
        ),
        "query": {
            "state": normalized_state or ("resolved" if str(status).casefold() == "resolved" else "needs_action"),
            "kind": str(kind or ""),
            "search": str(search or ""),
            "sort": normalized_sort,
            "view": normalized_view,
        },
        "revision": _v2_revision(),
    }


def _review_candidate_has_semantic_evidence(candidate: dict[str, Any]) -> bool:
    if candidate.get("selectable") is False:
        return False
    reasons = {
        str(reason or "").strip().casefold()
        for reason in candidate.get("reasons") or []
        if str(reason or "").strip()
    }
    return any(
        reason.startswith(("title_", "sequel_token:", "series_mapping:", "locked_mapping:"))
        or reason in {"manual_mapping", "bangumi_mapping"}
        for reason in reasons
    )


def _prepare_review_item(item: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(item)
    diagnosis = dict(prepared.get("diagnosis") or {})
    diagnosis["reports"] = _normalized_review_reports(diagnosis.get("reports"))
    diagnosis["line_previews"] = _normalized_review_line_previews(diagnosis.get("line_previews"))
    diagnosis["media_file"] = _normalized_file_info(diagnosis.get("media_file"))
    prepared["diagnosis"] = diagnosis
    prepared["source_lifecycle"] = str(diagnosis.get("source_lifecycle") or "")
    prepared["source_torrent_in_qbit"] = bool(diagnosis.get("source_torrent_in_qbit"))
    prepared["source_files_present"] = bool(diagnosis.get("source_files_present"))
    prepared["source_redownload_available"] = bool(diagnosis.get("source_redownload_available"))
    prepared["problem"] = _review_problem(prepared)
    if str(prepared.get("kind") or "") == "target_ambiguity":
        stored_candidates = list(prepared.get("candidates") or [])
        distinct_candidates = _deduplicate_review_candidates(stored_candidates)
        candidates = [
            {**candidate, "file_info": _normalized_file_info(candidate.get("file_info"))}
            for candidate in distinct_candidates
            if isinstance(candidate, dict) and _review_candidate_has_semantic_evidence(candidate)
        ]
        prepared["candidates"] = _enrich_review_candidates(candidates, config=config)
        rejected_count = len(distinct_candidates) - len(candidates)
        if rejected_count:
            diagnosis["rejected_candidate_count"] = rejected_count
            diagnosis["rejected_candidate_reason"] = "missing_title_or_locked_mapping_evidence"
    artwork_url = _review_artwork_url(prepared, config=config)
    if artwork_url:
        prepared["artwork_url"] = artwork_url
    prepared["recommended_action"] = _review_recommended_action(prepared)
    prepared["batch_eligible"] = bool(_safe_batch_review_body(prepared))
    return prepared


def _deduplicate_review_candidates(value: object) -> list[dict[str, Any]]:
    candidates = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    indexes: dict[str, int] = {}
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        path = str(candidate.get("path") or candidate.get("series_path") or "").strip()
        normalized_path = path.replace("\\", "/")
        windows_style = "\\" in path or bool(re.match(r"^[A-Za-z]:/", normalized_path))
        key = normalized_path.casefold() if windows_style else normalized_path
        if not key or key not in indexes:
            indexes[key or f"missing:{len(result)}"] = len(result)
            result.append(candidate)
            continue
        index = indexes[key]
        current = result[index]
        blocked = current.get("selectable") is False or candidate.get("selectable") is False
        current_reasons = [str(reason) for reason in current.get("reasons") or [] if str(reason)]
        incoming_reasons = [str(reason) for reason in candidate.get("reasons") or [] if str(reason)]
        reasons = list(dict.fromkeys([*current_reasons, *incoming_reasons]))
        try:
            current_score = float(current.get("score") or 0)
        except (TypeError, ValueError):
            current_score = 0.0
        try:
            incoming_score = float(candidate.get("score") or 0)
        except (TypeError, ValueError):
            incoming_score = 0.0
        if incoming_score > current_score:
            current = candidate
            result[index] = current
        current["reasons"] = reasons
        if blocked:
            current["selectable"] = False
    return result


def _normalized_review_reports(value: object) -> list[dict[str, Any]]:
    reports = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for report in reports:
        if not isinstance(report, dict):
            continue
        normalized = dict(report)
        issues: list[dict[str, Any]] = []
        for issue in report.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            indexes = sorted({int(index) for index in issue.get("indexes") or [] if str(index).isdigit()})
            key = f"{str(issue.get('code') or '').casefold()}:{','.join(map(str, indexes))}"
            if key in seen:
                continue
            seen.add(key)
            issues.append({**issue, "indexes": indexes})
        normalized["issues"] = issues
        result.append(normalized)
    return result


def _normalized_review_line_previews(value: object) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index") or 0)
        except (TypeError, ValueError):
            continue
        if index <= 0 or index in seen:
            continue
        seen.add(index)
        result.append(
            {
                "index": index,
                "timing": str(row.get("timing") or "")[:80],
                "source_ja": str(row.get("source_ja") or "")[:500],
                "output_zh": str(row.get("output_zh") or "")[:500],
                "issue_codes": [str(code)[:80] for code in row.get("issue_codes") or []][:8],
            }
        )
        if len(result) >= 50:
            break
    return result


def _normalized_file_info(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        timestamp = float(value.get("timestamp") or 0)
        size = max(0, int(value.get("size") or 0))
    except (TypeError, ValueError):
        return {}
    kind = str(value.get("kind") or "").strip().casefold()
    if timestamp <= 0 or kind not in {"created", "modified"}:
        return {}
    return {
        "timestamp": timestamp,
        "kind": kind,
        "size": size,
    }


def _review_issue_indexes(item: dict[str, Any]) -> list[int]:
    indexes: set[int] = set()
    for report in (item.get("diagnosis") or {}).get("reports") or []:
        for issue in report.get("issues") or []:
            indexes.update(int(value) for value in issue.get("indexes") or [] if str(value).isdigit())
    return sorted(value for value in indexes if value > 0)[:500]


def _review_issue_codes(item: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for report in (item.get("diagnosis") or {}).get("reports") or []:
        for issue in report.get("issues") or []:
            code = str(issue.get("code") or "").strip().casefold().replace("-", "_")
            if code:
                codes.add(code)
    return codes


def _review_remediation_candidate(item: dict[str, Any], action: str) -> dict[str, Any] | None:
    expected = str(action or "").strip().casefold()
    for candidate in item.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("action") or "").strip().casefold() == expected:
            return candidate
    return None


def _review_candidate_line_indexes(candidate: dict[str, Any] | None) -> list[int]:
    if not isinstance(candidate, dict):
        return []
    raw_indexes = candidate.get("indexes")
    if isinstance(raw_indexes, list):
        try:
            indexes = {int(value) for value in raw_indexes if int(value) > 0}
        except (TypeError, ValueError):
            return []
        return sorted(indexes)[:500]
    line_spec = str(candidate.get("lines") or "").strip()
    if not re.fullmatch(r"\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*", line_spec):
        return []
    indexes: set[int] = set()
    for token in line_spec.split(","):
        bounds = [int(value.strip()) for value in token.split("-", 1)]
        start, end = (bounds[0], bounds[0]) if len(bounds) == 1 else (bounds[0], bounds[1])
        if start <= 0 or end < start or end - start > 500:
            return []
        indexes.update(range(start, end + 1))
        if len(indexes) > 500:
            return []
    return sorted(indexes)


def _review_recommended_action(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    if kind == "target_ambiguity":
        safe = _safe_target_batch_body(item)
        if safe:
            season = _review_candidate_selection(str(safe["candidate_path"]))[2]
            label = "確認特別篇並重新提取" if season == 0 else f"確認第 {season} 季並重新提取"
            return {"action": "target.confirm_candidate", "label": label, "safe": True}
        if any(
            isinstance(candidate, dict) and _review_candidate_has_semantic_evidence(candidate)
            for candidate in item.get("candidates") or []
        ):
            return {"action": "target.choose_candidate", "label": "選擇正確作品與季度", "safe": False}
        return {"action": "target.rebuild_candidates", "label": "整理作品資料並重新比對", "safe": False}
    if kind in {"subtitle_quality", "asr_quality"} and _failed_review_requires_retranscription(item):
        return {
            "action": "ai.retranscribe",
            "label": "重新轉錄並修復 ASR 原文",
            "safe": True,
        }
    issue_codes = _review_issue_codes(item)
    source_issue = bool(
        issue_codes.intersection({"asr_prompt_echo", "hallucination_text", "asr_low_confidence", "leading_gap"})
    )
    retranscribe_candidate = _review_remediation_candidate(item, "ai.retranscribe")
    if kind == "asr_quality":
        return {
            "action": "ai.retranscribe",
            "label": "重新辨識問題片段",
            "safe": retranscribe_candidate is not None or not (item.get("candidates") or []),
        }
    if kind == "subtitle_quality" and source_issue:
        return {
            "action": "ai.retranscribe",
            "label": "重新轉錄並修復日文來源",
            "safe": retranscribe_candidate is not None,
        }
    line_candidate = _review_remediation_candidate(item, "ai.retranslate_lines")
    candidate_indexes = _review_candidate_line_indexes(line_candidate)
    reported_indexes = set(_review_issue_indexes(item))
    if kind == "subtitle_quality" and candidate_indexes and set(candidate_indexes).issubset(reported_indexes):
        return {
            "action": "ai.retranslate_lines",
            "label": f"重新翻譯 {len(candidate_indexes)} 行問題字幕",
            "indexes": candidate_indexes,
            "safe": True,
        }
    return {
        "action": "ai.retranslate",
        "label": "需要確認可用的修復方式",
        "safe": False,
    }


def _failed_review_requires_retranscription(item: dict[str, Any]) -> bool:
    action_state = item.get("action_state") if isinstance(item.get("action_state"), dict) else {}
    if str(action_state.get("status") or "").strip().casefold() != "failed":
        return False
    error = str(action_state.get("error") or "").strip().casefold()
    return (
        "retranscription is required" in error
        or "requires retranscription" in error
        or "use full retranscribe instead" in error
        or "japanese transcript is unavailable" in error
        or ("japanese asr diagnostic" in error and "retranscript" in error)
    )


def _safe_target_batch_body(item: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        candidate
        for candidate in item.get("candidates") or []
        if isinstance(candidate, dict) and _review_candidate_has_semantic_evidence(candidate)
    ]
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    path = str(candidate.get("path") or candidate.get("series_path") or "").strip()
    diagnosis = item.get("diagnosis") if isinstance(item.get("diagnosis"), dict) else {}
    recovery = diagnosis.get("recovery") if isinstance(diagnosis.get("recovery"), dict) else {}
    source = candidate.get("source_id") or recovery.get("source_id")
    diagnosed = {
        str(value).strip()
        for value in diagnosis.get("bangumi_ids") or []
        if str(value).strip().isdigit()
    }
    if not source and len(diagnosed) == 1:
        source = next(iter(diagnosed))
    if not path or not str(source or "").isdigit():
        return None
    reasons = {str(reason or "").strip().casefold() for reason in candidate.get("reasons") or []}
    strong_mapping = any(
        reason.startswith(("series_mapping:", "locked_mapping:"))
        or reason in {"manual_mapping", "bangumi_mapping"}
        for reason in reasons
    )
    try:
        score = float(candidate.get("score") or 0)
        margin = float(candidate.get("margin") or 0)
        confidence = float(candidate.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    if not strong_mapping and not (confidence >= 0.9 or (score >= 1000 and margin >= 100)):
        return None
    return {
        "candidate_path": path,
        "source_id": str(int(str(source))),
        "series_id": str(candidate.get("series_id") or ""),
    }


def _safe_batch_review_body(item: dict[str, Any]) -> dict[str, Any] | None:
    if str(item.get("status") or "") != "open":
        return None
    state = str(item.get("state") or "").strip().casefold()
    if state and state != "needs_action":
        return None
    if str(item.get("kind") or "") == "target_ambiguity":
        return _safe_target_batch_body(item)
    target = str((item.get("diagnosis") or {}).get("video") or item.get("target_key") or "").strip()
    if not target:
        return None
    action = _review_recommended_action(item)
    if not bool(action.get("safe")):
        return None
    body: dict[str, Any] = {"action": action["action"], "target": target}
    if action["action"] == "ai.retranslate_lines":
        body["indexes"] = action.get("indexes") or []
    return body


def _review_summary_payload(item: dict[str, Any]) -> dict[str, Any]:
    diagnosis = item.get("diagnosis") or {}
    problem = item.get("problem") or {}
    resolution = item.get("resolution") or {}
    media_title = str(diagnosis.get("series_title") or diagnosis.get("torrent_name") or "").strip()
    media_path = str(diagnosis.get("video") or item.get("target_key") or "").strip()
    if not media_title:
        media_title = PurePosixPath(media_path.replace("\\", "/")).name or str(item.get("summary") or "")
    indexes = _review_issue_indexes(item)
    action_state = item.get("action_state") or {"status": "idle"}
    return {
        "review_id": item.get("review_id"),
        "kind": item.get("kind"),
        "state": item.get("state"),
        "status": item.get("status"),
        "severity": item.get("severity"),
        "title": str(problem.get("title") or item.get("summary") or "需要處理"),
        "description": str(problem.get("description") or ""),
        "media_title": media_title,
        "media_path": media_path,
        "candidate_count": len(item.get("candidates") or []),
        "issue_count": sum(
            len(report.get("issues") or [])
            for report in diagnosis.get("reports") or []
            if isinstance(report, dict)
        ),
        "affected_indexes": indexes,
        "media_file": _normalized_file_info(diagnosis.get("media_file")),
        "source_lifecycle": str(diagnosis.get("source_lifecycle") or ""),
        "source_torrent_in_qbit": bool(diagnosis.get("source_torrent_in_qbit")),
        "source_files_present": bool(diagnosis.get("source_files_present")),
        "source_redownload_available": bool(diagnosis.get("source_redownload_available")),
        "recommended_action": item.get("recommended_action") or {},
        "batch_eligible": bool(item.get("batch_eligible")),
        "dismissed": bool(resolution.get("dismissed")),
        "action_state": action_state,
        "duplicate_count": int(item.get("duplicate_count") or 1),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "resolved_at": item.get("resolved_at"),
    }


def _attach_review_action_state(
    item: dict[str, Any],
    command: dict[str, Any] | None,
    *,
    queue_state: dict[str, Any] | None = None,
) -> None:
    status = str((command or {}).get("status") or "idle").casefold()
    review_updated_at = float(item.get("updated_at") or 0)
    command_finished_at = float((command or {}).get("finished_at") or 0)
    command_requested_at = float((command or {}).get("requested_at") or 0)
    command_terminal_at = command_finished_at or command_requested_at
    stale_terminal_command = (
        str(item.get("status") or "") == "open"
        and status in {"completed", "failed"}
        and review_updated_at > 0
        and command_terminal_at > 0
        and command_terminal_at < review_updated_at
    )
    if stale_terminal_command:
        command = None
        status = "idle"
    queued_result = (
        status == "completed"
        and bool(((command or {}).get("result") or {}).get("queued"))
    )
    queue_status = str((queue_state or {}).get("status") or "").strip().casefold()
    action_error = str((command or {}).get("error") or "")
    if queued_result and queue_state is not None:
        if queue_status in {"queued", "running"}:
            display_status = queue_status
        elif queue_status in {"failed", "failed_retry", "paused"}:
            display_status = "failed"
            action_error = str((queue_state or {}).get("last_error") or action_error)
        else:
            display_status = "completed"
    else:
        display_status = "queued" if queued_result else status
    item["action_state"] = {
        "command_id": str((command or {}).get("command_id") or ""),
        "action": str((command or {}).get("action") or ""),
        "status": display_status,
        "error": action_error,
        "queue_status": queue_status,
        "requested_at": float((command or {}).get("requested_at") or 0),
        "started_at": float((command or {}).get("started_at") or 0),
        "finished_at": float((command or {}).get("finished_at") or 0),
    }
    if str(item.get("status") or "") == "resolved":
        item["state"] = "resolved"
    elif display_status in {"accepted", "queued", "running"}:
        item["state"] = "processing"
    else:
        item["state"] = "needs_action"
    item["recommended_action"] = _review_recommended_action(item)
    item["batch_eligible"] = bool(_safe_batch_review_body(item))


def _prepare_review_items_with_action_state(
    items: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    prepared = [_prepare_review_item(item, config=config) for item in items]
    if not prepared:
        return prepared
    commands = review_command_states(
        _control_state_db_path(config),
        [str(item.get("review_id") or "") for item in prepared],
        inbox=_control_inbox_dir(config),
    )
    queue_states = review_queue_states(
        WORK_PATH / "scanner_state.sqlite3",
        [str(command.get("target") or "") for command in commands.values()],
    )
    for item in prepared:
        command = commands.get(str(item.get("review_id") or ""))
        _attach_review_action_state(
            item,
            command,
            queue_state=queue_states.get(str((command or {}).get("target") or ""), {}),
        )
    return prepared


def _review_automation_counts(
    config: dict[str, Any],
    *,
    active_queue_targets: set[str],
    needs_action_count: int,
) -> dict[str, int]:
    database = _control_state_db_path(config)
    try:
        database_key = str(database.resolve())
    except OSError:
        database_key = str(database)
    targets_payload = "\0".join(sorted(str(target) for target in active_queue_targets if str(target)))
    targets_digest = hashlib.sha256(targets_payload.encode("utf-8", errors="replace")).hexdigest()
    cache_key = (database_key, int(needs_action_count), targets_digest)

    # Hold the lock across the first calculation so concurrent SSE/page
    # requests do not all perform the same multi-page review scan.
    with _REVIEW_AUTOMATION_COUNTS_CACHE_LOCK:
        cached = _ttl_cache_get(
            _REVIEW_AUTOMATION_COUNTS_CACHE,
            cache_key,
            REVIEW_AUTOMATION_COUNTS_CACHE_TTL_SECONDS,
        )
        if isinstance(cached, dict):
            return {
                "automatic_safe": int(cached.get("automatic_safe") or 0),
                "human_required": int(cached.get("human_required") or 0),
            }
        try:
            result = _review_automation_counts_uncached(
                config,
                active_queue_targets=active_queue_targets,
                needs_action_count=needs_action_count,
            )
        except Exception:  # noqa: BLE001 - summary statistics must fail closed.
            result = {
                "automatic_safe": 0,
                "human_required": max(0, int(needs_action_count)),
            }
        if len(_REVIEW_AUTOMATION_COUNTS_CACHE) >= 64 and cache_key not in _REVIEW_AUTOMATION_COUNTS_CACHE:
            _REVIEW_AUTOMATION_COUNTS_CACHE.clear()
        _ttl_cache_set(_REVIEW_AUTOMATION_COUNTS_CACHE, cache_key, dict(result))
        return dict(result)


def _review_automation_counts_uncached(
    config: dict[str, Any],
    *,
    active_queue_targets: set[str],
    needs_action_count: int,
) -> dict[str, int]:
    """Partition needs-action reviews without executing any remediation."""

    automatic_safe = 0
    offset = 0
    while offset < needs_action_count:
        items, total = list_reviews(
            _control_state_db_path(config),
            status="open",
            kind="",
            limit=min(200, needs_action_count - offset),
            offset=offset,
            state="needs_action",
            active_queue_targets=active_queue_targets,
        )
        if not items:
            break
        prepared = _prepare_review_items_with_action_state(items, config=config)
        automatic_safe += len(_review_automatable_quality_review_ids(prepared, config=config))
        offset += len(items)
        if offset >= int(total or 0):
            break

    # Concurrent review changes or a read failure must never overstate what is
    # safe to automate. Any unclassified remainder stays human-required.
    automatic_safe = min(needs_action_count, automatic_safe)
    return {
        "automatic_safe": automatic_safe,
        "human_required": max(0, needs_action_count - automatic_safe),
    }


def _review_automatable_quality_review_ids(
    items: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> set[str]:
    """Return only reviews provably eligible for the Worker's next autopilot attempt."""

    if config.get("auto_ai_quality_review_autopilot_enabled") is not True:
        return set()
    candidates = [
        item
        for item in items
        if bool(item.get("batch_eligible"))
        and str(item.get("kind") or "") == "asr_quality"
        and str((item.get("recommended_action") or {}).get("action") or "") == "ai.retranscribe"
    ]
    targets = {
        str(item.get("review_id") or ""): str(
            (item.get("diagnosis") or {}).get("video") or item.get("target_key") or ""
        ).strip()
        for item in candidates
    }
    queue_states = review_queue_states(
        WORK_PATH / "scanner_state.sqlite3",
        [target for target in targets.values() if target],
    )
    failure_revisions: dict[str, str] = {}
    for review_id, target in targets.items():
        snapshot = queue_states.get(target) or {}
        failure_revision = str(snapshot.get("failure_revision") or "").strip()
        if (
            review_id
            and target
            and str(snapshot.get("status") or "").strip().casefold() == "paused"
            and str(snapshot.get("last_error_code") or "").strip().casefold()
            == "deterministic_asr_quality"
            and failure_revision
        ):
            failure_revisions[review_id] = failure_revision
    return review_autopilot_revision_attempts_allowed(
        _control_state_db_path(config),
        idempotency_prefix=_AI_QUALITY_REVIEW_AUTOPILOT_PREFIX,
        failure_revisions=failure_revisions,
        max_attempts=_AI_QUALITY_REVIEW_AUTOPILOT_MAX_ATTEMPTS,
    )


def _review_state_counts(
    config: dict[str, Any],
    *,
    active_queue_targets: set[str] | None = None,
) -> dict[str, int]:
    database = _control_state_db_path(config)
    counts = review_state_counts(database)
    if active_queue_targets is None:
        active_queue_targets = review_active_queue_targets(WORK_PATH / "scanner_state.sqlite3")
    _items, processing = list_reviews(
        database,
        status="open",
        kind="",
        limit=1,
        offset=0,
        state="processing",
        active_queue_targets=active_queue_targets,
    )
    open_count = int(counts.get("open") or 0)
    processing_count = min(open_count, int(processing or 0))
    needs_action_count = max(0, open_count - processing_count)
    automation_counts = _review_automation_counts(
        config,
        active_queue_targets=active_queue_targets,
        needs_action_count=needs_action_count,
    )
    return {
        **counts,
        "needs_action": needs_action_count,
        "processing": processing_count,
        **automation_counts,
    }


def _review_source_publication_timestamp(entries: list[dict[str, Any]]) -> float:
    """Return only a trustworthy upstream publication timestamp.

    Legacy qbit-recovered rows use qBittorrent's completion time as pub_date.
    That value must remain a download timestamp and must not be shown to the
    user as a source publication date.
    """

    return max(
        (_mikan_entry_source_publication_timestamp(entry) for entry in entries),
        default=0.0,
    )


def _review_source_publication_precision(entries: list[dict[str, Any]]) -> str:
    _timestamp, precision = max(
        (_mikan_entry_source_publication(entry) for entry in entries),
        key=lambda item: (item[0], item[1] == "time"),
        default=(0.0, ""),
    )
    return precision


def _normalized_source_tag(value: object) -> str:
    return str(value or "").strip().casefold().replace("_", "-")


def _mikan_entry_source_publication_timestamp(entry: dict[str, Any]) -> float:
    return _mikan_entry_source_publication(entry)[0]


def _mikan_entry_source_publication(entry: dict[str, Any]) -> tuple[float, str]:
    if not isinstance(entry, dict):
        return (0.0, "")
    source = next(
        (
            _normalized_source_tag(entry.get(key))
            for key in (
                "source",
                "last_completed_source",
                "last_failed_source",
                "last_superseded_source",
            )
            if str(entry.get(key) or "").strip()
        ),
        "",
    )
    source_url = str(
        entry.get("torrent_url")
        or entry.get("last_completed_torrent_url")
        or entry.get("last_failed_torrent_url")
        or entry.get("last_superseded_torrent_url")
        or ""
    ).strip().casefold()
    publication = (0.0, "")
    if source != "qbit-recovered" and (source or (source_url and not source_url.startswith("qbit://"))):
        exact = _parse_timestamp(entry.get("pub_date"))
        inferred = _torrent_url_date_timestamp(source_url) if exact <= 0 else 0.0
        publication = (exact or inferred, "time" if exact > 0 else "date" if inferred > 0 else "")

    deferred_source = _normalized_source_tag(entry.get("deferred_source"))
    deferred_url = str(entry.get("deferred_torrent_url") or "").strip().casefold()
    if deferred_source != "qbit-recovered" and (
        deferred_source or (deferred_url and not deferred_url.startswith("qbit://"))
    ):
        exact = _parse_timestamp(entry.get("deferred_pub_date"))
        inferred = _torrent_url_date_timestamp(deferred_url) if exact <= 0 else 0.0
        deferred_publication = (
            exact or inferred,
            "time" if exact > 0 else "date" if inferred > 0 else "",
        )
        publication = max(
            publication,
            deferred_publication,
            key=lambda item: (item[0], item[1] == "time"),
        )
    return publication


def _torrent_url_date_timestamp(value: object) -> float:
    raw = str(value or "").strip()
    if not raw or raw.casefold().startswith("qbit://"):
        return 0.0
    try:
        segments = [segment for segment in urlsplit(raw).path.split("/") if segment]
    except ValueError:
        return 0.0
    for segment in segments:
        if not re.fullmatch(r"(?:19|20)\d{6}", segment):
            continue
        try:
            return datetime.strptime(segment, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0.0


def _qbit_torrent_creation_timestamp(config: dict[str, Any], torrent_hash: str) -> float:
    """Read qBittorrent's metainfo creation date without mutating qB state."""

    normalized_hash = str(torrent_hash or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", normalized_hash):
        return 0.0
    base_url = _expand_config_env(str(config.get("qbit_base_url") or "")).strip().rstrip("/")
    if not base_url.lower().startswith(("http://", "https://")):
        return 0.0
    cache_key = f"{base_url.casefold()}|{normalized_hash}"
    now = time.time()
    with _QBIT_TORRENT_TIME_CACHE_LOCK:
        cached = _QBIT_TORRENT_TIME_CACHE.get(cache_key)
        if cached and float(cached.get("expires_at") or 0) > now:
            return float(cached.get("timestamp") or 0)

    username = _expand_config_env(str(config.get("qbit_username") or "")).strip()
    password = _expand_config_env(str(config.get("qbit_password") or ""))
    try:
        configured_timeout = float(config.get("qbit_timeout_seconds") or 3)
    except (TypeError, ValueError):
        configured_timeout = 3.0
    timeout = max(0.5, min(2.0, configured_timeout))
    timestamp = 0.0
    try:
        cookie_jar = http.cookiejar.CookieJar()
        opener = urllib_request.build_opener(urllib_request.HTTPCookieProcessor(cookie_jar))
        login_request = urllib_request.Request(
            f"{base_url}/api/v2/auth/login",
            data=urllib_parse.urlencode({"username": username, "password": password}).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with opener.open(login_request, timeout=timeout) as response:
            login_body = response.read(64).decode("utf-8", errors="replace").strip()
            if int(getattr(response, "status", 200) or 200) not in {200, 204} or login_body not in {"", "Ok.", "Ok"}:
                raise ValueError("qBittorrent login rejected")
        properties_url = (
            f"{base_url}/api/v2/torrents/properties?"
            + urllib_parse.urlencode({"hash": normalized_hash})
        )
        with opener.open(properties_url, timeout=timeout) as response:
            if int(getattr(response, "status", 200) or 200) != 200:
                raise ValueError("qBittorrent properties request failed")
            payload = json.loads(response.read(256 * 1024).decode("utf-8"))
            timestamp = _parse_timestamp((payload or {}).get("creation_date"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, urllib_error.URLError):
        timestamp = 0.0

    with _QBIT_TORRENT_TIME_CACHE_LOCK:
        if len(_QBIT_TORRENT_TIME_CACHE) >= 512 and cache_key not in _QBIT_TORRENT_TIME_CACHE:
            _QBIT_TORRENT_TIME_CACHE.pop(next(iter(_QBIT_TORRENT_TIME_CACHE)), None)
        _QBIT_TORRENT_TIME_CACHE[cache_key] = {
            "timestamp": float(timestamp),
            "expires_at": now + (300.0 if timestamp > 0 else 30.0),
        }
    return float(timestamp)


def _review_source_date_is_near_air_release(diagnosis: dict[str, Any]) -> bool:
    raw = " ".join(
        str(diagnosis.get(key) or "")
        for key in ("torrent_name", "source_video")
    ).casefold()
    normalized = re.sub(r"[^a-z0-9]+", "", raw)
    if any(token in normalized for token in ("bdrip", "bluray", "bdremux", "remux", "dvdrip", "dvd")):
        return False
    return any(token in normalized for token in ("webrip", "webdl", "hdtv"))


def _apply_target_review_date_guidance(item: dict[str, Any]) -> dict[str, Any]:
    if str(item.get("kind") or "") != "target_ambiguity":
        return item
    prepared = dict(item)
    diagnosis = dict(prepared.get("diagnosis") or {})
    source_published_at = _parse_timestamp(diagnosis.get("source_published_at"))
    candidates = [dict(candidate) for candidate in prepared.get("candidates") or [] if isinstance(candidate, dict)]
    if source_published_at <= 0 or not candidates or not _review_source_date_is_near_air_release(diagnosis):
        prepared["diagnosis"] = diagnosis
        prepared["candidates"] = candidates
        return prepared

    dated: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        file_info = _normalized_file_info(candidate.get("file_info"))
        timestamp = _parse_timestamp(file_info.get("timestamp"))
        if timestamp <= 0:
            continue
        distance_days = round(abs(timestamp - source_published_at) / 86400.0, 1)
        candidate["source_date_distance_days"] = distance_days
        dated.append((distance_days, candidate))

    if len(dated) != len(candidates):
        prepared["diagnosis"] = diagnosis
        prepared["candidates"] = candidates
        return prepared
    dated.sort(key=lambda pair: (pair[0], str(pair[1].get("path") or "").casefold()))
    closest_distance, closest = dated[0]
    runner_up_distance = dated[1][0] if len(dated) > 1 else float("inf")
    if closest_distance <= 120 and runner_up_distance - closest_distance >= 180:
        closest["date_recommended"] = True
        diagnosis["date_recommended_candidate_path"] = str(
            closest.get("path") or closest.get("series_path") or ""
        )
        diagnosis["date_recommended_distance_days"] = closest_distance

    if closest_distance > 550:
        diagnosis["candidate_date_conflict"] = True
        diagnosis["source_release_year"] = datetime.fromtimestamp(source_published_at, timezone.utc).year
        diagnosis["candidate_file_years"] = sorted({
            datetime.fromtimestamp(
                _parse_timestamp(_normalized_file_info(candidate.get("file_info")).get("timestamp")),
                timezone.utc,
            ).year
            for candidate in candidates
        })
        diagnosis["date_rejected_candidate_count"] = len(candidates)
        for candidate in candidates:
            candidate["selectable"] = False

    prepared["diagnosis"] = diagnosis
    prepared["candidates"] = candidates
    prepared["recommended_action"] = _review_recommended_action(prepared)
    prepared["batch_eligible"] = bool(_safe_batch_review_body(prepared))
    return prepared


def _enrich_target_review_source_timing(
    item: dict[str, Any],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Backfill source/qB times for old target reviews from read-only state.

    New Worker reviews persist these values directly.  Historical review rows
    predate that contract, so the detail endpoint recovers them from the
    durable extraction job and pending-item snapshots without modifying either
    database.
    """

    if str(item.get("kind") or "") != "target_ambiguity":
        return item
    prepared = dict(item)
    diagnosis = dict(prepared.get("diagnosis") or {})
    times = {
        "source_published_at": _parse_timestamp(diagnosis.get("source_published_at")),
        "torrent_created_at": _parse_timestamp(diagnosis.get("torrent_created_at")),
        "torrent_added_at": _parse_timestamp(diagnosis.get("torrent_added_at")),
        "torrent_completed_at": _parse_timestamp(diagnosis.get("torrent_completed_at")),
    }
    source_published_precision = str(diagnosis.get("source_published_precision") or "")
    if all(value > 0 for value in times.values()):
        diagnosis.update(times)
        diagnosis["source_published_precision"] = source_published_precision
        prepared["diagnosis"] = diagnosis
        return _apply_target_review_date_guidance(prepared)

    torrent_hash = str(diagnosis.get("torrent_hash") or "").strip().casefold()
    torrent_name = str(diagnosis.get("torrent_name") or "").strip()
    bangumi_ids = sorted({
        value
        for raw in diagnosis.get("bangumi_ids") or []
        if (value := _coerce_int(raw)) is not None
    })
    episode = _coerce_int(diagnosis.get("episode"))
    pending_entries: list[dict[str, Any]] = []
    torrent_payloads: list[dict[str, Any]] = []
    db_path = _mikan_state_db_path(config)
    if db_path.is_file():
        try:
            with _sqlite_connect(db_path, readonly=True) as connection:
                if _sqlite_table_exists(connection, "mikan_extract_jobs"):
                    columns = {
                        str(row[1])
                        for row in connection.execute("PRAGMA table_info(mikan_extract_jobs)").fetchall()
                    }
                    if {"torrent_json", "pending_entries_json"}.issubset(columns):
                        conditions: list[str] = []
                        parameters: list[Any] = []
                        if torrent_hash and "torrent_hash" in columns:
                            conditions.append("torrent_hash = ?")
                            parameters.append(torrent_hash)
                        if torrent_name and "torrent_name" in columns:
                            conditions.append("torrent_name = ? COLLATE NOCASE")
                            parameters.append(torrent_name)
                        if conditions:
                            order_column = "updated_at" if "updated_at" in columns else "rowid"
                            rows = connection.execute(
                                f"""
                                SELECT torrent_json, pending_entries_json
                                FROM mikan_extract_jobs
                                WHERE {' OR '.join(conditions)}
                                ORDER BY {order_column} DESC
                                LIMIT 20
                                """,
                                parameters,
                            ).fetchall()
                            for torrent_json, entries_json in rows:
                                payload = _json_object(torrent_json)
                                if payload:
                                    torrent_payloads.append(payload)
                                pending_entries.extend(
                                    entry
                                    for entry in _json_list(entries_json)
                                    if isinstance(entry, dict)
                                )

                if _sqlite_table_exists(connection, "mikan_download_items"):
                    columns = {
                        str(row[1])
                        for row in connection.execute("PRAGMA table_info(mikan_download_items)").fetchall()
                    }
                    if "raw_json" in columns:
                        conditions = []
                        parameters = []
                        if torrent_hash and "last_qbit_hash" in columns:
                            conditions.append("last_qbit_hash = ?")
                            parameters.append(torrent_hash)
                        if torrent_name and "last_qbit_name" in columns:
                            conditions.append("last_qbit_name = ? COLLATE NOCASE")
                            parameters.append(torrent_name)
                        # A bangumi/episode lookup is only a last resort.  When
                        # an exact torrent identity exists, mixing in every
                        # historical release for the same episode could show a
                        # different torrent's publication date.
                        if not conditions and bangumi_ids and "bangumi_id" in columns:
                            placeholders = ",".join("?" for _ in bangumi_ids)
                            bangumi_condition = f"bangumi_id IN ({placeholders})"
                            bangumi_parameters: list[Any] = list(bangumi_ids)
                            if episode is not None and "episode" in columns:
                                bangumi_condition += " AND episode = ?"
                                bangumi_parameters.append(episode)
                            conditions.append(f"({bangumi_condition})")
                            parameters.extend(bangumi_parameters)
                        if conditions:
                            order_column = "updated_at" if "updated_at" in columns else "rowid"
                            rows = connection.execute(
                                f"""
                                SELECT raw_json
                                FROM mikan_download_items
                                WHERE {' OR '.join(conditions)}
                                ORDER BY {order_column} DESC
                                LIMIT 100
                                """,
                                parameters,
                            ).fetchall()
                            pending_entries.extend(
                                entry
                                for (raw_json,) in rows
                                if (entry := _json_object(raw_json))
                            )
        except (OSError, sqlite3.Error):
            # Timing is decision support, not permission to weaken the safety
            # block.  A busy or old database should leave explicit unknowns.
            pass

    for payload in torrent_payloads:
        times["torrent_created_at"] = max(
            times["torrent_created_at"],
            _parse_timestamp(payload.get("creation_date")),
        )
        times["torrent_added_at"] = max(
            times["torrent_added_at"],
            _parse_timestamp(payload.get("added_on")),
        )
        times["torrent_completed_at"] = max(
            times["torrent_completed_at"],
            _parse_timestamp(payload.get("completion_on")),
        )
    for entry in pending_entries:
        times["torrent_added_at"] = max(
            times["torrent_added_at"],
            _parse_timestamp(entry.get("last_qbit_added_on")),
        )
        times["torrent_completed_at"] = max(
            times["torrent_completed_at"],
            _parse_timestamp(entry.get("last_qbit_completion_on")),
        )
    times["source_published_at"] = max(
        times["source_published_at"],
        _review_source_publication_timestamp(pending_entries),
    )
    recovered_precision = _review_source_publication_precision(pending_entries)
    if recovered_precision:
        source_published_precision = recovered_precision
    if times["torrent_created_at"] <= 0 and torrent_hash:
        times["torrent_created_at"] = _qbit_torrent_creation_timestamp(config, torrent_hash)
    diagnosis.update(times)
    diagnosis["source_published_precision"] = source_published_precision
    diagnosis["source_timing_available"] = any(value > 0 for value in times.values())
    prepared["diagnosis"] = diagnosis
    return _apply_target_review_date_guidance(prepared)


def _enrich_review_candidates(candidates: list[dict[str, Any]], *, config: dict[str, Any]) -> list[dict[str, Any]]:
    database = _series_metadata_db_path(config)
    if not database.exists() or not candidates:
        return candidates
    try:
        with _sqlite_connect(database, readonly=True) as connection:
            connection.row_factory = sqlite3.Row
            if not _sqlite_table_exists(connection, "series_profiles"):
                return candidates
            enriched: list[dict[str, Any]] = []
            for candidate in candidates:
                payload = dict(candidate)
                raw_path = str(payload.get("path") or payload.get("series_path") or "").strip()
                try:
                    _video, series_path, _season = _review_candidate_selection(raw_path)
                except HTTPException:
                    enriched.append(payload)
                    continue
                row = connection.execute(
                    "SELECT * FROM series_profiles WHERE local_path_key=?",
                    (str(series_path).casefold(),),
                ).fetchone()
                if row is not None:
                    profile = _public_series_profile(dict(row))
                    payload.setdefault("series_id", profile.get("series_id"))
                    payload.setdefault("series_title", profile.get("canonical_title"))
                    payload.setdefault("source_id", profile.get("mikan_bangumi_id"))
                    payload["artwork_available"] = bool(profile.get("cover_image_cache_key"))
                enriched.append(payload)
            return enriched
    except sqlite3.Error:
        return candidates


def _review_artwork_url(item: dict[str, Any], *, config: dict[str, Any]) -> str:
    for candidate in item.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        series_id = str(candidate.get("series_id") or "")
        if bool(candidate.get("artwork_available")) and _series_artwork_path(series_id, config=config) is not None:
            return f"/api/v2/series/{series_id}/artwork"

    target = str((item.get("diagnosis") or {}).get("video") or item.get("target_key") or "").strip()
    if not target:
        return ""
    try:
        _video, series_path, _season = _review_candidate_selection(target)
    except HTTPException:
        return ""
    database = _series_metadata_db_path(config)
    if not database.exists():
        return ""
    try:
        with _sqlite_connect(database, readonly=True) as connection:
            connection.row_factory = sqlite3.Row
            if not _sqlite_table_exists(connection, "series_profiles"):
                return ""
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(series_profiles)").fetchall()}
            if not {"series_id", "cover_image_cache_key"}.issubset(columns):
                return ""
            row = connection.execute(
                "SELECT series_id FROM series_profiles WHERE local_path_key=? AND cover_image_cache_key<>''",
                (str(series_path).casefold(),),
            ).fetchone()
    except sqlite3.Error:
        return ""
    series_id = str(row["series_id"] or "") if row is not None else ""
    return f"/api/v2/series/{series_id}/artwork" if _series_artwork_path(series_id, config=config) is not None else ""


@app.get("/api/v2/review-items/{review_id}")
def v2_review_item_detail(review_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"review_[0-9a-f]{24}", str(review_id or "")):
        raise HTTPException(status_code=400, detail="Invalid review id")
    config = _load_config()
    database = _control_state_db_path(config)
    item = read_review(database, review_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item does not exist")
    item = _enrich_target_review_source_timing(
        _prepare_review_item(item, config=config),
        config=config,
    )
    command = review_command_states(database, [review_id], inbox=_control_inbox_dir(config)).get(review_id)
    queue_states = review_queue_states(
        WORK_PATH / "scanner_state.sqlite3",
        [str((command or {}).get("target") or "")],
    )
    _attach_review_action_state(
        item,
        command,
        queue_state=queue_states.get(str((command or {}).get("target") or ""), {}),
    )
    return {"item": item, "revision": _v2_revision()}


def _review_candidate_selection(candidate_path: str) -> tuple[str, str, int]:
    """Return a normalized candidate video, its series root and season."""

    normalized = _validated_anime_path_text(candidate_path)
    if normalized.startswith("/"):
        candidate = PurePosixPath(normalized)
        season_directory = candidate.parent.name
        series_path = candidate.parent.parent.as_posix()
    else:
        candidate = Path(normalized)
        season_directory = candidate.parent.name
        series_path = str(candidate.parent.parent)
    match = re.fullmatch(r"Season\s+(\d+)", season_directory, flags=re.IGNORECASE)
    if match:
        season = int(match.group(1))
    elif season_directory.casefold() == "specials":
        season = 0
    else:
        raise HTTPException(
            status_code=400,
            detail="Review candidate must be a video inside Season N or Specials",
        )
    return normalized, _validated_anime_path_text(series_path), season


@app.post("/api/v2/review-items/{review_id}/resolve", status_code=202)
async def v2_resolve_review_item(review_id: str, request: Request) -> dict[str, Any]:
    if not re.fullmatch(r"review_[0-9a-f]{24}", str(review_id or "")):
        raise HTTPException(status_code=400, detail="Invalid review id")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    headers = getattr(request, "headers", {})
    idempotency_key = str(headers.get("idempotency-key", "") or payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="A valid Idempotency-Key is required")
    return _enqueue_review_resolution(review_id, payload, idempotency_key)


def _enqueue_review_resolution(
    review_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    remediation = str(payload.get("action") or payload.get("remediation") or "").strip().casefold()
    config = _load_config()
    control_database = _control_state_db_path(config)
    existing = read_command(control_database, stable_id("cmd", idempotency_key))
    if existing is not None:
        return existing
    review = read_review(control_database, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review item does not exist")
    if str(review.get("status") or "") != "open":
        raise HTTPException(status_code=409, detail="Review item is no longer open")
    if remediation == "review.dismiss":
        if str(review.get("kind") or "") != "target_ambiguity":
            raise HTTPException(
                status_code=400,
                detail="Only source pairing reviews can be dismissed",
            )
        return enqueue_atomic_command(
            config=config,
            work_path=_configured_work_path(config),
            expand=_expand_config_env,
            action="review.dismiss",
            target=review_id,
            parameters={"review_id": review_id},
            idempotency_key=idempotency_key,
        )
    if remediation in {"ai.retranslate", "ai.retranscribe", "ai.retranslate_lines"}:
        if str(review.get("kind") or "") not in {"subtitle_quality", "asr_quality"}:
            raise HTTPException(status_code=400, detail="Review item is not an AI quality review")
        target = str(payload.get("target") or payload.get("video") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="AI quality review resolution requires a video target")
        target = _validated_anime_path_text(target)
        diagnosed_target = str((review.get("diagnosis") or {}).get("video") or review.get("target_key") or "").strip()
        if diagnosed_target and _validated_anime_path_text(diagnosed_target) != target:
            raise HTTPException(status_code=400, detail="AI quality target does not match this review item")
        parameters: dict[str, Any] = {"review_id": review_id, "remediation": remediation}
        if remediation == "ai.retranslate_lines":
            if str(review.get("kind") or "") != "subtitle_quality":
                raise HTTPException(status_code=400, detail="Only translation reviews support line repair")
            raw_indexes = payload.get("indexes")
            if not isinstance(raw_indexes, list):
                raise HTTPException(status_code=400, detail="Line repair requires subtitle indexes")
            try:
                indexes = sorted({int(value) for value in raw_indexes if int(value) > 0})
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Invalid subtitle indexes") from exc
            allowed_indexes = set(_review_issue_indexes(_prepare_review_item(review, config=config)))
            if not indexes or len(indexes) > 500 or not set(indexes).issubset(allowed_indexes):
                raise HTTPException(status_code=400, detail="Line repair must use indexes reported by this review")
            parameters["lines"] = ",".join(str(value) for value in indexes)
        return enqueue_atomic_command(
            config=config,
            work_path=_configured_work_path(config),
            expand=_expand_config_env,
            action="review.resolve_ai",
            target=target,
            parameters=parameters,
            idempotency_key=idempotency_key,
        )

    if str(review.get("kind") or "") != "target_ambiguity":
        raise HTTPException(status_code=400, detail="Review item is not a target ambiguity")

    if remediation == "target.auto_rebuild_candidates":
        return enqueue_atomic_command(
            config=config,
            work_path=_configured_work_path(config),
            expand=_expand_config_env,
            action="review.auto_rebuild_target_candidates",
            target=review_id,
            parameters={"review_id": review_id},
            idempotency_key=idempotency_key,
        )

    if remediation == "target.rebuild_candidates":
        series_id = str(payload.get("series_id") or "").strip()
        if not re.fullmatch(r"series_[0-9a-f]{24}", series_id):
            raise HTTPException(status_code=400, detail="A valid stable series_id is required")
        try:
            season = int(payload.get("season"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="A numeric season is required") from exc
        if not 0 <= season <= 99:
            raise HTTPException(status_code=400, detail="Season must be between 0 and 99")
        return enqueue_atomic_command(
            config=config,
            work_path=_configured_work_path(config),
            expand=_expand_config_env,
            action="review.rebuild_target_candidates",
            target=review_id,
            parameters={
                "review_id": review_id,
                "series_id": series_id,
                "season": season,
            },
            idempotency_key=idempotency_key,
        )

    source_id = payload.get("source_id") or payload.get("bangumi_id")
    if source_id in (None, "") or not str(source_id).strip().isdigit():
        raise HTTPException(status_code=400, detail="A numeric bangumi_id/source_id is required")
    normalized_source_id = str(int(str(source_id).strip()))
    diagnosed_ids = {
        str(int(str(value).strip()))
        for value in (review.get("diagnosis") or {}).get("bangumi_ids", [])
        if str(value).strip().isdigit()
    }
    if diagnosed_ids and normalized_source_id not in diagnosed_ids:
        raise HTTPException(status_code=400, detail="Source id is not part of this review item")

    prepared_review = _enrich_target_review_source_timing(
        _prepare_review_item(review, config=config),
        config=config,
    )
    candidates: list[tuple[str, str, int]] = []
    for candidate in prepared_review.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if not _review_candidate_has_semantic_evidence(candidate):
            continue
        raw_path = str(candidate.get("path") or candidate.get("series_path") or "").strip()
        if not raw_path:
            continue
        try:
            selection = _review_candidate_selection(raw_path)
        except HTTPException:
            continue
        if selection not in candidates:
            candidates.append(selection)

    requested_candidate = str(payload.get("candidate_path") or "").strip()
    selected: tuple[str, str, int] | None = None
    if requested_candidate:
        normalized_candidate = _validated_anime_path_text(requested_candidate)
        selected = next((item for item in candidates if item[0] == normalized_candidate), None)
    else:
        # Backward compatibility for already-open older clients is safe only
        # when series root plus season identifies exactly one stored candidate.
        legacy_series_path = str(payload.get("series_path") or "").strip()
        try:
            legacy_season = int(payload.get("season") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid review season") from exc
        if legacy_series_path:
            normalized_series_path = _validated_anime_path_text(legacy_series_path)
            matches = [
                item for item in candidates
                if item[1] == normalized_series_path and item[2] == legacy_season
            ]
            if len(matches) == 1:
                selected = matches[0]
    if selected is None:
        raise HTTPException(
            status_code=400,
            detail="Select one exact candidate video from this review item",
        )

    candidate_path, series_path, season = selected
    parameters = {
        "review_id": review_id,
        "candidate_path": candidate_path,
        "series_path": series_path,
        "source_id": normalized_source_id,
        "season": season,
        "series_id": str(payload.get("series_id") or ""),
    }
    return enqueue_atomic_command(
        config=config,
        work_path=_configured_work_path(config),
        expand=_expand_config_env,
        action="review.resolve_target",
        target=review_id,
        parameters=parameters,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v2/review-items/batch-resolve", status_code=202)
async def v2_batch_resolve_review_items(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    raw_ids = payload.get("review_ids")
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="review_ids must be an array")
    review_ids = list(dict.fromkeys(str(value or "").strip() for value in raw_ids))
    if not review_ids or len(review_ids) > 100 or any(
        not re.fullmatch(r"review_[0-9a-f]{24}", value) for value in review_ids
    ):
        raise HTTPException(status_code=400, detail="Batch review ids are invalid")
    headers = getattr(request, "headers", {})
    idempotency_key = str(headers.get("idempotency-key", "") or payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="A valid Idempotency-Key is required")
    requested_action = str(payload.get("action") or "safe.default").strip().casefold()
    config = _load_config()
    database = _control_state_db_path(config)
    commands = review_command_states(database, review_ids, inbox=_control_inbox_dir(config))
    queue_states = review_queue_states(
        WORK_PATH / "scanner_state.sqlite3",
        [str(command.get("target") or "") for command in commands.values()],
    )
    queued: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    prepared_batch: list[tuple[str, dict[str, Any], str]] = []
    for review_id in review_ids:
        review = read_review(database, review_id)
        if review is None:
            rejected.append({"review_id": review_id, "reason": "審核項目不存在"})
            continue
        prepared = _prepare_review_item(review, config=config)
        command = commands.get(review_id)
        _attach_review_action_state(
            prepared,
            command,
            queue_state=queue_states.get(str((command or {}).get("target") or ""), {}),
        )
        body = _safe_batch_review_body(prepared)
        if body is None:
            rejected.append({"review_id": review_id, "reason": "此項目仍需人工選擇，未執行"})
            continue
        action = str(body.get("action") or "target.confirm_candidate").casefold()
        if requested_action not in {"safe.default", action}:
            rejected.append({"review_id": review_id, "reason": "修復方式與這批項目不同"})
            continue
        prepared_batch.append((review_id, body, action))

    action_groups = {action for _review_id, _body, action in prepared_batch}
    if len(action_groups) > 1:
        rejected.extend(
            {"review_id": review_id, "reason": "不同修復方式不能放在同一批，未執行"}
            for review_id, _body, _action in prepared_batch
        )
        return {
            "queued": [],
            "rejected": rejected,
            "queued_count": 0,
            "rejected_count": len(rejected),
            "action": requested_action,
        }

    action_group = next(iter(action_groups), "")
    for review_id, body, action in prepared_batch:
        try:
            command = _enqueue_review_resolution(
                review_id,
                body,
                f"{idempotency_key}:{review_id}:{action}",
            )
        except HTTPException as exc:
            rejected.append({"review_id": review_id, "reason": str(exc.detail)})
            continue
        queued.append(
            {
                "review_id": review_id,
                "command_id": str(command.get("command_id") or ""),
                "status": str(command.get("status") or "accepted"),
            }
        )
    return {
        "queued": queued,
        "rejected": rejected,
        "queued_count": len(queued),
        "rejected_count": len(rejected),
        "action": action_group or requested_action,
    }


@app.get("/api/v2/series/{series_id}/artwork")
def v2_series_artwork(series_id: str) -> FileResponse:
    path = _series_artwork_path(series_id, config=_load_config())
    if path is None:
        raise HTTPException(status_code=404, detail="Series artwork is not available")
    media_types = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    return FileResponse(
        str(path),
        media_type=media_types[path.suffix.casefold()],
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/api/v2/series/{series_id}")
def v2_series_detail(series_id: str) -> dict[str, Any]:
    return _series_profile_detail_by_id(series_id)


@app.get("/api/v2/stream")
def v2_stream() -> StreamingResponse:
    def generate():
        previous: dict[str, Any] = {}
        last_heartbeat = 0.0
        while True:
            snapshot = _stream_state_version()
            changed = _v2_changed_entities(previous, snapshot)
            now = time.monotonic()
            if changed != ["heartbeat"] or now - last_heartbeat >= 15.0:
                previous = snapshot
                last_heartbeat = now
                payload = {
                    "revision": _v2_revision(snapshot),
                    "changed": changed,
                    "at": time.time(),
                }
                yield f"event: revision\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
            time.sleep(1.0)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/dashboard/summary")
def dashboard_summary() -> dict[str, Any]:
    return _dashboard_summary()


@app.get("/api/health")
def health() -> dict[str, Any]:
    config = _load_config()
    return {
        "now": int(time.time()),
        "version": _version_summary(),
        "health": _health_summary(config, fast=False),
        "worker": _worker_summary(),
        "paths": {
            "config": str(CONFIG_PATH),
            "work": str(WORK_PATH),
            "logs": str(LOG_PATH),
            "docker_socket": str(DOCKER_SOCKET),
        },
    }


@app.get("/api/queue")
def queue(status_filter: str | None = None, search: str = "", limit: int = 50) -> dict[str, Any]:
    config = _load_config()
    return _queue_summary(
        status_filter=status_filter or None,
        search=search,
        limit=max(1, min(int(limit), 200)),
        stale_running_seconds=_config_stale_running_seconds(config),
        max_concurrent_videos=_config_max_concurrent_videos(config),
    )


@app.get("/api/mikan/downloads")
def mikan_downloads(
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
    search: str = "",
    compact: bool = False,
) -> dict[str, Any]:
    config = _load_config()
    payload = _mikan_downloads_summary(
        config,
        page=max(1, int(page)),
        page_size=max(1, min(int(page_size), 100)),
        status_filter=status_filter,
        search=search,
    )
    return _compact_mikan_downloads_payload(payload) if compact else payload


@app.get("/api/workflow")
def workflow() -> dict[str, Any]:
    task_payload = _workflow_tasks_summary(limit=500)
    return {
        "nodes": _workflow_nodes(),
        "edges": _workflow_edges(),
        "stats": task_payload["stats"],
        "updated_at": time.time(),
    }


@app.get("/api/workflow/tasks")
def workflow_tasks(limit: int = 200) -> dict[str, Any]:
    return _workflow_tasks_summary(limit=max(1, min(int(limit), 500)))


@app.get("/api/dashboard/tasks")
def dashboard_tasks(
    limit: int = 160,
    status_filter: str | None = None,
    search: str = "",
    page: int = 1,
    page_size: int | None = None,
    mode: str = "all",
) -> dict[str, Any]:
    config = _load_config()
    return _dashboard_tasks_summary(
        limit=max(1, min(int(limit), 300)),
        status_filter=status_filter or None,
        search=search,
        page=max(1, int(page)),
        page_size=max(1, min(int(page_size), 100)) if page_size is not None else None,
        mode=mode,
        stale_running_seconds=_config_stale_running_seconds(config),
        max_concurrent_videos=_config_max_concurrent_videos(config),
    )


@app.post("/api/queue/actions/{action}")
async def queue_action(action: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    path = str(payload.get("path", "")).strip()
    if not path:
        raise HTTPException(status_code=400, detail="Missing queue path")
    normalized_action = str(action or "").strip().casefold()
    worker_action = LEGACY_QUEUE_COMMAND_ACTIONS.get(normalized_action)
    if worker_action is None:
        raise HTTPException(status_code=404, detail=f"Unknown queue action: {action}")
    parameters: dict[str, Any] = {}
    if normalized_action == "retranslate-lines":
        lines = str(payload.get("lines", "")).strip()
        if not re.fullmatch(r"\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*", lines):
            raise HTTPException(status_code=400, detail="Invalid subtitle line indexes")
        parameters["lines"] = lines
    target = _validated_anime_path_text(path)
    return _enqueue_legacy_worker_command(
        action=worker_action,
        target=target,
        parameters=parameters,
        request=request,
        scope=f"queue:{normalized_action}",
    )


@app.get("/api/series")
def series_profiles(page: int = 1, page_size: int = 40, search: str = "") -> dict[str, Any]:
    return _series_profiles_summary(
        page=max(1, int(page)),
        page_size=max(1, min(100, int(page_size))),
        search=search.strip(),
    )


@app.get("/api/series/detail")
def series_profile_detail(path: str) -> dict[str, Any]:
    return _series_profile_detail(path)


@app.post("/api/series/lock")
async def set_series_profile_lock(request: Request) -> dict[str, Any]:
    payload = await request.json()
    path = str(payload.get("path", "")).strip() if isinstance(payload, dict) else ""
    if not path:
        raise HTTPException(status_code=400, detail="Missing series path")
    locked = bool(payload.get("locked"))
    return _enqueue_legacy_worker_command(
        action="series.lock",
        target=_validated_anime_path_text(path),
        parameters={"locked": locked},
        request=request,
        scope="series-lock",
    )


@app.post("/api/series/match")
async def set_series_profile_match(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    path = str(payload.get("path", "")).strip()
    provider = str(payload.get("provider", "anilist")).strip()
    provider_id = str(payload.get("provider_id", "")).strip()
    title = str(payload.get("title", "")).strip()
    if not path or not provider_id or not title:
        raise HTTPException(status_code=400, detail="path, provider_id and title are required")
    return _enqueue_legacy_worker_command(
        action="series.match",
        target=_validated_anime_path_text(path),
        parameters={"provider": provider, "provider_id": provider_id, "title": title},
        request=request,
        scope="series-match",
    )


@app.post("/api/series/glossary")
async def upsert_series_glossary(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    path = str(payload.get("path", "")).strip()
    source_text = str(payload.get("source_text", "")).strip()
    target_text = str(payload.get("target_text", "")).strip()
    term_type = str(payload.get("term_type", "term")).strip() or "term"
    if not path or not source_text:
        raise HTTPException(status_code=400, detail="path and source_text are required")
    return _enqueue_legacy_worker_command(
        action="series.glossary_upsert",
        target=_validated_anime_path_text(path),
        parameters={"source_text": source_text, "target_text": target_text, "term_type": term_type},
        request=request,
        scope="series-glossary-upsert",
    )


@app.post("/api/series/glossary/delete")
async def delete_series_glossary(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    path = str(payload.get("path", "")).strip()
    source_text = str(payload.get("source_text", "")).strip()
    if not path or not source_text:
        raise HTTPException(status_code=400, detail="path and source_text are required")
    return _enqueue_legacy_worker_command(
        action="series.glossary_delete",
        target=_validated_anime_path_text(path),
        parameters={"source_text": source_text},
        request=request,
        scope="series-glossary-delete",
    )


@app.get("/api/ai/diagnostics")
def ai_diagnostics(path: str) -> dict[str, Any]:
    return _ai_diagnostics_for_video(path)


@app.get("/api/ai/control")
def ai_control() -> dict[str, Any]:
    return _ai_control_summary()


@app.post("/api/ai/control/{action}")
def set_ai_control(action: str) -> dict[str, Any]:
    normalized = str(action or "").strip().casefold()
    if normalized not in {"pause", "resume"}:
        raise HTTPException(status_code=404, detail=f"Unknown AI control action: {action}")
    return _enqueue_legacy_worker_command(
        action="system.ai_queue_pause" if normalized == "pause" else "system.ai_queue_resume",
        scope=f"ai-control:{normalized}",
    )


@app.post("/api/mikan/redownload/cancel")
def cancel_mikan_redownload() -> dict[str, Any]:
    return _enqueue_legacy_worker_command(
        action="mikan.cancel_redownload",
        scope="mikan-cancel-redownload",
    )


@app.post("/api/config/presets/{preset}")
def apply_config_preset(preset: str) -> dict[str, Any]:
    values = CONFIG_PRESETS.get(preset)
    if values is None:
        raise HTTPException(status_code=404, detail=f"Unknown preset: {preset}")
    config = _load_config()
    changed = {key: value for key, value in values.items() if config.get(key) != value}
    if changed:
        config.update(changed)
        _save_config(config)
    return {"preset": preset, "changed": changed, "restart_recommended": bool(changed)}


@app.get("/api/events")
def events(limit: int = 50, include_counts: bool = True) -> dict[str, Any]:
    return _events_summary(limit=max(1, min(int(limit), 200)), include_counts=include_counts)


@app.get("/api/v2/events")
def v2_events(
    cursor: str | None = None,
    limit: int = 40,
    attention_only: bool = False,
    detail: bool = False,
) -> dict[str, Any]:
    # Fifty compact rows keep the uncompressed response below the 20 KiB
    # mobile budget while cursor pagination still exposes the full history.
    page_size = max(1, min(50, int(limit)))
    offset = min(_decode_cursor(cursor), 5000)
    fetch_limit = min(
        1000,
        max(offset + page_size + 1, (offset + page_size) * (5 if attention_only else 1)),
    )
    summary = _events_summary(limit=fetch_limit, include_counts=False)
    rows = list(summary.get("recent") or [])
    if attention_only:
        rows = [row for row in rows if str(row.get("severity") or "") in {"danger", "warn"}]
    selected = rows[offset : offset + page_size]
    items = [_v2_event_payload(row, detail=bool(detail)) for row in selected]
    next_offset = offset + len(selected)
    return {
        "items": items,
        "next_cursor": _encode_cursor(next_offset) if next_offset < len(rows) else None,
        "revision": _v2_revision(),
    }


@app.get("/api/stream")
def stream() -> StreamingResponse:
    def generate():
        last_payload = ""
        last_heartbeat = time.monotonic()
        while True:
            payload = _stream_state_version()
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if encoded != last_payload:
                last_payload = encoded
                last_heartbeat = time.monotonic()
                yield f"event: state\ndata: {encoded}\n\n"
            elif time.monotonic() - last_heartbeat >= 15.0:
                # SSE comments keep proxies and mobile browsers connected
                # without telling the frontend that application state changed.
                last_heartbeat = time.monotonic()
                yield ": heartbeat\n\n"
            time.sleep(1.0)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/config")
def config_fields() -> dict[str, Any]:
    config = _load_config()
    return {
        "path": str(CONFIG_PATH),
        "fields": [
            {"key": key, "value": config.get(key), **meta}
            for key, meta in EDITABLE_FIELDS.items()
        ],
    }


@app.patch("/api/config")
async def update_config(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    values = payload.get("values", payload)
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="Expected values object")

    config = _load_config()
    changed: dict[str, Any] = {}
    for key, raw_value in values.items():
        if key not in EDITABLE_FIELDS:
            raise HTTPException(status_code=400, detail=f"Field is not editable: {key}")
        coerced = _coerce_value(key, raw_value)
        if config.get(key) != coerced:
            config[key] = coerced
            changed[key] = coerced

    if changed:
        _save_config(config)
    return {"changed": changed, "restart_recommended": bool(changed)}


@app.get("/api/logs/{name}")
def logs(name: str, lines: int = 200) -> dict[str, Any]:
    if name not in {"app", "failed"}:
        raise HTTPException(status_code=404, detail="Unknown log")
    filename = "app.log" if name == "app" else "failed.log"
    return {
        "name": name,
        "path": str(LOG_PATH / filename),
        "content": _tail_file(LOG_PATH / filename, max(1, min(lines, 2000))),
    }


@app.post("/api/actions/restart-worker")
def restart_worker() -> dict[str, Any]:
    return _start_restart_worker_action()


@app.post("/api/actions/{action}")
def run_action(action: str) -> dict[str, Any]:
    worker_action = LEGACY_BACKGROUND_COMMAND_ACTIONS.get(str(action or "").strip().casefold())
    if worker_action is not None:
        parameters = {"delete_files": False} if worker_action == "mikan.request_redownload_all" else {}
        return _enqueue_legacy_worker_command(
            action=worker_action,
            parameters=parameters,
            scope=f"action:{action}",
        )
    command = ACTION_COMMANDS.get(action)
    if command is None:
        raise HTTPException(status_code=404, detail="Unknown action")
    return _start_background_action(action, command)


@app.get("/api/actions/status")
def action_status() -> dict[str, Any]:
    return _action_snapshot()


def _start_background_action(action: str, command: list[str]) -> dict[str, Any]:
    _claim_background_action(action)
    thread = threading.Thread(
        target=_run_background_action,
        args=(action, command, _background_action_timeout_seconds(action)),
        daemon=True,
        name=f"webui-action-{action}",
    )
    try:
        thread.start()
    except RuntimeError as exc:
        _finish_background_action(ok=False, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to start action: {exc}") from exc
    return {"ok": True, "started": True, "action": action, "action_state": _action_snapshot()}


def _start_restart_worker_action() -> dict[str, Any]:
    action = "restart-worker"
    _claim_background_action(action)
    thread = threading.Thread(
        target=_run_restart_worker_action,
        daemon=True,
        name="webui-action-restart-worker",
    )
    try:
        thread.start()
    except RuntimeError as exc:
        _finish_background_action(ok=False, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to start action: {exc}") from exc
    return {"ok": True, "started": True, "action": action, "action_state": _action_snapshot()}


def _claim_background_action(action: str) -> None:
    with ACTION_LOCK:
        if ACTION_STATE["running"]:
            raise HTTPException(status_code=409, detail=f"Action already running: {ACTION_STATE['action']}")
        ACTION_STATE.update(
            {
                "running": True,
                "action": action,
                "started_at": time.time(),
                "finished_at": None,
                "ok": None,
                "output": "",
                "error": "",
            }
        )

def _run_background_action(action: str, command: list[str], timeout_seconds: float) -> None:
    try:
        output = _docker_exec(WORKER_CONTAINER_NAME, command, timeout_seconds=timeout_seconds)
        _finish_background_action(ok=True, output=output[-12000:])
    except Exception as exc:  # noqa: BLE001 - keep action failures visible in the UI.
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _finish_background_action(ok=False, error=_format_background_action_error(action, str(detail)))


def _run_restart_worker_action() -> None:
    try:
        request_timeout = max(
            DOCKER_API_TIMEOUT_SECONDS,
            float(DOCKER_RESTART_STOP_TIMEOUT_SECONDS) + 20.0,
        )
        _docker_request(
            "POST",
            f"/containers/{WORKER_CONTAINER_NAME}/restart?t={DOCKER_RESTART_STOP_TIMEOUT_SECONDS}",
            timeout_seconds=request_timeout,
        )
        _WORKER_SUMMARY_CACHE.clear()
        _finish_background_action(ok=True, output=f"Restarted {WORKER_CONTAINER_NAME}")
    except Exception as exc:  # noqa: BLE001 - keep action failures visible in the UI.
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _finish_background_action(ok=False, error=str(detail))


def _finish_background_action(*, ok: bool, output: str = "", error: str = "") -> None:
    with ACTION_LOCK:
        ACTION_STATE.update(
            {
                "running": False,
                "finished_at": time.time(),
                "ok": bool(ok),
                "output": output,
                "error": error,
            }
        )


def _background_action_timeout_seconds(action: str) -> float:
    if action == "ai-refresh-queue-state":
        return DOCKER_EXEC_TIMEOUT_SECONDS
    if action in {
        "retry-all-failures",
        "mikan-process-completed",
        "mikan-requeue-failed-extracts",
        "refresh-ass",
        "cleanup-generated",
        "backup-state",
    }:
        return SHORT_ACTION_EXEC_TIMEOUT_SECONDS
    return DOCKER_EXEC_TIMEOUT_SECONDS


def _action_snapshot() -> dict[str, Any]:
    with ACTION_LOCK:
        snapshot = dict(ACTION_STATE)
    if not snapshot.get("running") and snapshot.get("finished_at"):
        try:
            finished_at = float(snapshot["finished_at"])
        except (TypeError, ValueError):
            finished_at = 0.0
        if finished_at and time.time() - finished_at > ACTION_RESULT_DISPLAY_SECONDS:
            return {
                "running": False,
                "action": None,
                "started_at": None,
                "finished_at": None,
                "ok": None,
                "output": "",
                "error": "",
            }
    if snapshot.get("started_at"):
        try:
            end_time = time.time() if snapshot.get("running") else float(snapshot.get("finished_at") or time.time())
            snapshot["elapsed_seconds"] = max(0.0, end_time - float(snapshot["started_at"]))
        except (TypeError, ValueError):
            snapshot["elapsed_seconds"] = 0.0
    return snapshot


def _dashboard_summary() -> dict[str, Any]:
    config = _load_config()
    payload = status(lite=True)
    extract_jobs = _mikan_extract_jobs_summary_from_state_db(
        config,
        include_history=False,
        recent_limit=20,
    )
    state_db = payload.setdefault("mikan", {}).setdefault("state_db", {})
    if isinstance(state_db, dict):
        state_db["extract_jobs"] = extract_jobs
    payload["recent_completed"] = {
        "ai": _recent_ai_completed_summary(limit=8),
        "subtitle_extract": list(extract_jobs.get("recent_completed") or [])[:8],
    }
    payload["eta"] = _eta_summary()
    payload["failure_summary"] = _ai_failure_root_summary(config, extract_jobs=extract_jobs)
    payload["ai_failed_retry_sweep"] = read_auto_remediation_status(
        _control_state_db_path(config)
    )
    payload["database_health"] = _database_health_summary(config)
    payload["recommendations"] = _dashboard_recommendations(payload)
    payload["dashboard"] = {
        "task_page_size": 30,
        "downloads_page_size": 20,
        "events_limit": 20,
    }
    return payload


def _dashboard_recommendations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn runtime state into a short, actionable operator checklist."""

    recommendations: list[dict[str, Any]] = []
    queue_counts = payload.get("queue_counts") if isinstance(payload.get("queue_counts"), dict) else {}
    current_ai = payload.get("current_ai") if isinstance(payload.get("current_ai"), dict) else {}
    mikan = payload.get("mikan") if isinstance(payload.get("mikan"), dict) else {}
    state_db = mikan.get("state_db") if isinstance(mikan.get("state_db"), dict) else {}
    pipeline = state_db.get("pipeline") if isinstance(state_db.get("pipeline"), dict) else {}
    extract_jobs = state_db.get("extract_jobs") if isinstance(state_db.get("extract_jobs"), dict) else {}
    extract_counts = extract_jobs.get("counts") if isinstance(extract_jobs.get("counts"), dict) else {}
    ai_scheduler = payload.get("ai_scheduler") if isinstance(payload.get("ai_scheduler"), dict) else {}

    if ai_scheduler.get("problem"):
        retry_in = int(ai_scheduler.get("retry_in_seconds") or 0)
        recommendations.append({
            "key": "ai-scheduler",
            "tone": "danger",
            "title": "AI 排程器沒有正常讀取佇列",
            "detail": (
                f"Worker 會在 {retry_in} 秒內自動重試；也可以立即要求重試。"
                if retry_in > 0
                else "Worker 正在自動恢復；也可以立即要求重試。"
            ),
            "action": "ai-scheduler-retry",
        })

    if bool(current_ai.get("running_stale")):
        recommendations.append({
            "key": "stale-ai",
            "tone": "danger",
            "title": "AI 任務可能卡住",
            "detail": "開啟 AI 佇列確認心跳，必要時使用復原卡住任務。",
            "panel": "queue",
            "status_filter": "running",
        })

    paused = int(queue_counts.get("paused") or 0)
    if paused:
        recommendations.append({
            "key": "paused-ai-review",
            "tone": "warn",
            "title": f"{paused} 部 AI 字幕等待人工確認",
            "detail": "已停止自動重試，請選擇重翻譯、重新轉錄、重試或略過。",
            "panel": "queue",
            "status_filter": "paused",
        })

    ai_retryable = int(queue_counts.get("failed_retry") or 0)
    extract_retryable_value = (
        extract_jobs.get("retryable_count")
        if "retryable_count" in extract_jobs
        else extract_counts.get("failed")
    )
    extract_retryable = int(extract_retryable_value or 0)
    retryable = ai_retryable + extract_retryable
    if retryable:
        recommendations.append({
            "key": "retry-failures",
            "tone": "warn",
            "title": f"{retryable} 個失敗項目可重新處理",
            "detail": "批次重試只會重排失敗工作，不會中斷目前影片。",
            "action": "retry-all-failures",
            "retryable_count": retryable,
            "counts": {
                "ai_failed_retry": ai_retryable,
                "extract_retryable": extract_retryable,
            },
        })

    database_health = payload.get("database_health") if isinstance(payload.get("database_health"), dict) else {}
    database_rows = database_health.get("databases") if isinstance(database_health.get("databases"), list) else []
    reclaimable = [
        row for row in database_rows
        if isinstance(row, dict)
        and float(row.get("reclaim_mib") or 0) >= 64.0
        and float(row.get("freelist_ratio") or 0) >= 0.25
    ]
    if reclaimable:
        reclaim_mib = round(sum(float(row.get("reclaim_mib") or 0) for row in reclaimable), 1)
        recommendations.append({
            "key": "database-maintenance",
            "tone": "muted",
            "title": f"資料庫可回收約 {reclaim_mib:g} MiB",
            "detail": "系統會在閒置時自動備份並整理，也可以現在排入維護。",
            "action": "database-maintenance",
        })

    waiting_extract = int(pipeline.get("waiting_extract") or 0)
    extracting = int(pipeline.get("extracting") or 0)
    if waiting_extract and not extracting and not bool(mikan.get("busy")):
        recommendations.append({
            "key": "resume-extraction",
            "tone": "running",
            "title": f"{waiting_extract} 個字幕來源等待提取",
            "detail": "目前沒有提取工作，立即要求 Worker 對帳已完成下載。",
            "action": "mikan-process-completed",
        })

    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    checks = health.get("checks") if isinstance(health.get("checks"), list) else []
    backup_check = next(
        (item for item in checks if isinstance(item, dict) and item.get("name") == "state_backups"),
        None,
    )
    if isinstance(backup_check, dict) and "no backup created" in str(backup_check.get("detail") or "").casefold():
        recommendations.append({
            "key": "create-backup",
            "tone": "muted",
            "title": "尚未建立可驗證的狀態備份",
            "detail": "先備份 AI、Mikan 與作品資訊資料庫，之後才能安全維護。",
            "action": "backup-state",
        })

    return recommendations[:5]


def _ai_control_summary() -> dict[str, Any]:
    path = WORK_PATH / AI_CONTROL_NAME
    payload = _read_json_object(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "path": str(path),
        "exists": path.exists(),
        "paused": bool(payload.get("paused")),
        "updated_at": float(payload.get("updated_at") or mtime or 0),
        "requested_at": payload.get("requested_at"),
        "requested_by": str(payload.get("requested_by") or ""),
    }


def _ai_scheduler_summary(config: dict[str, Any] | None = None) -> dict[str, Any]:
    path = WORK_PATH / AI_SCHEDULER_STATE_NAME
    payload = _read_json_object(path)
    now = time.time()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    updated_at = _coerce_float(payload.get("updated_at")) or mtime
    state_changed_at = _coerce_float(payload.get("state_changed_at")) or updated_at
    heartbeat_age = max(0.0, now - updated_at) if updated_at > 0 else None
    state_age = max(0.0, now - state_changed_at) if state_changed_at > 0 else None
    watch_interval = _coerce_float((config or {}).get("watch_interval_seconds")) or 300.0
    # The Worker writes a dedicated heartbeat every 30 seconds even while an
    # isolated AI subprocess is running. Keep the stale threshold bounded so a
    # five-minute scan interval cannot hide a dead scheduler.
    stale_after = max(90.0, min(180.0, watch_interval))
    stale = bool(path.exists() and heartbeat_age is not None and heartbeat_age > stale_after)
    state = str(payload.get("state") or ("unknown" if path.exists() else "unavailable")).strip().casefold()
    reason_code = str(payload.get("reason_code") or "").strip().casefold()
    problem = bool(
        stale
        or state == "error"
        or (state == "blocked" and reason_code not in {"mikan_redownload"})
    )
    next_retry_at = _coerce_float(payload.get("next_retry_at")) or 0.0
    return {
        "path": str(path),
        "exists": path.exists(),
        "state": state,
        "reason_code": reason_code,
        "message": str(payload.get("message") or ""),
        "error": str(payload.get("error") or ""),
        "worker_pid": _coerce_int(payload.get("worker_pid")) or None,
        "updated_at": updated_at,
        "state_changed_at": state_changed_at,
        "heartbeat_age_seconds": round(heartbeat_age, 3) if heartbeat_age is not None else None,
        "state_age_seconds": round(state_age, 3) if state_age is not None else None,
        "stale_after_seconds": stale_after,
        "stale": stale,
        "problem": problem,
        "consecutive_errors": max(0, _coerce_int(payload.get("consecutive_errors")) or 0),
        "next_retry_at": next_retry_at,
        "retry_in_seconds": max(0, round(next_retry_at - now)) if next_retry_at > 0 else 0,
        "last_success_at": _coerce_float(payload.get("last_success_at")) or 0.0,
        "last_claim_at": _coerce_float(payload.get("last_claim_at")) or 0.0,
        "last_completed_at": _coerce_float(payload.get("last_completed_at")) or 0.0,
        "processed_last_cycle": max(0, _coerce_int(payload.get("processed_last_cycle")) or 0),
        "current_video": str(payload.get("current_video") or ""),
    }


def _deployment_hold_summary() -> dict[str, Any]:
    path = WORK_PATH / DEPLOYMENT_HOLD_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"active": False, "path": str(path)}
    except (OSError, TypeError, ValueError) as exc:
        return {"active": True, "path": str(path), "error": str(exc)}
    if not isinstance(payload, dict):
        return {"active": True, "path": str(path), "error": "invalid deployment hold payload"}
    return {
        "active": bool(payload.get("active", True)),
        "path": str(path),
        "deployment_id": str(payload.get("deployment_id") or ""),
        "created_at": float(payload.get("created_at") or 0),
        "reason": str(payload.get("reason") or ""),
    }


def _io_policy_summary(config: dict[str, Any], current_ai: dict[str, Any] | None) -> dict[str, Any]:
    idle_workers = max(1, _coerce_int(config.get("mikan_extract_workers")) or 2)
    during_ai_workers = min(
        idle_workers,
        max(1, _coerce_int(config.get("mikan_extract_workers_during_ai")) or 1),
    )
    startup_delay = _coerce_int(config.get("scanner_background_scan_startup_delay_seconds"))
    scan_yield_seconds = _coerce_float(config.get("scanner_walk_yield_seconds"))
    stage = str((current_ai or {}).get("stage") or "").strip().casefold()
    ai_disk_active = bool(current_ai) and stage in {
        "worker",
        "preflight",
        "metadata_context",
        "audio_selection",
        "audio",
        "language_detect",
        "vocal_separation",
        "transcription",
    }
    pressure = _read_io_pressure_summary()
    pressure_some_threshold = _coerce_float(config.get("storage_io_pressure_some_avg10_threshold")) or 35.0
    pressure_full_threshold = _coerce_float(config.get("storage_io_pressure_full_avg10_threshold")) or 10.0
    pressure_busy = bool(config.get("storage_io_pressure_enabled", True)) and (
        float(pressure.get("some_avg10") or 0) >= pressure_some_threshold
        or float(pressure.get("full_avg10") or 0) >= pressure_full_threshold
    )
    return {
        "profile": "storage-balanced",
        "ai_disk_active": ai_disk_active,
        "extract_workers_effective": 1 if pressure_busy else during_ai_workers if ai_disk_active else idle_workers,
        "extract_workers_idle": idle_workers,
        "extract_workers_during_ai": during_ai_workers,
        "background_scan_interval_seconds": max(
            1,
            _coerce_int(config.get("scanner_background_scan_interval_seconds")) or 21600,
        ),
        "background_scan_startup_delay_seconds": max(
            0,
            600 if startup_delay is None else startup_delay,
        ),
        "scan_yield_every_entries": max(
            1,
            _coerce_int(config.get("scanner_walk_yield_every_entries")) or 256,
        ),
        "scan_yield_seconds": max(
            0.0,
            0.025 if scan_yield_seconds is None else scan_yield_seconds,
        ),
        "pressure_busy": pressure_busy,
        "pressure": pressure,
    }


def _read_io_pressure_summary() -> dict[str, float]:
    try:
        lines = Path("/proc/pressure/io").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    result: dict[str, float] = {}
    for line in lines:
        kind, separator, values = line.partition(" ")
        if not separator or kind not in {"some", "full"}:
            continue
        match = re.search(r"\bavg10=([0-9.]+)", values)
        if match:
            result[f"{kind}_avg10"] = float(match.group(1))
    return result


def _set_ai_control(*, paused: bool) -> dict[str, Any]:
    path = WORK_PATH / AI_CONTROL_NAME
    now = time.time()
    payload = {
        "paused": bool(paused),
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": now,
        "requested_by": "webui",
    }
    _write_json_atomic(path, payload)
    return {"ok": True, **_ai_control_summary()}


def _request_mikan_redownload_cancel() -> dict[str, Any]:
    active_path = WORK_PATH / "mikan_redownload_all.active.json"
    request_path = WORK_PATH / "mikan_redownload_all.request.json"
    cancel_path = WORK_PATH / MIKAN_REDOWNLOAD_CANCEL_NAME
    active = _request_file_summary(active_path)
    pending = _request_file_summary(request_path)
    active_age = int(active.get("age_seconds") or 0) if active.get("exists") else None
    active_recent = bool(
        active.get("exists")
        and active_age is not None
        and active_age <= MIKAN_REDOWNLOAD_ACTIVE_STALE_SECONDS
    )
    if not active_recent and not pending.get("exists"):
        raise HTTPException(status_code=409, detail="No active or pending Mikan redownload-all operation")

    now = time.time()
    payload = {
        "action": "cancel_redownload_all",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": now,
        "requested_by": "webui",
    }
    _write_json_atomic(cancel_path, payload)
    cancelled_pending = bool(pending.get("exists") and not active_recent)
    if cancelled_pending:
        request_path.unlink(missing_ok=True)
    return {
        "ok": True,
        "cancel_requested": active_recent,
        "cancelled_pending": cancelled_pending,
        "message": (
            "Mikan redownload cancellation requested; the current network or filesystem step may finish first."
            if active_recent
            else "Pending Mikan redownload request cancelled."
        ),
        "path": str(cancel_path),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _load_config() -> dict[str, Any]:
    signature = _config_file_signature()
    with _CONFIG_CACHE_LOCK:
        if _CONFIG_CACHE.get("signature") == signature and isinstance(_CONFIG_CACHE.get("value"), dict):
            return dict(_CONFIG_CACHE["value"])

    if signature[1] < 0:
        with _CONFIG_CACHE_LOCK:
            _CONFIG_CACHE.update({"signature": signature, "value": {}})
        return {}
    try:
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="Config root must be a YAML mapping")
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE.update({"signature": signature, "value": dict(data)})
    return dict(data)


def _config_file_signature() -> tuple[str, int, int]:
    try:
        stat = CONFIG_PATH.stat()
    except OSError:
        return (str(CONFIG_PATH), -1, -1)
    return (str(CONFIG_PATH), int(stat.st_mtime_ns), int(stat.st_size))


def _invalidate_config_cache() -> None:
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE.update({"signature": None, "value": None})
    _SUBTITLE_QUALITY_CACHE.clear()
    _AI_COMPLETION_TIME_CACHE.clear()


def _save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=120)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=CONFIG_PATH.parent, delete=False) as handle:
            handle.write(rendered)
            temp_path = Path(handle.name)
        temp_path.replace(CONFIG_PATH)
    except OSError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        try:
            CONFIG_PATH.write_text(rendered, encoding="utf-8")
        except OSError as fallback_exc:
            raise HTTPException(status_code=500, detail=f"Failed to save config: {fallback_exc}") from fallback_exc
    _invalidate_config_cache()


def _coerce_value(key: str, value: Any) -> Any:
    meta = EDITABLE_FIELDS[key]
    kind = meta["type"]
    try:
        if kind == "bool":
            if isinstance(value, bool):
                coerced = value
            elif isinstance(value, str):
                lowered = value.strip().casefold()
                if lowered in {"true", "1", "yes", "on"}:
                    coerced = True
                elif lowered in {"false", "0", "no", "off"}:
                    coerced = False
                else:
                    raise ValueError
            else:
                coerced = bool(value)
        elif kind == "int":
            coerced = int(value)
        elif kind == "float":
            coerced = float(value)
        else:
            coerced = str(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid value for {key}") from exc

    minimum = meta.get("min")
    if minimum is not None and coerced < minimum:
        raise HTTPException(status_code=400, detail=f"{key} must be >= {minimum}")
    return coerced














def _queue_summary(
    status_filter: str | None = None,
    search: str = "",
    limit: int = 20,
    stale_running_seconds: int | None = None,
    max_concurrent_videos: int | None = None,
) -> dict[str, Any]:
    db_path = WORK_PATH / "scanner_state.sqlite3"
    if not db_path.exists():
        return {"database": str(db_path), "exists": False, "table_exists": False, "counts": {}, "recent": [], "filtered": 0}

    try:
        # Schema creation and migration belong exclusively to the Worker.  A
        # GET request must never become a second SQLite writer.
        with _sqlite_connect(db_path, readonly=True) as conn:
            if not _sqlite_table_exists(conn, "ai_candidate_queue"):
                return {
                    "database": str(db_path),
                    "exists": True,
                    "table_exists": False,
                    "counts": {},
                    "recent": [],
                    "filtered": 0,
                    "ready": 0,
                    "stale_running": 0,
                }
            combined_cte = _queue_combined_cte(conn)
            counts = {
                str(status): int(count)
                for status, count in conn.execute(
                    f"{combined_cte} SELECT status, COUNT(*) FROM combined GROUP BY status"
                ).fetchall()
            }
            now = time.time()
            stale_seconds = max(60, int(stale_running_seconds or QUEUE_STALE_RUNNING_SECONDS))
            stale_cutoff = now - stale_seconds
            active_running_limit = max(1, int(max_concurrent_videos or 1))
            running_rows = [
                (str(path), float(running_since or 0), float(heartbeat_at or running_since or 0))
                for path, running_since, heartbeat_at in conn.execute(
                    """
                    SELECT
                        q.path,
                        COALESCE(q.running_at, q.updated_at, 0) AS running_since,
                        COALESCE(j.updated_at, q.updated_at, q.running_at, 0) AS heartbeat_at
                    FROM ai_candidate_queue q
                    LEFT JOIN ai_job_state j ON j.path = q.path
                    WHERE q.status = 'running'
                    ORDER BY heartbeat_at DESC, q.path COLLATE NOCASE ASC
                    """
                ).fetchall()
            ]
            active_running_paths = {path for path, _running_since, _heartbeat_at in running_rows[:active_running_limit]}
            recoverable_running = sum(
                1
                for path, _running_since, heartbeat_at in running_rows
                if (heartbeat_at and heartbeat_at <= stale_cutoff) or path not in active_running_paths
            )
            ready = int(
                conn.execute(
                    f"""
                    {combined_cte}
                    SELECT COUNT(*)
                    FROM combined
                    WHERE status = 'queued'
                       OR (status = 'failed_retry' AND (next_retry_at IS NULL OR next_retry_at <= ?))
                    """,
                    (now,),
                ).fetchone()[0]
            )
            stale_running = sum(1 for _path, _running_since, heartbeat_at in running_rows if heartbeat_at and heartbeat_at <= stale_cutoff)
            where_parts: list[str] = []
            params: list[Any] = []
            if status_filter:
                where_parts.append("status = ?")
                params.append(status_filter)
            if search.strip():
                where_parts.append("path LIKE ? COLLATE NOCASE")
                params.append(f"%{search.strip()}%")
            where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
            recent_where_sql = _alias_queue_where(where_sql, "q")
            filtered = int(
                conn.execute(
                    f"{combined_cte} SELECT COUNT(*) FROM combined {where_sql}",
                    params,
                ).fetchone()[0]
            )
            recent = []
            for (
                path,
                status,
                attempts,
                updated_at,
                last_error,
                running_at,
                next_retry_at,
                force_ai,
                stage,
                job_status,
                message,
                started_at,
                job_updated_at,
                finished_at,
            ) in conn.execute(
                    f"""
                    {combined_cte}
                    SELECT
                        q.path,
                        q.status,
                        q.attempts,
                        q.updated_at,
                        q.last_error,
                        q.running_at,
                        q.next_retry_at,
                        q.force_ai,
                        j.stage,
                        j.status AS job_status,
                        j.message,
                        j.started_at,
                        j.updated_at AS job_updated_at,
                        j.finished_at
                    FROM combined q
                    LEFT JOIN ai_job_state j ON j.path = q.path
                    {recent_where_sql}
                    ORDER BY
                        CASE
                            WHEN q.status = 'queued' THEN 0
                            WHEN q.status = 'failed_retry'
                                 AND (q.next_retry_at IS NULL OR q.next_retry_at <= ?) THEN 1
                            WHEN q.status = 'running' THEN 2
                            WHEN q.status = 'failed_retry' THEN 3
                            WHEN q.status IN ('paused', 'skipped') THEN 4
                            WHEN q.status = 'done' THEN 5
                            ELSE 6
                        END ASC,
                        CASE
                            WHEN q.status = 'failed_retry'
                                 AND q.next_retry_at IS NOT NULL
                                 AND q.next_retry_at > ? THEN q.next_retry_at
                            ELSE 0
                        END ASC,
                        q.force_ai DESC,
                        q.added_at DESC,
                        q.mtime_ns DESC,
                        q.path COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    [*params, now, now, limit],
                ).fetchall():
                status_text = str(status)
                queue_updated_at = float(updated_at or 0)
                queue_running_at = float(running_at or 0)
                job_started_at = float(started_at or 0)
                job_updated_at_value = float(job_updated_at or 0)
                job_finished_at = float(finished_at or 0)
                running_started_at = (queue_running_at or job_started_at) if status_text == "running" else 0.0
                completed_at = (job_finished_at or queue_updated_at) if status_text == "done" else 0.0
                heartbeat_at = job_updated_at_value or queue_updated_at or queue_running_at
                running_stale = bool(status_text == "running" and heartbeat_at and heartbeat_at <= stale_cutoff)
                running_orphaned = bool(status_text == "running" and str(path) not in active_running_paths)
                item: dict[str, Any] = {
                    "path": str(path),
                    "status": status_text,
                    "attempts": int(attempts or 0),
                    "updated_at": max(queue_updated_at, job_updated_at_value),
                    "last_error": last_error,
                    "running_at": queue_running_at,
                    "running_started_at": running_started_at,
                    "heartbeat_at": heartbeat_at,
                    "running_stale": running_stale,
                    "running_orphaned": running_orphaned,
                    "running_recoverable": running_stale or running_orphaned,
                    "completed_at": completed_at,
                    "next_retry_at": float(next_retry_at or 0),
                    "force_ai": bool(force_ai),
                }
                if stage:
                    item["job"] = {
                        "stage": str(stage),
                        "status": str(job_status or ""),
                        "message": str(message or ""),
                        "started_at": job_started_at,
                        "updated_at": job_updated_at_value,
                        "finished_at": job_finished_at,
                    }
                recent.append(item)
    except sqlite3.Error as exc:
        return {
            "database": str(db_path),
            "exists": True,
            "table_exists": True,
            "error": str(exc),
            "counts": {},
            "recent": [],
            "filtered": 0,
        }

    return {
        "database": str(db_path),
        "exists": True,
        "table_exists": True,
        "counts": counts,
        "recent": recent,
        "filtered": filtered,
        "ready": ready,
        "stale_running": stale_running,
        "recoverable_running": recoverable_running,
        "max_concurrent_videos": active_running_limit,
        "stale_running_after_seconds": max(60, int(stale_running_seconds or QUEUE_STALE_RUNNING_SECONDS)),
        "filter": {"status": status_filter, "search": search, "limit": limit},
    }


def _workflow_nodes() -> list[dict[str, Any]]:
    return [
        {"id": "input", "label": "Input File", "subtitle": "來源影片與語言檢查", "kind": "source"},
        {"id": "transcribe", "label": "Whisper Transcribe", "subtitle": "語音轉錄與時間軸", "kind": "processor"},
        {"id": "translate", "label": "LLM Translate", "subtitle": "翻譯與字幕修整", "kind": "processor"},
        {"id": "output", "label": "Output File", "subtitle": "輸出字幕檔", "kind": "target"},
    ]


def _workflow_edges() -> list[dict[str, Any]]:
    return [
        {"id": "input-transcribe", "source": "input", "target": "transcribe"},
        {"id": "transcribe-translate", "source": "transcribe", "target": "translate"},
        {"id": "translate-output", "source": "translate", "target": "output"},
    ]


def _workflow_tasks_summary(limit: int = 200) -> dict[str, Any]:
    config = _load_config()
    return _dashboard_tasks_summary(
        limit=max(1, min(int(limit), 500)),
        stale_running_seconds=_config_stale_running_seconds(config),
        max_concurrent_videos=_config_max_concurrent_videos(config),
    )


def _dashboard_tasks_summary(
    limit: int = 160,
    status_filter: str | None = None,
    search: str = "",
    page: int = 1,
    page_size: int | None = None,
    mode: str = "all",
    stale_running_seconds: int | None = None,
    max_concurrent_videos: int | None = None,
) -> dict[str, Any]:
    page_value = max(1, int(page))
    limit_value = max(1, min(int(limit), 500))
    page_size_value = max(1, min(int(page_size), 100)) if page_size is not None else limit_value
    offset_value = (page_value - 1) * page_size_value if page_size is not None else 0
    query_limit = page_size_value if page_size is not None else limit_value
    mode_value = str(mode or "all").strip().lower()
    if mode_value not in {"all", "active", "completed"}:
        mode_value = "all"
    db_path = WORK_PATH / "scanner_state.sqlite3"
    if not db_path.exists():
        return {
            "database": str(db_path),
            "exists": False,
            "table_exists": False,
            "tasks": [],
            "recent_completed": [],
            "stats": _workflow_node_stats([]),
            "counts": {},
            "filtered": 0,
            "total": 0,
            "page": page_value,
            "page_size": page_size_value,
            "page_count": 1,
            "mode": mode_value,
            "updated_at": time.time(),
        }

    try:
        with _sqlite_connect(db_path, readonly=True) as conn:
            if not _sqlite_table_exists(conn, "ai_candidate_queue"):
                return {
                    "database": str(db_path),
                    "exists": True,
                    "table_exists": False,
                    "tasks": [],
                    "recent_completed": [],
                    "stats": _workflow_node_stats([]),
                    "counts": {},
                    "filtered": 0,
                    "total": 0,
                    "page": page_value,
                    "page_size": page_size_value,
                    "page_count": 1,
                    "mode": mode_value,
                    "updated_at": time.time(),
                }
            queue_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(ai_candidate_queue)").fetchall()
            }
            has_job_state = _sqlite_table_exists(conn, "ai_job_state")
            has_stage_events = _sqlite_table_exists(conn, "ai_stage_events")
            job_columns = (
                {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(ai_job_state)").fetchall()
                }
                if has_job_state
                else set()
            )

            def queue_expr(name: str, fallback: str) -> str:
                return f"q.{name}" if name in queue_columns else fallback

            def job_expr(name: str, fallback: str = "NULL") -> str:
                return f"j.{name}" if name in job_columns else fallback

            last_error_expr = queue_expr("last_error", "NULL")
            if has_stage_events and "last_error" in queue_columns:
                failure_anchor_expr = queue_expr("last_error_at", queue_expr("updated_at", "0"))
                last_error_expr = f"""
                    CASE
                        WHEN q.last_error = 'worker returned false'
                            THEN COALESCE(
                                (
                                    SELECT e.message
                                    FROM ai_stage_events e
                                    WHERE e.path = q.path
                                      AND e.status = 'failed'
                                      AND TRIM(COALESCE(e.message, '')) NOT IN ('', 'worker returned false')
                                      AND e.created_at >= COALESCE({failure_anchor_expr}, 0) - 21600
                                    ORDER BY e.created_at DESC, e.id DESC
                                    LIMIT 1
                                ),
                                q.last_error
                            )
                        ELSE q.last_error
                    END
                """

            now = time.time()
            stale_seconds = max(60, int(stale_running_seconds or QUEUE_STALE_RUNNING_SECONDS))
            stale_cutoff = now - stale_seconds
            active_running_limit = max(1, int(max_concurrent_videos or 1))
            running_since_expr = (
                f"COALESCE({queue_expr('running_at', 'NULL')}, "
                f"{queue_expr('updated_at', '0')}, 0)"
            )
            running_rows = [
                (str(path), float(running_since or 0))
                for path, running_since in conn.execute(
                    f"""
                    SELECT q.path, {running_since_expr} AS running_since
                    FROM ai_candidate_queue q
                    WHERE q.status = 'running'
                    ORDER BY running_since DESC, q.path COLLATE NOCASE ASC
                    """
                ).fetchall()
            ]
            active_running_paths = {path for path, _running_since in running_rows[:active_running_limit]}
            job_status_expr = job_expr("status")
            job_join_sql = "LEFT JOIN ai_job_state j ON j.path = q.path" if has_job_state else ""
            base_from_sql = f"""
                FROM (
                    SELECT
                        q.path,
                        CASE
                            WHEN q.status = 'running' THEN 'running'
                            WHEN q.status = 'done' AND {job_status_expr} = 'skipped' THEN 'skipped'
                            WHEN q.status = 'done' THEN 'done'
                            WHEN q.status = 'failed_retry' THEN 'failed_retry'
                            WHEN q.status = 'paused' THEN 'paused'
                            WHEN q.status = 'skipped' THEN 'skipped'
                            WHEN {job_status_expr} IN ('ok', 'done') THEN 'done'
                            ELSE q.status
                        END AS effective_status,
                        q.status AS queue_status,
                        {queue_expr('mtime_ns', '0')} AS mtime_ns,
                        {queue_expr('added_at', queue_expr('updated_at', '0'))} AS added_at,
                        {queue_expr('attempts', '0')} AS attempts,
                        {queue_expr('updated_at', '0')} AS updated_at,
                        {last_error_expr} AS last_error,
                        {queue_expr('running_at', 'NULL')} AS running_at,
                        {queue_expr('next_retry_at', 'NULL')} AS next_retry_at,
                        {queue_expr('force_ai', '0')} AS force_ai,
                        {job_expr('stage')} AS stage,
                        {job_status_expr} AS job_status,
                        {job_expr('message')} AS message,
                        {job_expr('started_at')} AS started_at,
                        {job_expr('updated_at', '0')} AS job_updated_at,
                        {job_expr('finished_at')} AS finished_at
                    FROM ai_candidate_queue q
                    {job_join_sql}
                ) item
            """
            counts = {
                str(status): int(count)
                for status, count in conn.execute(
                    f"""
                    SELECT effective_status, COUNT(*)
                    {base_from_sql}
                    GROUP BY effective_status
                    """
                ).fetchall()
            }
            where_parts: list[str] = []
            params: list[Any] = []
            normalized_status_filter = _normalize_dashboard_status_filter(status_filter)
            if normalized_status_filter:
                where_parts.append("effective_status = ?")
                params.append(normalized_status_filter)
            if mode_value == "active":
                where_parts.append("effective_status <> 'done'")
            elif mode_value == "completed":
                where_parts.append("effective_status = 'done'")
            if search.strip():
                where_parts.append("path LIKE ? COLLATE NOCASE")
                params.append(f"%{search.strip()}%")
            where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
            filtered = int(
                conn.execute(
                    f"SELECT COUNT(*) {base_from_sql} {where_sql}",
                    params,
                ).fetchone()[0]
            )
            if mode_value == "completed":
                order_sql = """
                    COALESCE(finished_at, job_updated_at, updated_at, 0) DESC,
                    path COLLATE NOCASE ASC
                """
                order_params: list[Any] = []
            else:
                order_sql = """
                    CASE
                        WHEN effective_status = 'running' THEN 0
                        WHEN effective_status = 'failed_retry' THEN 1
                        WHEN effective_status = 'queued' THEN 2
                        WHEN effective_status IN ('paused', 'skipped') THEN 3
                        WHEN effective_status = 'done' THEN 4
                        ELSE 5
                    END ASC,
                    CASE
                        WHEN effective_status = 'failed_retry'
                             AND next_retry_at IS NOT NULL
                             AND next_retry_at > ? THEN next_retry_at
                        ELSE 0
                    END ASC,
                    CASE
                        WHEN effective_status = 'queued' THEN CAST(COALESCE(added_at, 0) * 1000000000 AS INTEGER)
                        ELSE CAST(COALESCE(running_at, updated_at, 0) * 1000000000 AS INTEGER)
                    END DESC,
                    COALESCE(mtime_ns, 0) DESC,
                    COALESCE(running_at, updated_at, 0) DESC,
                    path COLLATE NOCASE ASC
                """
                order_params = [now]
            tasks = []
            for row in conn.execute(
                f"""
                SELECT
                    path,
                    effective_status,
                    queue_status,
                    mtime_ns,
                    added_at,
                    attempts,
                    updated_at,
                    last_error,
                    running_at,
                    next_retry_at,
                    force_ai,
                    stage,
                    job_status,
                    message,
                    started_at,
                    job_updated_at,
                    finished_at
                {base_from_sql}
                {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """,
                [*params, *order_params, query_limit, offset_value],
            ).fetchall():
                tasks.append(
                    _dashboard_task_from_row(
                        row,
                        stale_cutoff=stale_cutoff,
                        active_running_paths=active_running_paths,
                    )
                )
            recent_completed = []
            # Paginated dashboard callers already request either the active or
            # completed page. Returning another 100 completed task objects on
            # every active-page request made a 30-row response several times
            # larger and repeated expensive subtitle-quality probes.
            if page_size is None:
                for row in conn.execute(
                    f"""
                    SELECT
                        path,
                        effective_status,
                        queue_status,
                        mtime_ns,
                        added_at,
                        attempts,
                        updated_at,
                        last_error,
                        running_at,
                        next_retry_at,
                        force_ai,
                        stage,
                        job_status,
                        message,
                        started_at,
                        job_updated_at,
                        finished_at
                    {base_from_sql}
                    WHERE effective_status = 'done'
                    ORDER BY
                        COALESCE(finished_at, job_updated_at, updated_at, 0) DESC,
                        path COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    (min(100, max(1, int(limit))),),
                ).fetchall():
                    recent_completed.append(
                        _dashboard_task_from_row(
                            row,
                            stale_cutoff=stale_cutoff,
                            active_running_paths=active_running_paths,
                        )
                    )
    except sqlite3.Error as exc:
        return {
            "database": str(db_path),
            "exists": True,
            "table_exists": True,
            "error": str(exc),
            "tasks": [],
            "recent_completed": [],
            "stats": _workflow_node_stats([]),
            "counts": {},
            "filtered": 0,
            "total": 0,
            "page": page_value,
            "page_size": page_size_value,
            "page_count": 1,
            "mode": mode_value,
            "updated_at": time.time(),
        }

    page_count = max(1, (filtered + page_size_value - 1) // page_size_value)
    return {
        "database": str(db_path),
        "exists": True,
        "table_exists": True,
        "tasks": tasks,
        "recent_completed": recent_completed,
        "stats": _workflow_node_stats(tasks),
        "counts": counts,
        "queue_counts": counts,
        "filtered": filtered,
        "total": filtered,
        "page": page_value,
        "page_size": page_size_value,
        "page_count": page_count,
        "mode": mode_value,
        "updated_at": time.time(),
    }


def _normalize_dashboard_status_filter(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    return {
        "success": "done",
        "failed": "failed_retry",
        "queued": "queued",
        "running": "running",
        "paused": "paused",
        "skipped": "skipped",
        "done": "done",
    }.get(normalized, normalized)


def _dashboard_task_from_row(
    row: tuple[Any, ...],
    *,
    stale_cutoff: float,
    active_running_paths: set[str],
) -> dict[str, Any]:
    (
        path,
        effective_status,
        queue_status,
        mtime_ns,
        added_at,
        attempts,
        updated_at,
        last_error,
        running_at,
        next_retry_at,
        force_ai,
        stage,
        job_status,
        message,
        started_at,
        job_updated_at,
        finished_at,
    ) = row
    status_text = str(effective_status or queue_status or "")
    queue_status_text = str(queue_status or "")
    job_status_text = str(job_status or "")
    stage_text = str(stage or status_text)
    if status_text == "queued" and job_status_text in {"failed", "skipped"}:
        stage_text = status_text
    job_started_at = float(started_at or 0)
    job_updated_at_value = float(job_updated_at or 0)
    queue_running_at = float(running_at or 0)
    running_started_at = (queue_running_at or job_started_at) if status_text == "running" else 0.0
    queue_updated_at = float(updated_at or 0)
    path_text = str(path or "")
    message_text = _preferred_ai_failure_message(message, last_error)
    detected_existing_completion = bool(
        status_text == "done" and _is_detected_existing_ai_completion(stage_text, message_text)
    )
    detected_existing_file_time = (
        _ai_subtitle_completion_time_for_video(path_text) if detected_existing_completion else 0.0
    )
    heartbeat_at = job_updated_at_value or queue_updated_at or queue_running_at
    completed_at = (
        float(detected_existing_file_time or finished_at or job_updated_at_value or queue_updated_at or 0)
        if status_text == "done"
        else 0.0
    )
    running_stale = bool(status_text == "running" and heartbeat_at and heartbeat_at <= stale_cutoff)
    running_orphaned = bool(status_text == "running" and path_text not in active_running_paths)
    # Active queue cards cannot have a finished subtitle to inspect. Avoid
    # hundreds of guaranteed-miss stat calls across the media mount on every
    # active-page refresh.
    subtitle_quality = _subtitle_quality_for_video(path_text) if status_text == "done" else None
    node_id = _workflow_node_id(stage=stage_text, raw_status=status_text, job_status=job_status_text)
    status = _workflow_status(raw_status=status_text, job_status=job_status_text)
    language_info = _task_language_info(stage_text, message_text)
    metadata_info = _task_metadata_info(message_text)
    completion_kind = ""
    completion_label = ""
    if status_text == "done":
        completion_kind = (
            "generated"
            if (
                not detected_existing_completion
                and (float(finished_at or 0) > 0 or job_started_at > 0)
            )
            else "detected_existing"
        )
        completion_label = "AI 字幕生成完成" if completion_kind == "generated" else "掃描確認已有 AI 字幕"
    return {
        "path": path_text,
        "file_name": Path(path_text).name if path_text else "unknown",
        "node_id": node_id,
        "node_label": _workflow_node_label(node_id),
        "stage": stage_text,
        "status": status,
        "raw_status": status_text,
        "effective_status": status_text,
        "queue_status": queue_status_text,
        "job_status": job_status_text,
        "message": message_text,
        "problem": _ai_task_problem(
            status=status_text,
            stage=stage_text,
            message=message_text,
            retry_at=float(next_retry_at or 0),
        ),
        "language": language_info.get("language"),
        "language_probability": language_info.get("probability"),
        "language_confident": language_info.get("confident"),
        "language_allowed": language_info.get("allowed"),
        "language_reason": language_info.get("reason"),
        "language_samples": language_info.get("samples"),
        "skip_reason": language_info.get("skip_reason"),
        "metadata_context": metadata_info,
        "subtitle_quality": subtitle_quality,
        "progress": _workflow_progress(node_id=node_id, status=status, message=message_text),
        "attempts": int(attempts or 0),
        "updated_at": max(queue_updated_at, job_updated_at_value),
        "queued_at": float(added_at or 0),
        "priority_time": float(int(mtime_ns or 0) / 1_000_000_000),
        "mtime_ns": int(mtime_ns or 0),
        "running_started_at": running_started_at,
        "heartbeat_at": heartbeat_at,
        "completed_at": completed_at,
        "completion_kind": completion_kind,
        "completion_label": completion_label,
        "next_retry_at": float(next_retry_at or 0),
        "running_stale": running_stale,
        "running_orphaned": running_orphaned,
        "running_recoverable": running_stale or running_orphaned,
        "force_ai": bool(force_ai),
        "job": {
            "stage": stage_text,
            "status": job_status_text,
            "message": str(message or ""),
            "started_at": job_started_at,
            "updated_at": job_updated_at_value,
            "finished_at": float(finished_at or 0),
        }
        if stage
        else None,
    }


def _legacy_workflow_tasks_summary(limit: int = 200) -> dict[str, Any]:
    config = _load_config()
    queue_payload = _queue_summary(
        limit=max(1, min(int(limit), 500)),
        stale_running_seconds=_config_stale_running_seconds(config),
        max_concurrent_videos=_config_max_concurrent_videos(config),
    )
    tasks = [_workflow_task_from_queue_item(item) for item in queue_payload.get("recent", [])]
    stats = _workflow_node_stats(tasks)
    return {
        "database": queue_payload.get("database"),
        "exists": queue_payload.get("exists", False),
        "table_exists": queue_payload.get("table_exists", False),
        "tasks": tasks,
        "stats": stats,
        "total": queue_payload.get("filtered", len(tasks)),
        "queue_counts": queue_payload.get("counts", {}),
        "updated_at": time.time(),
    }


def _preferred_ai_failure_message(message: Any, last_error: Any) -> str:
    message_text = str(message or "").strip()
    last_error_text = str(last_error or "").strip()
    if message_text == "worker returned false" and last_error_text and last_error_text != message_text:
        return last_error_text
    return message_text or last_error_text


def _workflow_task_from_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    job = item.get("job") if isinstance(item.get("job"), dict) else {}
    raw_status = str(item.get("status") or "")
    job_stage = str(job.get("stage") or "")
    job_status = str(job.get("status") or "")
    stage = job_stage or raw_status
    node_id = _workflow_node_id(stage=stage, raw_status=raw_status, job_status=job_status)
    status = _workflow_status(raw_status=raw_status, job_status=job_status)
    message_text = _preferred_ai_failure_message(job.get("message"), item.get("last_error"))
    progress = _workflow_progress(
        node_id=node_id,
        status=status,
        message=message_text,
    )
    path = str(item.get("path") or "")
    subtitle_quality = _subtitle_quality_for_video(path)
    return {
        "path": path,
        "file_name": Path(path).name if path else "unknown",
        "node_id": node_id,
        "node_label": _workflow_node_label(node_id),
        "stage": stage,
        "status": status,
        "raw_status": raw_status,
        "job_status": job_status,
        "message": message_text,
        "subtitle_quality": subtitle_quality,
        "progress": progress,
        "attempts": int(item.get("attempts") or 0),
        "running_started_at": float(item.get("running_started_at") or 0),
        "updated_at": float(item.get("updated_at") or 0),
        "completed_at": float(item.get("completed_at") or 0),
        "running_recoverable": bool(item.get("running_recoverable")),
        "force_ai": bool(item.get("force_ai")),
    }


def _subtitle_quality_for_video(path: str) -> dict[str, Any] | None:
    if not path:
        return None
    cached = _ttl_cache_get(_SUBTITLE_QUALITY_CACHE, path, SUBTITLE_FILE_PROBE_CACHE_TTL_SECONDS)
    if isinstance(cached, dict) and "quality" in cached:
        value = cached.get("quality")
        return value if isinstance(value, dict) else None
    video = Path(path)
    candidates = _subtitle_quality_candidate_paths(video, _load_config())
    for subtitle_path in candidates:
        payload: dict[str, Any] | None = None
        for report_path in (
            _managed_subtitle_quality_report_path(subtitle_path),
            subtitle_path.with_name(subtitle_path.name + ".quality.json"),
        ):
            if not report_path.exists():
                continue
            try:
                candidate_payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(candidate_payload, dict):
                payload = candidate_payload
                break
        if payload is None:
            continue
        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        report_subtitle_path = payload.get("path")
        if not isinstance(report_subtitle_path, str) or not report_subtitle_path.strip():
            report_subtitle_path = str(subtitle_path)
        result = {
            "status": str(payload.get("status") or ""),
            "score": _coerce_int(payload.get("score")),
            "dialogues": _coerce_int(payload.get("dialogues")),
            "has_failures": bool(payload.get("has_failures")),
            "has_warnings": bool(payload.get("has_warnings")),
            "issues": [
                {
                    "code": str(issue.get("code") or ""),
                    "severity": str(issue.get("severity") or ""),
                    "message": str(issue.get("message") or ""),
                    "count": _coerce_int(issue.get("count")) or 0,
                    "samples": [
                        str(sample)[:240]
                        for sample in (issue.get("samples") if isinstance(issue.get("samples"), list) else [])[:5]
                    ],
                    "indexes": list(
                        dict.fromkeys(
                            index
                            for raw_index in (
                                issue.get("indexes") if isinstance(issue.get("indexes"), list) else []
                            )[:200]
                            if (index := _coerce_int(raw_index)) is not None and index > 0
                        )
                    )[:100],
                }
                for issue in issues[:8]
                if isinstance(issue, dict)
            ],
            "report_path": str(report_path),
            "subtitle_path": report_subtitle_path,
            "updated_at": report_path.stat().st_mtime,
        }
        _ttl_cache_set(_SUBTITLE_QUALITY_CACHE, path, {"quality": result})
        return result
    _ttl_cache_set(_SUBTITLE_QUALITY_CACHE, path, {"quality": None})
    return None


def _managed_subtitle_quality_report_path(subtitle_path: str | Path) -> Path:
    path = Path(subtitle_path)
    normalized = str(path.absolute())
    digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:24]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name).strip("._-")[:96] or "subtitle"
    return WORK_PATH / "subtitle_quality_reports" / f"{safe_name}.{digest}.quality.json"


def _is_detected_existing_ai_completion(stage: str, message: str) -> bool:
    stage_text = str(stage or "").strip().lower()
    message_text = str(message or "").strip().lower()
    return (
        stage_text == "detected_existing"
        or "already exists" in message_text
        or "detected during scan" in message_text
        or "detected before queue processing" in message_text
    )


def _ai_subtitle_completion_time_for_video(path: str) -> float:
    if not path:
        return 0.0
    cached = _ttl_cache_get(_AI_COMPLETION_TIME_CACHE, path, SUBTITLE_FILE_PROBE_CACHE_TTL_SECONDS)
    if isinstance(cached, dict) and "mtime" in cached:
        return float(cached.get("mtime") or 0.0)
    video = Path(path)
    mtimes: list[float] = []
    for subtitle_path in _subtitle_quality_candidate_paths(video, _load_config()):
        if not subtitle_path.exists() or not subtitle_path.is_file():
            continue
        try:
            mtimes.append(subtitle_path.stat().st_mtime)
        except OSError:
            continue
    result = max(mtimes) if mtimes else 0.0
    _ttl_cache_set(_AI_COMPLETION_TIME_CACHE, path, {"mtime": result})
    return result


def _subtitle_quality_candidate_paths(video: Path, config: dict[str, Any]) -> list[Path]:
    suffixes = [
        config.get("ai_traditional_chinese_ass_suffix"),
        ".AI繁日雙語.zh-TW.ass",
        ".AI繁體中文.zh-TW.ass",
        ".AI繁体中文.zh-TW.ass",
        config.get("ai_simplified_chinese_ass_suffix"),
        ".AI简日双语.zh.ass",
        ".AI簡日雙語.zh.ass",
        config.get("ai_japanese_ass_suffix"),
        ".AI日本語.ja.ass",
        ".AI日語.ja.ass",
    ]
    seen: set[str] = set()
    candidates: list[Path] = []
    for suffix in suffixes:
        suffix_text = str(suffix or "").strip()
        if not suffix_text:
            continue
        candidate = video.with_name(video.stem + suffix_text)
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    try:
        globbed = sorted(video.parent.glob(video.stem + ".AI*.ass"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        globbed = []
    for candidate in globbed:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return candidates


def _task_language_info(stage: str, message: str) -> dict[str, Any]:
    fields = _parse_key_value_message(message)
    language = fields.get("language")
    probability = _parse_optional_float(fields.get("probability"))
    confident = _parse_optional_bool(fields.get("confident"))
    allowed_values = [item.strip() for item in str(fields.get("allowed") or "").split(",") if item.strip()]
    info: dict[str, Any] = {}
    if language:
        info["language"] = language
        info["probability"] = probability
        info["confident"] = confident
        info["allowed"] = language in allowed_values if allowed_values else None
        info["reason"] = fields.get("reason")
        info["samples"] = fields.get("samples")
    lowered = message.lower()
    if (
        "language_skip" in stage.lower()
        or "language_uncertain" in stage.lower()
        or lowered.startswith("skipped non-allowed source language")
        or lowered.startswith("skipped source language gate")
    ):
        info["skip_reason"] = message
        if not language:
            info["language"] = "unknown"
    return info


def _task_metadata_info(message: str) -> dict[str, Any] | None:
    if "metadata context" not in message.lower():
        return None
    fields = _parse_key_value_message(message)
    return {
        "provider": fields.get("provider"),
        "cached": _parse_optional_bool(fields.get("cached")),
        "chars": _parse_optional_int(fields.get("chars")),
    }


def _parse_key_value_message(message: str) -> dict[str, str]:
    return {
        str(key): str(value).strip()
        for key, value in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)", message or "")
    }


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return None


def _workflow_node_id(*, stage: str, raw_status: str, job_status: str) -> str:
    stage_value = stage.lower()
    status_value = (job_status or raw_status).lower()
    if "language" in stage_value:
        return "input"
    if "metadata" in stage_value:
        return "translate"
    if raw_status == "done" or status_value in {"ok", "done", "success", "finished"} or stage_value == "complete":
        return "output"
    if "translate" in stage_value or "translation" in stage_value or "llm" in stage_value:
        return "translate"
    if "transcrib" in stage_value or "transcript" in stage_value or "whisper" in stage_value or "vocal" in stage_value:
        return "transcribe"
    return "input"


def _workflow_status(*, raw_status: str, job_status: str) -> str:
    raw_value = (raw_status or "").lower()
    job_value = (job_status or "").lower()
    if raw_value == "queued":
        return "Queued"
    if raw_value == "running":
        return "Running"
    if raw_value == "failed_retry":
        return "Failed"
    if raw_value == "paused":
        return "Paused"
    if raw_value == "skipped" or job_value == "skipped":
        return "Skipped"
    if raw_value == "done":
        return "Success"
    value = job_value or raw_value
    if value in {"ok", "done", "success", "finished", "complete", "completed"}:
        return "Success"
    if "fail" in value or value == "error":
        return "Failed"
    if value in {"running", "processing", "active"}:
        return "Running"
    if value == "paused":
        return "Paused"
    if value == "skipped":
        return "Skipped"
    return "Queued"


def _workflow_progress(*, node_id: str, status: str, message: str = "") -> int | None:
    if status in {"Success", "Failed", "Skipped"}:
        return 100
    if status == "Running":
        batch = re.search(r"\b(?:translating|translated)?\s*batch\s+(\d+)\s*/\s*(\d+)\b", message, re.IGNORECASE)
        if batch:
            current = int(batch.group(1))
            total = int(batch.group(2))
            if total > 0:
                return max(0, min(100, round(current * 100 / total)))
        return None
    return 0


def _workflow_node_stats(tasks: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    stats = {
        str(node["id"]): {"total": 0, "running": 0, "success": 0, "failed": 0, "queued": 0}
        for node in _workflow_nodes()
    }
    for task in tasks:
        node_id = str(task.get("node_id") or "input")
        bucket = stats.setdefault(node_id, {"total": 0, "running": 0, "success": 0, "failed": 0, "queued": 0})
        bucket["total"] += 1
        status = str(task.get("status") or "").lower()
        if status == "running":
            bucket["running"] += 1
        elif status == "success":
            bucket["success"] += 1
        elif status == "failed":
            bucket["failed"] += 1
        else:
            bucket["queued"] += 1
    return stats


def _workflow_node_label(node_id: str) -> str:
    for node in _workflow_nodes():
        if node["id"] == node_id:
            return str(node["label"])
    return node_id


def _queue_combined_cte(conn: sqlite3.Connection) -> str:
    completed_sources = [
        """
        SELECT path, updated_at, 'queue done' AS message
        FROM ai_candidate_queue
        WHERE status = 'done'
        """,
        """
        SELECT path, COALESCE(finished_at, updated_at), COALESCE(message, 'AI subtitle job completed')
        FROM ai_job_state
        WHERE (stage = 'complete' AND status IN ('ok', 'done'))
           OR status IN ('ok', 'done')
        """,
        """
        SELECT path, created_at, COALESCE(message, 'AI subtitle job completed')
        FROM ai_stage_events
        WHERE stage = 'complete'
          AND status IN ('ok', 'done')
        """,
    ]
    completed_sql = "\nUNION ALL\n".join(completed_sources)
    return f"""
        WITH completed_raw(path, updated_at, message) AS (
            {completed_sql}
        ),
        completed AS (
            SELECT path, MAX(COALESCE(updated_at, 0)) AS updated_at
            FROM completed_raw
            WHERE path IS NOT NULL
              AND path != ''
            GROUP BY path
        ),
        combined_queue AS (
            SELECT
                q.path,
                CASE
                    WHEN q.status = 'done' AND current_job.status = 'skipped'
                        THEN 'skipped'
                    WHEN c.path IS NOT NULL AND COALESCE(c.updated_at, 0) >= COALESCE(q.updated_at, 0)
                        THEN 'done'
                    ELSE q.status
                END AS status,
                CASE
                    WHEN c.path IS NOT NULL AND COALESCE(c.updated_at, 0) >= COALESCE(q.updated_at, 0)
                        THEN 0
                    ELSE q.attempts
                END AS attempts,
                CASE
                    WHEN c.path IS NOT NULL AND COALESCE(c.updated_at, 0) > COALESCE(q.updated_at, 0)
                        THEN c.updated_at
                    ELSE q.updated_at
                END AS updated_at,
                CASE
                    WHEN c.path IS NOT NULL AND COALESCE(c.updated_at, 0) >= COALESCE(q.updated_at, 0)
                        THEN NULL
                    WHEN q.last_error = 'worker returned false'
                        THEN COALESCE(
                            (
                                SELECT e.message
                                FROM ai_stage_events e
                                WHERE e.path = q.path
                                  AND e.status = 'failed'
                                  AND TRIM(COALESCE(e.message, '')) NOT IN ('', 'worker returned false')
                                  AND e.created_at >= COALESCE(q.last_error_at, q.updated_at, 0) - 21600
                                ORDER BY e.created_at DESC, e.id DESC
                                LIMIT 1
                            ),
                            q.last_error
                        )
                    ELSE q.last_error
                END AS last_error,
                CASE
                    WHEN c.path IS NOT NULL AND COALESCE(c.updated_at, 0) >= COALESCE(q.updated_at, 0)
                        THEN NULL
                    ELSE q.running_at
                END AS running_at,
                CASE
                    WHEN c.path IS NOT NULL AND COALESCE(c.updated_at, 0) >= COALESCE(q.updated_at, 0)
                        THEN NULL
                    ELSE q.next_retry_at
                END AS next_retry_at,
                CASE
                    WHEN c.path IS NOT NULL AND COALESCE(c.updated_at, 0) >= COALESCE(q.updated_at, 0)
                        THEN 0
                    ELSE COALESCE(q.force_ai, 0)
                END AS force_ai,
                q.mtime_ns,
                q.added_at
            FROM ai_candidate_queue q
            LEFT JOIN completed c ON c.path = q.path
            LEFT JOIN ai_job_state current_job ON current_job.path = q.path
        ),
        virtual_done AS (
            SELECT
                c.path,
                'done' AS status,
                0 AS attempts,
                c.updated_at AS updated_at,
                NULL AS last_error,
                NULL AS running_at,
                NULL AS next_retry_at,
                0 AS force_ai,
                CAST(COALESCE(c.updated_at, 0) * 1000000000 AS INTEGER) AS mtime_ns,
                c.updated_at AS added_at
            FROM completed c
            LEFT JOIN ai_candidate_queue q ON q.path = c.path
            WHERE q.path IS NULL
        ),
        combined AS (
            SELECT * FROM combined_queue
            UNION ALL
            SELECT * FROM virtual_done
        )
    """


def _alias_queue_where(where_sql: str, alias: str) -> str:
    if not where_sql:
        return ""
    return (
        where_sql.replace("WHERE status = ?", f"WHERE {alias}.status = ?")
        .replace(" AND status = ?", f" AND {alias}.status = ?")
        .replace("WHERE path LIKE ?", f"WHERE {alias}.path LIKE ?")
        .replace(" AND path LIKE ?", f" AND {alias}.path LIKE ?")
    )


def _config_stale_running_seconds(config: dict[str, Any]) -> int:
    try:
        return max(
            60,
            int(
                config.get("ai_queue_stage_stale_seconds")
                or config.get("ai_queue_running_stale_seconds")
                or QUEUE_STALE_RUNNING_SECONDS
            ),
        )
    except (TypeError, ValueError):
        return QUEUE_STALE_RUNNING_SECONDS


def _config_max_concurrent_videos(config: dict[str, Any]) -> int:
    try:
        return max(1, int(config.get("max_concurrent_videos") or 1))
    except (TypeError, ValueError):
        return 1


def _ttl_cache_get(cache: dict[Any, Any], key: Any, ttl_seconds: float) -> Any | None:
    cached = cache.get(key)
    if not isinstance(cached, dict):
        return None
    if time.monotonic() - float(cached.get("monotonic_at", 0.0)) > ttl_seconds:
        return None
    return cached.get("value")


def _ttl_cache_set(cache: dict[Any, Any], key: Any, value: Any) -> Any:
    if len(cache) > 128:
        cache.clear()
    cache[key] = {"monotonic_at": time.monotonic(), "value": value}
    return value


def _series_metadata_db_path(config: dict[str, Any] | None = None) -> Path:
    config = config or _load_config()
    raw_path = _expand_config_env(
        str(config.get("series_metadata_db_path") or "series_metadata.sqlite3")
    ).strip() or "series_metadata.sqlite3"
    configured = Path(raw_path)
    if configured.is_absolute():
        return configured
    return Path(str(config.get("work_path") or "/work")) / configured


def _series_artwork_cache_root(config: dict[str, Any]) -> Path:
    raw_path = _expand_config_env(str(config.get("series_artwork_cache_path") or "series_artwork")).strip()
    configured = Path(raw_path or "series_artwork")
    return configured if configured.is_absolute() else _configured_work_path(config) / configured


def _series_artwork_path(series_id: str, *, config: dict[str, Any]) -> Path | None:
    normalized = str(series_id or "").strip()
    if not re.fullmatch(r"series_[0-9a-f]{24}", normalized):
        return None
    database = _series_metadata_db_path(config)
    if not database.exists():
        return None
    try:
        with _sqlite_connect(database, readonly=True) as connection:
            if not _sqlite_table_exists(connection, "series_profiles"):
                return None
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(series_profiles)").fetchall()}
            if not {"series_id", "cover_image_cache_key"}.issubset(columns):
                return None
            row = connection.execute(
                "SELECT cover_image_cache_key FROM series_profiles WHERE series_id=?",
                (normalized,),
            ).fetchone()
    except sqlite3.Error:
        return None
    key = str(row[0] or "").strip() if row is not None else ""
    if not re.fullmatch(r"series_[0-9a-f]{24}\.(?:jpg|png|webp)", key, flags=re.IGNORECASE):
        return None
    root = _series_artwork_cache_root(config).resolve(strict=False)
    candidate = (root / key).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _series_profiles_summary(*, page: int, page_size: int, search: str) -> dict[str, Any]:
    db_path = _series_metadata_db_path()
    if not db_path.exists():
        return {"exists": False, "items": [], "total": 0, "page": page, "page_size": page_size, "page_count": 0}
    with _sqlite_connect(db_path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        if not _sqlite_table_exists(conn, "series_profiles"):
            return {"exists": True, "items": [], "total": 0, "page": page, "page_size": page_size, "page_count": 0}
        params: list[Any] = []
        where = ""
        if search:
            token = f"%{search}%"
            where = "WHERE canonical_title LIKE ? OR local_path LIKE ? OR aliases_json LIKE ?"
            params.extend([token, token, token])
        total = int(conn.execute(f"SELECT COUNT(*) FROM series_profiles {where}", params).fetchone()[0])
        coverage = {
            "anilist": int(conn.execute("SELECT COUNT(*) FROM series_profiles WHERE provider = 'anilist'").fetchone()[0]),
            "enriched": int(
                conn.execute(
                    "SELECT COUNT(*) FROM series_profiles WHERE provider = 'anilist' AND length(COALESCE(synopsis, '')) > 0"
                ).fetchone()[0]
            ),
            "unmatched": int(
                conn.execute(
                    "SELECT COUNT(*) FROM series_profiles WHERE provider NOT IN ('anilist') OR provider_id = ''"
                ).fetchone()[0]
            ),
            "glossary_terms": int(conn.execute("SELECT COUNT(*) FROM series_glossary").fetchone()[0])
            if _sqlite_table_exists(conn, "series_glossary")
            else 0,
            "glossary_series": int(
                conn.execute("SELECT COUNT(DISTINCT local_path_key) FROM series_glossary").fetchone()[0]
            )
            if _sqlite_table_exists(conn, "series_glossary")
            else 0,
        }
        rows = conn.execute(
            f"SELECT * FROM series_profiles {where} ORDER BY updated_at DESC, canonical_title LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "exists": True,
        "database": str(db_path),
        "items": [_public_series_profile(dict(row)) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": (total + page_size - 1) // page_size if total else 0,
        "coverage": coverage,
    }


def _series_profile_detail(path: str) -> dict[str, Any]:
    local_path = _validated_anime_path(path)
    db_path = _series_metadata_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Series metadata database does not exist")
    key = str(local_path.resolve()).casefold()
    with _sqlite_connect(db_path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM series_profiles WHERE local_path_key = ?", (key,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Series profile not found")
        glossary = [
            dict(item)
            for item in conn.execute(
                "SELECT source_text, target_text, term_type, locked, source, updated_at FROM series_glossary WHERE local_path_key = ? ORDER BY locked DESC, source_text",
                (key,),
            ).fetchall()
        ]
    return {"profile": _public_series_profile(dict(row)), "glossary": glossary}


def _series_profile_detail_by_id(series_id: str) -> dict[str, Any]:
    normalized = str(series_id or "").strip()
    if not re.fullmatch(r"series_[0-9a-f]{24}", normalized):
        raise HTTPException(status_code=400, detail="Invalid series id")
    db_path = _series_metadata_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Series metadata database does not exist")
    with _sqlite_connect(db_path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        if not _sqlite_table_exists(conn, "series_profiles"):
            raise HTTPException(status_code=404, detail="Series profile not found")
        columns = {str(item[1]) for item in conn.execute("PRAGMA table_info(series_profiles)").fetchall()}
        if "series_id" in columns:
            row = conn.execute("SELECT * FROM series_profiles WHERE series_id = ?", (normalized,)).fetchone()
        else:
            row = next(
                (
                    candidate
                    for candidate in conn.execute("SELECT * FROM series_profiles").fetchall()
                    if stable_id("series", str(candidate["local_path_key"] or "")) == normalized
                ),
                None,
            )
        if row is None:
            raise HTTPException(status_code=404, detail="Series profile not found")
        key = str(row["local_path_key"] or "")
        glossary = [
            dict(item)
            for item in conn.execute(
                "SELECT source_text, target_text, term_type, locked, source, updated_at "
                "FROM series_glossary WHERE local_path_key = ? ORDER BY locked DESC, source_text",
                (key,),
            ).fetchall()
        ] if _sqlite_table_exists(conn, "series_glossary") else []
    return {"profile": _public_series_profile(dict(row)), "glossary": glossary}


def _ai_diagnostics_for_video(path: str) -> dict[str, Any]:
    video = _validated_anime_path(path)
    config = _load_config()
    digest = hashlib.sha1(str(video.resolve()).encode("utf-8")).hexdigest()[:20]
    work_path = Path(str(config.get("work_path") or "/work"))
    provenance_root = Path(str(config.get("processing_provenance_path") or "provenance"))
    if not provenance_root.is_absolute():
        provenance_root = work_path / provenance_root
    audio_root = Path(str(config.get("audio_selection_manifest_path") or "audio_selection"))
    if not audio_root.is_absolute():
        audio_root = work_path / audio_root
    return {
        "path": str(video),
        "provenance": _read_json_object(provenance_root / f"{digest}.json"),
        "audio_selection": _read_json_object(audio_root / f"{digest}.json"),
        "review": _ai_review_lines(work_path, digest[:16]),
    }


def _ai_review_lines(work_path: Path, digest: str) -> dict[str, Any]:
    cache_root = work_path / "ai_srt_cache"
    candidates = sorted(cache_root.glob(f"*.{digest}.*.srt"), key=lambda item: item.stat().st_mtime, reverse=True)
    archive_root = work_path / "asr_review_archive"
    if archive_root.exists():
        archive_dirs = sorted(
            archive_root.glob(f"*-{digest}"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if archive_dirs:
            candidates.extend(sorted(archive_dirs[0].glob("*.srt"), key=lambda item: item.name))

    japanese: dict[int, dict[str, Any]] = {}
    chinese: dict[int, dict[str, Any]] = {}
    files: list[str] = []
    for candidate in candidates:
        files.append(str(candidate))
        target = japanese if ".ja.srt" in candidate.name.casefold() else chinese
        for row in _parse_srt_review_rows(candidate):
            target.setdefault(int(row["index"]), row)
    indexes = sorted(set(japanese) | set(chinese))
    return {
        "files": files[:8],
        "line_count": len(indexes),
        "lines": [
            {
                "index": index,
                "timing": (japanese.get(index) or chinese.get(index) or {}).get("timing", ""),
                "japanese": japanese.get(index, {}).get("text", ""),
                "chinese": chinese.get(index, {}).get("text", ""),
            }
            for index in indexes[:600]
        ],
    }


def _parse_srt_review_rows(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for block in re.split(r"\n{2,}", text.strip()):
        lines = [line.strip() for line in block.splitlines()]
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0])
        except ValueError:
            continue
        if "-->" not in lines[1]:
            continue
        rows.append({"index": index, "timing": lines[1], "text": "\n".join(lines[2:]).strip()})
    return rows


def _public_series_profile(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    local_path_key = str(payload.get("local_path_key") or str(payload.get("local_path") or "").casefold())
    payload["series_id"] = str(payload.get("series_id") or stable_id("series", local_path_key))
    for key in ("titles_json", "aliases_json", "characters_json", "staff_json"):
        target = key.removesuffix("_json")
        try:
            value = json.loads(str(payload.pop(key, "[]") or "[]"))
        except json.JSONDecodeError:
            value = []
        payload[target] = value if isinstance(value, list) else []
    payload["locked"] = bool(payload.get("locked"))
    payload.pop("local_path_key", None)
    return payload


def _validated_anime_path(path: str) -> Path:
    config = _load_config()
    root_text = _expand_config_env(str(config.get("input_path") or "/anime")).strip() or "/anime"
    candidate_text = str(path or "").strip()
    if root_text.startswith("/") or candidate_text.startswith("/"):
        if not root_text.startswith("/") or not candidate_text.startswith("/"):
            raise HTTPException(status_code=400, detail="Path style must match input_path")
        root_posix = PurePosixPath(posixpath.normpath(root_text))
        candidate_posix = PurePosixPath(posixpath.normpath(candidate_text))
        try:
            candidate_posix.relative_to(root_posix)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Path must stay under input_path") from exc
        # Keep the normalized container spelling. No stat/resolve call is made,
        # so the WebUI container does not need the media mount.
        return Path(str(candidate_posix))
    root = Path(root_text).resolve(strict=False)
    candidate_raw = Path(candidate_text)
    if not candidate_raw.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute")
    # Pure lexical containment: the WebUI container does not need /anime to be
    # mounted, but '..' and sibling-prefix escapes are still rejected.
    candidate = candidate_raw.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path must stay under input_path") from exc
    return candidate


def _validated_anime_path_text(path: str) -> str:
    """Return normalized text without changing POSIX container separators on Windows tests."""
    validated = _validated_anime_path(path)
    return validated.as_posix() if str(path or "").strip().startswith("/") else str(validated)


def _configured_work_path(config: dict[str, Any]) -> Path:
    raw = _expand_config_env(str(config.get("work_path") or str(WORK_PATH))).strip()
    path = Path(raw or str(WORK_PATH))
    return path if path.is_absolute() else WORK_PATH / path


def _completed_delivery_payload(
    *,
    enabled: bool,
    available: bool = False,
    state: str,
    final_path: str = "",
    committed_at: float = 0.0,
    size: int = 0,
    output_hash: str = "",
    error: str = "",
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "available": bool(available),
        "state": str(state),
        "final_path": str(final_path),
        "committed_at": float(committed_at or 0),
        "size": int(size or 0),
        "hash": str(output_hash),
        "error": str(error),
    }


def _completed_delivery_feature_enabled(config: dict[str, Any]) -> bool:
    # Worker config parsing produces a real bool. Fail closed on YAML strings
    # such as "true" instead of silently enabling filesystem reads in WebUI.
    return config.get("completed_delivery_enabled") is True


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _completed_delivery_expected_paths(
    video: str,
    config: dict[str, Any],
) -> tuple[Path, Path, Path, Path, Path]:
    source_policy = str(
        config.get("completed_delivery_source_policy") or "retain"
    ).strip().casefold()
    if source_policy != "retain":
        raise ValueError("completed_source_policy_invalid")
    source = Path(str(video or "")).resolve(strict=False)
    if not str(video or "").strip():
        raise ValueError("source_path_missing")
    work_root = _configured_work_path(config).resolve(strict=False)
    input_value = _expand_config_env(str(config.get("input_path") or "/anime")).strip()
    input_root = Path(input_value or "/anime").resolve(strict=False)
    completed_value = _expand_config_env(
        str(config.get("completed_delivery_path") or "")
    ).strip()
    if not completed_value:
        raise ValueError("completed_root_unconfigured")
    completed_root = Path(completed_value)
    if not completed_root.is_absolute():
        completed_root = work_root / completed_root
    completed_root = completed_root.resolve(strict=False)
    if not completed_root.is_dir():
        raise ValueError("completed_root_unavailable")
    if _path_is_within(completed_root, input_root):
        raise ValueError("completed_root_inside_input")
    if _path_is_within(completed_root, work_root):
        raise ValueError("completed_root_inside_work")
    try:
        relative = source.relative_to(input_root)
    except ValueError as exc:
        raise ValueError("source_outside_input") from exc
    relative_output = (
        relative
        if relative.suffix.casefold() == ".mkv"
        else relative.with_name(f"{relative.name}.mkv")
    )
    destination = (completed_root / relative_output).resolve(strict=False)
    if destination == source or not _path_is_within(destination, completed_root):
        raise ValueError("completed_destination_invalid")

    digest = hashlib.sha256(
        str(source).encode("utf-8", errors="replace")
    ).hexdigest()
    receipt_value = _expand_config_env(
        str(
            config.get("completed_delivery_manifest_path")
            or "completed_delivery_manifests"
        )
    ).strip()
    receipt_root = Path(receipt_value or "completed_delivery_manifests")
    if not receipt_root.is_absolute():
        receipt_root = work_root / receipt_root
    receipt = receipt_root.resolve(strict=False) / digest[:2] / f"{digest}.json"

    manifest_value = _expand_config_env(
        str(config.get("ai_output_manifest_path") or "ai_output_manifests")
    ).strip()
    manifest_root = Path(manifest_value or "ai_output_manifests")
    if not manifest_root.is_absolute():
        manifest_root = work_root / manifest_root
    manifest = manifest_root.resolve(strict=False) / digest[:2] / f"{digest}.json"
    return source, destination, receipt, receipt.with_suffix(".delivering"), manifest


def _completed_delivery_file_signature(path: Path) -> tuple[bool, int, int]:
    try:
        stat = path.stat()
        return path.is_file(), int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        return False, 0, 0


def _completed_delivery_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _completed_delivery_ledger_evidence(
    paths: list[str] | tuple[str, ...],
) -> tuple[bool, dict[str, dict[str, Any]]]:
    canonical_paths = sorted(
        {
            str(Path(path).resolve(strict=False))
            for path in paths
            if str(path or "").strip()
        }
    )
    if not canonical_paths:
        return True, {}
    database = WORK_PATH / "scanner_state.sqlite3"
    if not database.is_file():
        return False, {}
    try:
        with _sqlite_connect(database, readonly=True) as connection:
            if not _sqlite_table_exists(connection, "ai_delivery_obligations"):
                return False, {}
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(ai_delivery_obligations)"
                ).fetchall()
            }
            required = {
                "obligation_id",
                "canonical_path",
                "policy_revision",
                "state",
                "verified_at",
                "verification_json",
            }
            if not required.issubset(columns):
                return False, {}
            placeholders = ",".join("?" for _path in canonical_paths)
            rows = connection.execute(
                f"""
                SELECT obligation_id, canonical_path, policy_revision,
                       verified_at, verification_json
                FROM ai_delivery_obligations
                WHERE state='succeeded' AND canonical_path IN ({placeholders})
                ORDER BY verified_at DESC, obligation_id ASC
                """,
                canonical_paths,
            ).fetchall()
    except (OSError, sqlite3.Error):
        return False, {}

    result: dict[str, dict[str, Any]] = {}
    for obligation_id, canonical_path, policy_revision, verified_at, verification_json in rows:
        path = str(canonical_path or "")
        if not path or path in result:
            continue
        try:
            verification = json.loads(str(verification_json or "{}"))
            verified_value = float(verified_at or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(verification, dict):
            continue
        if verification.get("completed_delivery_verified") is not True:
            continue
        result[path] = {
            "obligation_id": str(obligation_id or ""),
            "policy_revision": str(policy_revision or ""),
            "verified_at": verified_value,
            "receipt_path": str(
                verification.get("completed_delivery_receipt") or ""
            ),
            "receipt_sha256": str(
                verification.get("completed_delivery_receipt_sha256") or ""
            ).casefold(),
            "committed_at": verification.get(
                "completed_delivery_committed_at"
            ),
        }
    return True, result


def _completed_delivery_cache_signature(
    *,
    source: Path,
    destination: Path,
    receipt: Path,
    marker: Path,
    manifest: Path,
    ledger_available: bool,
    evidence: dict[str, Any] | None,
) -> tuple[Any, ...]:
    evidence_text = json.dumps(
        evidence or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        str(source),
        str(destination),
        str(receipt),
        str(manifest),
        _completed_delivery_file_signature(source),
        _completed_delivery_file_signature(destination),
        _completed_delivery_file_signature(receipt),
        _completed_delivery_file_signature(marker),
        _completed_delivery_file_signature(manifest),
        bool(ledger_available),
        evidence_text,
    )


def _completed_delivery_cache_get(
    source: Path, signature: tuple[Any, ...]
) -> dict[str, Any] | None:
    with _COMPLETED_DELIVERY_STATUS_CACHE_LOCK:
        cached = _COMPLETED_DELIVERY_STATUS_CACHE.get(str(source))
        if not isinstance(cached, dict) or cached.get("signature") != signature:
            return None
        value = cached.get("value")
        return dict(value) if isinstance(value, dict) else None


def _completed_delivery_cache_set(
    source: Path,
    signature: tuple[Any, ...],
    value: dict[str, Any],
) -> dict[str, Any]:
    with _COMPLETED_DELIVERY_STATUS_CACHE_LOCK:
        if len(_COMPLETED_DELIVERY_STATUS_CACHE) >= 2048:
            _COMPLETED_DELIVERY_STATUS_CACHE.clear()
        _COMPLETED_DELIVERY_STATUS_CACHE[str(source)] = {
            "signature": signature,
            "value": dict(value),
        }
    return value


def _completed_delivery_status(
    video: str,
    config: dict[str, Any],
    *,
    completed: bool,
    ledger_available: bool | None = None,
    evidence: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if not _completed_delivery_feature_enabled(config):
        return _completed_delivery_payload(enabled=False, state="disabled")
    if not completed:
        return _completed_delivery_payload(enabled=True, state="pending")
    try:
        source, destination, receipt, marker, manifest = (
            _completed_delivery_expected_paths(video, config)
        )
    except (OSError, TypeError, ValueError) as exc:
        return _completed_delivery_payload(
            enabled=True,
            state="invalid",
            error=str(exc) or "completed_delivery_configuration_invalid",
        )

    if marker.is_file():
        try:
            timeout = float(config.get("completed_delivery_timeout_seconds") or 7200)
            marker_age = float(time.time() if now is None else now) - marker.stat().st_mtime
        except (OSError, TypeError, ValueError):
            timeout = 0.0
            marker_age = math.inf
        stale = not math.isfinite(timeout) or timeout <= 0 or marker_age > timeout
        return _completed_delivery_payload(
            enabled=True,
            state="stale" if stale else "delivering",
            error="delivery_marker_stale" if stale else "delivery_in_progress",
        )
    if not receipt.is_file():
        return _completed_delivery_payload(
            enabled=True,
            state="missing",
            error="receipt_missing",
        )

    if ledger_available is None:
        ledger_available, evidence_by_path = _completed_delivery_ledger_evidence(
            [str(source)]
        )
        evidence = evidence_by_path.get(str(source))
    signature = _completed_delivery_cache_signature(
        source=source,
        destination=destination,
        receipt=receipt,
        marker=marker,
        manifest=manifest,
        ledger_available=bool(ledger_available),
        evidence=evidence,
    )
    cached = _completed_delivery_cache_get(source, signature)
    if cached is not None:
        return cached

    def finish(state: str, error: str) -> dict[str, Any]:
        return _completed_delivery_cache_set(
            source,
            signature,
            _completed_delivery_payload(enabled=True, state=state, error=error),
        )

    try:
        receipt_stat_before = receipt.stat()
        if (
            int(receipt_stat_before.st_size) <= 0
            or int(receipt_stat_before.st_size) > COMPLETED_DELIVERY_RECEIPT_MAX_BYTES
        ):
            return finish("invalid", "receipt_size_invalid")
        receipt_bytes = receipt.read_bytes()
        receipt_stat_after = receipt.stat()
        if (
            int(receipt_stat_before.st_size) != int(receipt_stat_after.st_size)
            or int(receipt_stat_before.st_mtime_ns)
            != int(receipt_stat_after.st_mtime_ns)
        ):
            return finish("stale", "receipt_changed_during_read")
        payload = json.loads(receipt_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return finish("invalid", "receipt_unreadable")
    if not isinstance(payload, dict):
        return finish("invalid", "receipt_not_object")
    if (
        payload.get("schema_version") != COMPLETED_DELIVERY_SCHEMA_VERSION
        or isinstance(payload.get("schema_version"), bool)
        or payload.get("contract") != COMPLETED_DELIVERY_CONTRACT
        or payload.get("state") != "committed"
        or payload.get("source_retained") is not True
        or not isinstance(payload.get("attempt_id"), str)
        or not str(payload.get("attempt_id") or "")
    ):
        return finish("invalid", "receipt_contract_invalid")

    source_payload = payload.get("source")
    delivery_payload = payload.get("delivery")
    publication_manifest = payload.get("publication_manifest")
    publication = payload.get("publication")
    output = payload.get("output")
    if not all(
        isinstance(item, dict)
        for item in (
            source_payload,
            delivery_payload,
            publication_manifest,
            publication,
            output,
        )
    ):
        return finish("invalid", "receipt_fields_invalid")
    assert isinstance(source_payload, dict)
    assert isinstance(delivery_payload, dict)
    assert isinstance(publication_manifest, dict)
    assert isinstance(publication, dict)
    assert isinstance(output, dict)

    if str(source_payload.get("canonical_path") or "") != str(source):
        return finish("stale", "source_identity_stale")
    source_signature = _completed_delivery_file_signature(source)
    if not source_signature[0]:
        return finish("missing", "source_missing")
    try:
        source_size = int(source_payload.get("media_size"))
        source_mtime_ns = int(source_payload.get("media_mtime_ns"))
    except (TypeError, ValueError):
        return finish("invalid", "source_identity_invalid")
    if source_size != source_signature[1] or source_mtime_ns != source_signature[2]:
        return finish("stale", "source_identity_stale")
    source_sha256 = str(source_payload.get("sha256") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        return finish("invalid", "source_hash_invalid")
    if not str(source_payload.get("media_fingerprint") or ""):
        return finish("invalid", "source_fingerprint_missing")

    obligation_id = str(delivery_payload.get("obligation_id") or "")
    policy_revision = str(delivery_payload.get("policy_revision") or "")
    if not obligation_id or not policy_revision:
        return finish("invalid", "delivery_identity_invalid")
    if str(payload.get("destination") or "") != str(destination):
        return finish("stale", "destination_identity_stale")
    if str(publication_manifest.get("path") or "") != str(manifest):
        return finish("stale", "publication_manifest_identity_stale")
    manifest_sha256 = str(publication_manifest.get("sha256") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        return finish("invalid", "publication_manifest_hash_invalid")

    kind = str(publication.get("kind") or "")
    languages = publication.get("output_languages")
    valid_publication = bool(
        publication.get("contract") == AI_DELIVERY_PUBLICATION_CONTRACT
        and (
            (
                kind == "translated_trilingual"
                and languages == list(AI_DELIVERY_TRANSLATED_LANGUAGES)
            )
            or (
                kind in {"adopted_zh_tw", "converted_zh_cn"}
                and languages == list(AI_DELIVERY_TRADITIONAL_CHINESE_LANGUAGES)
            )
        )
    )
    if not valid_publication:
        return finish("invalid", "publication_semantics_invalid")

    try:
        committed_at = float(payload.get("committed_at"))
        output_size = int(output.get("size"))
        output_mtime_ns = int(output.get("mtime_ns"))
    except (TypeError, ValueError):
        return finish("invalid", "output_identity_invalid")
    output_sha256 = str(output.get("sha256") or "").casefold()
    if (
        not math.isfinite(committed_at)
        or committed_at <= 0
        or output_size <= 0
        or output_mtime_ns <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", output_sha256)
        or str(output.get("path") or "") != str(destination)
    ):
        return finish("invalid", "output_identity_invalid")
    output_signature = _completed_delivery_file_signature(destination)
    if not output_signature[0]:
        return finish("missing", "final_artifact_missing")
    if output_size != output_signature[1] or output_mtime_ns != output_signature[2]:
        return finish("stale", "final_artifact_stale")

    if ledger_available is not True:
        return finish("invalid", "worker_ledger_unavailable")
    if not isinstance(evidence, dict):
        return finish("invalid", "worker_delivery_evidence_missing")
    receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
    try:
        evidence_committed_at = float(evidence.get("committed_at"))
        evidence_verified_at = float(evidence.get("verified_at"))
    except (TypeError, ValueError):
        return finish("invalid", "worker_delivery_evidence_invalid")
    if (
        str(evidence.get("obligation_id") or "") != obligation_id
        or str(evidence.get("policy_revision") or "") != policy_revision
        or str(evidence.get("receipt_path") or "") != str(receipt)
        or str(evidence.get("receipt_sha256") or "").casefold() != receipt_sha256
        or not math.isfinite(evidence_committed_at)
        or abs(evidence_committed_at - committed_at) > AI_DELIVERY_DUE_TOLERANCE_SECONDS
        or not math.isfinite(evidence_verified_at)
        or abs(evidence_verified_at - committed_at) > AI_DELIVERY_DUE_TOLERANCE_SECONDS
    ):
        return finish("invalid", "worker_delivery_evidence_invalid")

    if not manifest.is_file():
        return finish("missing", "publication_manifest_missing")
    if _completed_delivery_sha256(manifest) != manifest_sha256:
        return finish("stale", "publication_manifest_stale")
    if _completed_delivery_sha256(source) != source_sha256:
        return finish("stale", "source_hash_stale")
    if _completed_delivery_sha256(destination) != output_sha256:
        return finish("stale", "final_artifact_hash_stale")
    final_signature = _completed_delivery_cache_signature(
        source=source,
        destination=destination,
        receipt=receipt,
        marker=marker,
        manifest=manifest,
        ledger_available=True,
        evidence=evidence,
    )
    if final_signature != signature:
        return finish("stale", "delivery_evidence_changed_during_validation")
    return _completed_delivery_cache_set(
        source,
        signature,
        _completed_delivery_payload(
            enabled=True,
            available=True,
            state="committed",
            final_path=str(destination),
            committed_at=committed_at,
            size=output_size,
            output_hash=output_sha256,
        ),
    )


def _completed_delivery_overview(config: dict[str, Any]) -> dict[str, Any]:
    if not _completed_delivery_feature_enabled(config):
        return _completed_delivery_payload(enabled=False, state="disabled")
    recent = _recent_ai_completed_summary(limit=1)
    if not recent:
        return _completed_delivery_payload(enabled=True, state="waiting")
    path = str(recent[0].get("path") or "")
    ledger_available, evidence_by_path = _completed_delivery_ledger_evidence([path])
    canonical = str(Path(path).resolve(strict=False)) if path else ""
    return _completed_delivery_status(
        path,
        config,
        completed=True,
        ledger_available=ledger_available,
        evidence=evidence_by_path.get(canonical),
    )


def _completed_delivery_task_details(
    items: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    completed_paths = [
        str(item.get("path") or "")
        for item in items
        if str(
            item.get("effective_status")
            or item.get("raw_status")
            or item.get("status")
            or ""
        ).casefold()
        in {"done", "success", "completed"}
    ]
    if _completed_delivery_feature_enabled(config) and completed_paths:
        ledger_available, evidence_by_path = _completed_delivery_ledger_evidence(
            completed_paths
        )
    else:
        ledger_available, evidence_by_path = True, {}
    result: list[dict[str, Any]] = []
    for item in items:
        path = str(item.get("path") or "")
        status = str(
            item.get("effective_status")
            or item.get("raw_status")
            or item.get("status")
            or ""
        ).casefold()
        canonical = str(Path(path).resolve(strict=False)) if path else ""
        result.append(
            {
                **item,
                "completed_delivery": _completed_delivery_status(
                    path,
                    config,
                    completed=status in {"done", "success", "completed"},
                    ledger_available=ledger_available,
                    evidence=evidence_by_path.get(canonical),
                ),
            }
        )
    return result


def _control_state_db_path(config: dict[str, Any]) -> Path:
    return configured_path(
        config,
        _configured_work_path(config),
        "control_state_path",
        "control_state.sqlite3",
        _expand_config_env,
    )


def _control_inbox_dir(config: dict[str, Any]) -> Path:
    return configured_path(
        config,
        _configured_work_path(config),
        "control_inbox_path",
        "control_inbox",
        _expand_config_env,
    )


def _enqueue_legacy_worker_command(
    *,
    action: str,
    target: str = "",
    parameters: dict[str, Any] | None = None,
    request: Request | None = None,
    scope: str,
) -> dict[str, Any]:
    """Compatibility bridge that keeps legacy APIs read-only toward Worker state.

    Legacy clients did not send idempotency keys. A short deterministic time
    bucket collapses accidental double taps while still allowing an intentional
    retry a few seconds later. New clients should use /api/v2/commands.
    """
    headers = getattr(request, "headers", {}) if request is not None else {}
    explicit_key = str(headers.get("idempotency-key", "") or "").strip()
    normalized_parameters = parameters if isinstance(parameters, dict) else {}
    if explicit_key:
        idempotency_key = explicit_key[:200]
    else:
        canonical = json.dumps(
            {
                "scope": str(scope),
                "action": str(action),
                "target": str(target),
                "parameters": normalized_parameters,
                "bucket": int(time.time() // 5),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        idempotency_key = f"legacy-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    config = _load_config()
    command = enqueue_atomic_command(
        config=config,
        work_path=_configured_work_path(config),
        expand=_expand_config_env,
        action=action,
        target=target,
        parameters=normalized_parameters,
        idempotency_key=idempotency_key,
    )
    return {
        **command,
        "ok": True,
        "started": True,
        "accepted": str(command.get("status") or "") in {"accepted", "queued", "running"},
    }


def _library_summary(limit: int = 20) -> dict[str, Any]:
    cache_key = (max(1, min(int(limit), 200)), str(WORK_PATH / "scanner_state.sqlite3"))
    cached = _ttl_cache_get(_LIBRARY_SUMMARY_CACHE, cache_key, SUMMARY_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    db_path = WORK_PATH / "scanner_state.sqlite3"
    if not db_path.exists():
        return _ttl_cache_set(
            _LIBRARY_SUMMARY_CACHE,
            cache_key,
            {"database": str(db_path), "exists": False, "table_exists": False, "counts": {}, "total": 0},
        )

    try:
        with _sqlite_connect(db_path, readonly=True) as conn:
            if not _sqlite_table_exists(conn, "video_scan_cache"):
                return _ttl_cache_set(_LIBRARY_SUMMARY_CACHE, cache_key, {
                    "database": str(db_path),
                    "exists": True,
                    "table_exists": False,
                    "counts": {},
                    "total": 0,
                })
            counts = {
                str(status): int(count)
                for status, count in conn.execute(
                    "SELECT status, COUNT(*) FROM video_scan_cache GROUP BY status"
                ).fetchall()
            }
            recent_needs_ai = [
                {"path": str(path), "status": str(status), "updated_at": float(updated_at or 0)}
                for path, status, updated_at in conn.execute(
                    """
                    SELECT path, status, updated_at
                    FROM video_scan_cache
                    WHERE status = 'needs_ai'
                    ORDER BY updated_at DESC, path COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 200)),),
                ).fetchall()
            ]
    except sqlite3.Error as exc:
        return _ttl_cache_set(_LIBRARY_SUMMARY_CACHE, cache_key, {
            "database": str(db_path),
            "exists": True,
            "table_exists": True,
            "error": str(exc),
            "counts": {},
            "total": 0,
        })

    total = sum(counts.values())
    subtitle_ready = sum(counts.get(key, 0) for key in ("finished", "local_chinese", "embedded_chinese"))
    needs_ai = counts.get("needs_ai", 0)
    return _ttl_cache_set(_LIBRARY_SUMMARY_CACHE, cache_key, {
        "database": str(db_path),
        "exists": True,
        "table_exists": True,
        "counts": counts,
        "total": total,
        "needs_ai": needs_ai,
        "subtitle_ready": subtitle_ready,
        "recent_needs_ai": recent_needs_ai,
    })


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


class _ClosingSQLiteConnection(sqlite3.Connection):
    """Commit or roll back a ``with`` block, then release the SQLite handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _sqlite_connect(path: Path, *, readonly: bool = True) -> sqlite3.Connection:
    """Open a WebUI state database under an enforced read-only contract."""

    if not readonly:
        raise RuntimeError("WebUI SQLite writes are prohibited; submit a Worker command instead")
    conn = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&cache=private",
        timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
        uri=True,
        factory=_ClosingSQLiteConnection,
    )
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
    return conn


def _fast_queue_counts() -> dict[str, int]:
    db_path = WORK_PATH / "scanner_state.sqlite3"
    cache_key = str(db_path)
    cached = _ttl_cache_get(
        _FAST_QUEUE_COUNTS_CACHE,
        cache_key,
        FAST_QUEUE_COUNTS_CACHE_TTL_SECONDS,
    )
    if cached is not None:
        return dict(cached)
    if not db_path.exists():
        return _ttl_cache_set(_FAST_QUEUE_COUNTS_CACHE, cache_key, {})
    try:
        with _sqlite_connect(db_path, readonly=True) as conn:
            if not _sqlite_table_exists(conn, "ai_candidate_queue"):
                return {}
            if not _sqlite_table_exists(conn, "ai_job_state"):
                rows = conn.execute(
                    "SELECT status, COUNT(*) FROM ai_candidate_queue GROUP BY status"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT effective_status, COUNT(*)
                    FROM (
                        SELECT CASE
                            WHEN q.status = 'running' THEN 'running'
                            WHEN q.status = 'done' AND j.status = 'skipped' THEN 'skipped'
                            WHEN q.status = 'done' THEN 'done'
                            WHEN q.status IN ('failed_retry', 'paused', 'skipped') THEN q.status
                            WHEN j.status IN ('ok', 'done') THEN 'done'
                            ELSE q.status
                        END AS effective_status
                        FROM ai_candidate_queue q
                        LEFT JOIN ai_job_state j ON j.path = q.path
                    ) current_queue
                    GROUP BY effective_status
                    """
                ).fetchall()
            return _ttl_cache_set(
                _FAST_QUEUE_COUNTS_CACHE,
                cache_key,
                {str(status): int(count) for status, count in rows},
            )
    except sqlite3.Error:
        return {}


def _fast_current_ai(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    db_path = WORK_PATH / "scanner_state.sqlite3"
    if not db_path.exists():
        return None
    try:
        with _sqlite_connect(db_path, readonly=True) as conn:
            if not _sqlite_table_exists(conn, "ai_candidate_queue"):
                return None
            queue_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ai_candidate_queue)").fetchall()}
            if "path" not in queue_columns or "status" not in queue_columns:
                return None
            has_job_table = _sqlite_table_exists(conn, "ai_job_state")
            job_columns = (
                {str(row[1]) for row in conn.execute("PRAGMA table_info(ai_job_state)").fetchall()}
                if has_job_table
                else set()
            )

            def qcol(name: str, fallback: str = "NULL") -> str:
                return f"q.{name}" if name in queue_columns else fallback

            def jcol(name: str, fallback: str = "NULL") -> str:
                return f"j.{name}" if name in job_columns else fallback

            join_sql = "LEFT JOIN ai_job_state j ON j.path = q.path" if has_job_table else ""
            row = conn.execute(
                f"""
                SELECT
                    q.path,
                    q.status,
                    {qcol("running_at")} AS running_at,
                    {qcol("updated_at", "0")} AS queue_updated_at,
                    {qcol("last_error")} AS last_error,
                    {jcol("stage")} AS stage,
                    {jcol("status")} AS job_status,
                    {jcol("message")} AS message,
                    {jcol("started_at")} AS started_at,
                    {jcol("updated_at", "0")} AS job_updated_at
                FROM ai_candidate_queue q
                {join_sql}
                WHERE q.status = 'running'
                ORDER BY
                    COALESCE({jcol("updated_at")}, {qcol("updated_at")}, {qcol("running_at")}, {qcol("added_at")}, 0) DESC,
                    q.path COLLATE NOCASE ASC
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None

    now = time.time()
    path = str(row[0] or "")
    stage = str(row[5] or row[1] or "")
    job_status = str(row[6] or row[1] or "")
    message = str(row[7] or row[4] or "")
    running_started_at = float(row[2] or row[8] or 0)
    job_updated_at = float(row[9] or row[3] or running_started_at or 0)
    stale_after = _config_stale_running_seconds(config or {})
    heartbeat_age = max(0.0, now - job_updated_at) if job_updated_at else 0.0
    return {
        "path": path,
        "file_name": Path(path).name if path else "unknown",
        "stage": stage,
        "status": "Running",
        "job_status": job_status,
        "message": message,
        "running_started_at": running_started_at,
        "updated_at": job_updated_at,
        "heartbeat_age_seconds": heartbeat_age,
        "stale_after_seconds": stale_after,
        "running_stale": bool(job_updated_at and heartbeat_age >= stale_after),
    }


def _recent_ai_completed_summary(limit: int = 8) -> list[dict[str, Any]]:
    db_path = WORK_PATH / "scanner_state.sqlite3"
    if not db_path.exists():
        return []
    limit_value = max(1, min(int(limit), 50))
    rows: list[dict[str, Any]] = []
    try:
        with _sqlite_connect(db_path, readonly=True) as conn:
            if _sqlite_table_exists(conn, "ai_job_state"):
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ai_job_state)").fetchall()}
                required = {"path", "status"}
                if required.issubset(columns):
                    queue_completion_filter = ""
                    if _sqlite_table_exists(conn, "ai_candidate_queue"):
                        queue_columns = {
                            str(row[1])
                            for row in conn.execute("PRAGMA table_info(ai_candidate_queue)").fetchall()
                        }
                        if {"path", "status"}.issubset(queue_columns):
                            queue_completion_filter = """
                              AND (
                                  NOT EXISTS (
                                      SELECT 1 FROM ai_candidate_queue q
                                      WHERE q.path = ai_job_state.path
                                  )
                                  OR EXISTS (
                                      SELECT 1 FROM ai_candidate_queue q
                                      WHERE q.path = ai_job_state.path
                                        AND q.status = 'done'
                                  )
                              )
                            """
                    finished_expr = "COALESCE(finished_at, updated_at, started_at, 0)" if "finished_at" in columns else "COALESCE(updated_at, started_at, 0)"
                    stage_expr = "stage" if "stage" in columns else "'complete'"
                    message_expr = "message" if "message" in columns else "''"
                    started_expr = "started_at" if "started_at" in columns else "0"
                    updated_expr = "updated_at" if "updated_at" in columns else "0"
                    rows.extend(
                        {
                            "path": str(path),
                            "file_name": Path(str(path)).name if path else "unknown",
                            "status": "Success",
                            "raw_status": "done",
                            "node_id": "output",
                            "node_label": _workflow_node_label("output"),
                            "stage": str(stage or "complete"),
                            "job_status": str(job_status or "ok"),
                            "message": str(message or ""),
                            "started_at": float(started_at or 0),
                            "updated_at": float(updated_at or completed_at or 0),
                            "completed_at": float(completed_at or updated_at or 0),
                            "source": "ai",
                            "completion_kind": "generated",
                            "completion_label": "AI 字幕生成完成",
                        }
                        for path, stage, job_status, message, started_at, updated_at, completed_at in conn.execute(
                            f"""
                            SELECT path, {stage_expr}, status, {message_expr}, {started_expr}, {updated_expr}, {finished_expr}
                            FROM ai_job_state
                            WHERE (
                                status IN ('ok', 'done', 'success', 'finished')
                                OR ({stage_expr} = 'complete' AND status IN ('ok', 'done'))
                            )
                            {queue_completion_filter}
                            ORDER BY {finished_expr} DESC, path COLLATE NOCASE ASC
                            LIMIT ?
                            """,
                            (limit_value * 2,),
                        ).fetchall()
                    )
            if _sqlite_table_exists(conn, "ai_candidate_queue"):
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ai_candidate_queue)").fetchall()}
                if {"path", "status"}.issubset(columns):
                    if "updated_at" in columns and "added_at" in columns:
                        updated_expr = "COALESCE(q.updated_at, q.added_at, 0)"
                    elif "updated_at" in columns:
                        updated_expr = "COALESCE(q.updated_at, 0)"
                    elif "added_at" in columns:
                        updated_expr = "COALESCE(q.added_at, 0)"
                    else:
                        updated_expr = "0"
                    message_expr = "q.last_error" if "last_error" in columns else "''"
                    queue_job_join = (
                        "LEFT JOIN ai_job_state j ON j.path = q.path"
                        if _sqlite_table_exists(conn, "ai_job_state")
                        else ""
                    )
                    skipped_filter = "AND COALESCE(j.status, '') <> 'skipped'" if queue_job_join else ""
                    rows.extend(
                        {
                            "path": str(path),
                            "file_name": Path(str(path)).name if path else "unknown",
                            "status": "Success",
                            "raw_status": "done",
                            "node_id": "output",
                            "node_label": _workflow_node_label("output"),
                            "stage": "complete",
                            "job_status": "done",
                            "message": str(message or "AI subtitle completed"),
                            "updated_at": float(completed_at or 0),
                            "completed_at": float(completed_at or 0),
                            "source": "ai",
                            "completion_kind": "detected_existing",
                            "completion_label": "掃描確認已有 AI 字幕",
                        }
                        for path, message, completed_at in conn.execute(
                            f"""
                            SELECT q.path, {message_expr}, {updated_expr}
                            FROM ai_candidate_queue q
                            {queue_job_join}
                            WHERE q.status = 'done'
                              {skipped_filter}
                            ORDER BY {updated_expr} DESC, q.path COLLATE NOCASE ASC
                            LIMIT ?
                            """,
                            (limit_value * 2,),
                        ).fetchall()
                    )
    except sqlite3.Error:
        return []

    normalized_rows = [_normalize_ai_completed_summary_item(item) for item in rows]
    deduped: dict[str, dict[str, Any]] = {}
    for item in sorted(normalized_rows, key=lambda row: (-float(row.get("completed_at") or 0), str(row.get("path") or ""))):
        path = str(item.get("path") or "")
        if path and path not in deduped:
            deduped[path] = item
        if len(deduped) >= limit_value:
            break
    return list(deduped.values())


def _normalize_ai_completed_summary_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    stage = str(normalized.get("stage") or "")
    message = str(normalized.get("message") or "")
    detected_existing = (
        str(normalized.get("completion_kind") or "") == "detected_existing"
        or _is_detected_existing_ai_completion(stage, message)
    )
    if detected_existing:
        path = str(normalized.get("path") or "")
        file_time = _ai_subtitle_completion_time_for_video(path)
        if file_time:
            normalized["completed_at"] = file_time
            normalized["updated_at"] = file_time
        normalized["stage"] = "detected_existing"
        normalized["completion_kind"] = "detected_existing"
        normalized["completion_label"] = "掃描確認已有 AI 字幕"
    return normalized


def _events_summary(limit: int = 50, *, include_counts: bool = True) -> dict[str, Any]:
    db_path = WORK_PATH / "scanner_state.sqlite3"
    config = _load_config()
    mikan_db_path = _mikan_state_db_path(config)
    limit = max(1, min(int(limit), 1000))
    cache_key = (limit, bool(include_counts), str(db_path), str(mikan_db_path))
    cached = _ttl_cache_get(_EVENTS_SUMMARY_CACHE, cache_key, SUMMARY_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    recent: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    table_exists = False
    errors: list[str] = []
    try:
        if db_path.exists():
            with _sqlite_connect(db_path, readonly=True) as conn:
                table_exists = _sqlite_table_exists(conn, "ai_stage_events")
                if table_exists:
                    if include_counts:
                        counts.update(
                            {
                                f"{stage}:{status}": int(count)
                                for stage, status, count in conn.execute(
                                    """
                                    SELECT stage, status, COUNT(*)
                                    FROM ai_stage_events
                                    WHERE created_at >= ?
                                    GROUP BY stage, status
                                    """,
                                    (time.time() - 24 * 3600,),
                                ).fetchall()
                            }
                        )
                    recent.extend(
                        {
                            "id": int(event_id),
                            "source": "ai",
                            "path": str(path),
                            "stage": str(stage),
                            "status": str(status),
                            "message": str(message or ""),
                            "created_at": float(created_at or 0),
                        }
                        for event_id, path, stage, status, message, created_at in conn.execute(
                            """
                            SELECT id, path, stage, status, message, created_at
                            FROM ai_stage_events
                            ORDER BY created_at DESC, id DESC
                            LIMIT ?
                            """,
                            (limit,),
                        ).fetchall()
                    )
    except sqlite3.Error as exc:
        errors.append(f"ai: {exc}")

    mikan_events = _mikan_timeline_events_summary(config, limit=limit, include_counts=include_counts)
    table_exists = table_exists or bool(mikan_events.get("table_exists"))
    for key, count in mikan_events.get("counts", {}).items():
        counts[str(key)] = counts.get(str(key), 0) + int(count)
    recent.extend(mikan_events.get("recent", []))
    if mikan_events.get("error"):
        errors.append(f"mikan: {mikan_events['error']}")

    recent = sorted(recent, key=lambda event: (float(event.get("created_at") or 0), str(event.get("id"))), reverse=True)[:limit]
    exists = db_path.exists() or bool(mikan_events.get("exists"))
    result = {
        "database": str(db_path),
        "mikan_database": str(mikan_db_path),
        "exists": exists,
        "table_exists": table_exists,
        "recent": recent,
        "counts": counts,
    }
    if errors:
        result["error"] = "; ".join(errors)
    return _ttl_cache_set(_EVENTS_SUMMARY_CACHE, cache_key, {
        **result,
    })


def _mikan_timeline_events_summary(
    config: dict[str, Any],
    *,
    limit: int = 50,
    include_counts: bool = True,
) -> dict[str, Any]:
    db_path = _mikan_state_db_path(config)
    result: dict[str, Any] = {
        "database": str(db_path),
        "exists": False,
        "table_exists": False,
        "recent": [],
        "counts": {},
    }
    if not db_path.exists():
        return result
    conn: sqlite3.Connection | None = None
    try:
        conn = _sqlite_connect(db_path, readonly=True)
        if not _sqlite_table_exists(conn, "mikan_download_events"):
            return {**result, "exists": True}
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(mikan_download_events)").fetchall()}
        occurrence_expr = "MAX(1, COALESCE(occurrence_count, 1))" if "occurrence_count" in columns else "1"
        detail_json_expr = "detail_json" if "detail_json" in columns else "'{}'"
        last_seen_expr = "COALESCE(NULLIF(last_seen_at, 0), created_at)" if "last_seen_at" in columns else "created_at"
        counts = {}
        if include_counts:
            cutoff = time.time() - 24 * 3600
            counts = {
                f"mikan:{event}": int(count)
                for event, count in conn.execute(
                    """
                    SELECT event, SUM({occurrence_expr})
                    FROM mikan_download_events
                    WHERE {last_seen_expr} >= ?
                    GROUP BY event
                    """.format(occurrence_expr=occurrence_expr, last_seen_expr=last_seen_expr),
                    (cutoff,),
                ).fetchall()
            }
        recent = [
            {
                "id": f"mikan-{event_id}",
                "source": "mikan",
                "path": str(key),
                "stage": "mikan",
                "status": str(event or ""),
                "severity": _mikan_timeline_event_severity(event, detail, _json_object(detail_json)),
                "message": _mikan_timeline_message(key, bangumi_id, episode, detail),
                "detail_data": _json_object(detail_json),
                "occurrence_count": max(1, int(occurrence_count or 1)),
                "created_at": float(last_seen_at or created_at or 0),
            }
            for event_id, key, bangumi_id, episode, event, detail, detail_json, occurrence_count, last_seen_at, created_at in conn.execute(
                f"""
                SELECT id, key, bangumi_id, episode, event, detail,
                       {detail_json_expr}, {occurrence_expr}, {last_seen_expr}, created_at
                FROM mikan_download_events
                ORDER BY {last_seen_expr} DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        return {
            **result,
            "exists": True,
            "table_exists": True,
            "recent": recent,
            "counts": counts,
        }
    except sqlite3.Error as exc:
        return {**result, "exists": True, "error": str(exc)}
    finally:
        if conn is not None:
            conn.close()


def _mikan_timeline_message(key: Any, bangumi_id: Any, episode: Any, detail: Any) -> str:
    prefix_parts = []
    if bangumi_id not in (None, ""):
        prefix_parts.append(f"bangumi {bangumi_id}")
    if episode not in (None, ""):
        prefix_parts.append(f"EP {int(episode):02d}" if _coerce_int(episode) is not None else f"EP {episode}")
    prefix = " / ".join(prefix_parts) or str(key or "")
    detail_text = str(detail or "")
    return f"{prefix} - {detail_text}" if detail_text else prefix


def _mikan_timeline_event_severity(
    event: Any,
    detail: Any,
    detail_data: dict[str, Any] | None = None,
) -> str:
    event_text = str(event or "").casefold()
    detail_text = str(detail or "").casefold()
    current_status = str((detail_data or {}).get("status") or "").casefold()
    status_match = re.search(r"\bstatus=([^\s]+)", detail_text)
    if not current_status and status_match:
        current_status = status_match.group(1).split("->")[-1]

    if event_text == "failure_recorded":
        return "warn"
    if current_status in {"completed", "success"}:
        return "success"
    if current_status in {"target_missing", "terminal_failed"}:
        return "danger"
    if current_status in {"extract_failed", "failed_candidate"}:
        return "warn"
    if current_status in {"no_candidate_retry", "replaced"}:
        return "muted"
    if current_status in {"downloading", "extracting_subtitles"}:
        return "running"
    return "queued"


def _v2_event_payload(event: dict[str, Any], *, detail: bool) -> dict[str, Any]:
    source = str(event.get("source") or "system")
    code = str(event.get("status") or "updated")
    stage = str(event.get("stage") or source)
    severity = str(event.get("severity") or _generic_event_severity(code))
    title = _event_title(source=source, stage=stage, code=code, severity=severity)
    description = _event_description(event, title=title)
    path = str(event.get("path") or "")
    normalized_path = path.replace("\\", "/").rstrip("/")
    payload: dict[str, Any] = {
        "id": str(event.get("id") or ""),
        "source": source,
        "code": code,
        "severity": severity,
        "title": title,
        "description": description,
        "entity": normalized_path.rsplit("/", 1)[-1] if normalized_path else "",
        "occurrence_count": max(1, int(event.get("occurrence_count") or 1)),
        "occurred_at": float(event.get("created_at") or 0),
    }
    if detail:
        payload["path"] = path
        payload["technical_detail"] = str(event.get("message") or "")[:2000]
        payload["data"] = dict(event.get("detail_data") or {})
    return payload


def _generic_event_severity(code: str) -> str:
    normalized = str(code or "").casefold()
    if "fail" in normalized or normalized in {"error", "terminal_failed"}:
        return "danger"
    if normalized in {"warning", "warn", "check", "target_missing"}:
        return "warn"
    if normalized in {"ok", "success", "completed", "done"}:
        return "success"
    if normalized in {"running", "downloading", "extracting_subtitles"}:
        return "running"
    return "queued"


def _event_title(*, source: str, stage: str, code: str, severity: str) -> str:
    if source == "mikan":
        return {
            "created": "已加入字幕來源工作",
            "source_changed": "已切換字幕來源",
            "status_changed": "字幕來源狀態已更新",
            "failure_recorded": "字幕來源需要處理",
        }.get(code, "字幕來源狀態已更新")
    stage_label = {
        "transcription": "AI 轉錄",
        "translation": "AI 翻譯",
        "complete": "AI 字幕",
        "quality": "字幕品質檢查",
    }.get(stage, "AI 字幕")
    if severity in {"danger", "warn"}:
        return f"{stage_label}需要處理"
    if severity == "success":
        return f"{stage_label}已完成"
    return f"{stage_label}處理中"


def _event_description(event: dict[str, Any], *, title: str) -> str:
    data = dict(event.get("detail_data") or {})
    status = str(data.get("status") or "")
    reason = str(data.get("reason") or "")
    status_labels = {
        "queued": "工作已排入佇列。",
        "downloading": "字幕來源正在下載。",
        "completed_waiting_extract": "下載完成，正在等待字幕提取。",
        "extracting_subtitles": "正在提取字幕。",
        "completed": "字幕來源已處理完成。",
        "extract_failed": "字幕提取失敗，系統會保留來源供重試。",
        "target_missing": "找不到可安全配對的影片，需要人工確認。",
        "failed_candidate": "這個來源不適用，系統會尋找其他候選。",
    }
    reason_labels = {
        "target_ambiguity": "找到多個可能季度，系統已停止自動配對並等待確認。",
        "no_subtitle_streams": "來源中沒有可用的中文字幕軌，系統會尋找其他來源。",
        "source_video_missing": "下載內容不完整，系統會保留紀錄並嘗試修復。",
        "source_torrent_missing_before_extract": "原始下載已不在 qBittorrent，系統會重新整理來源狀態。",
    }
    if reason in reason_labels:
        return reason_labels[reason]
    if status in status_labels:
        return status_labels[status]
    if str(event.get("source") or "") == "ai":
        severity = str(event.get("severity") or "")
        if severity == "success":
            return "本階段已通過檢查。"
        if severity in {"danger", "warn"}:
            return "系統已保留工作狀態，可在人工審核中查看建議處理方式。"
        return "系統正在處理這個字幕工作。"
    return title


_PROBLEM_PRESENTATIONS: dict[str, dict[str, Any]] = {
    "queued": {
        "severity": "info", "title": "等待處理", "description": "工作已排入佇列，Worker 會依順序處理。",
        "system_action": "保留工作並持續排程。", "recommended_action": "不需要操作。",
    },
    "running": {
        "severity": "info", "title": "正在處理", "description": "Worker 正在執行這個工作。",
        "system_action": "持續更新進度與心跳。", "recommended_action": "不需要操作。",
    },
    "paused": {
        "severity": "warning", "title": "AI 已安全暫停", "description": "這次處理未通過，字幕未發布。",
        "system_action": "保持封鎖並保留工作與診斷。", "recommended_action": "若持續暫停，再到審核中心查看。",
    },
    "downloading": {
        "severity": "info", "title": "正在下載字幕來源", "description": "qBittorrent 正在下載來源檔案。",
        "system_action": "監看下載速度，停滯逾時後自動更換來源。", "recommended_action": "不需要操作。",
    },
    "completed_waiting_extract": {
        "severity": "info", "title": "等待提取字幕", "description": "來源已下載完成，Worker 會在資源允許時立即提取。",
        "system_action": "已喚醒字幕提取排程。", "recommended_action": "不需要操作。",
    },
    "extracting_subtitles": {
        "severity": "info", "title": "正在提取字幕", "description": "Worker 正在檢查來源檔並提取可用中文字幕。",
        "system_action": "持續更新檔案進度與心跳。", "recommended_action": "不需要操作。",
    },
    "completed": {
        "severity": "success", "title": "字幕已匯入", "description": "字幕已通過檢查並安全發布。",
        "system_action": "保留完成紀錄。", "recommended_action": "不需要操作。",
    },
    "no_candidate_retry": {
        "severity": "warning", "title": "暫時找不到字幕來源", "description": "目前沒有符合條件的來源，系統會在重試時間到達後再搜尋。",
        "system_action": "保留項目並定時重新搜尋。", "recommended_action": "通常不需要操作。",
    },
    "no_subtitle_streams": {
        "severity": "warning", "title": "來源內沒有可用中文字幕", "description": "下載檔案中沒有可安全匯入的中文字幕軌。",
        "system_action": "排除這個來源並尋找替補。", "recommended_action": "等待系統更換來源。",
    },
    "source_torrent_missing_before_extract": {
        "severity": "warning", "title": "下載來源已不在 qBittorrent", "description": "字幕尚未提取前，原始下載已被移除或路徑失效。",
        "system_action": "重新核對下載狀態並尋找可用來源。", "recommended_action": "等待系統修復；若持續出現再檢查 qBittorrent 保留規則。",
    },
    "source_video_missing": {
        "severity": "warning", "title": "下載內容不完整", "description": "來源記錄存在，但實際影片檔目前不可讀取。",
        "system_action": "保留失敗紀錄並嘗試其他來源。", "recommended_action": "檢查下載路徑掛載是否正常。",
    },
    "target_missing": {
        "severity": "warning", "title": "找不到對應的媒體庫影片", "description": "來源已下載，但無法安全決定字幕應放到哪一個影片。",
        "system_action": "停止匯入，避免字幕放錯作品。", "recommended_action": "到人工審核確認作品與季度。", "requires_user_action": True,
    },
    "target_ambiguity": {
        "severity": "warning", "title": "需要確認作品與季度", "description": "找到多個可能目標，系統已停止自動配對以避免放錯字幕。",
        "system_action": "保持封鎖，不匯入字幕也不刪除 torrent。", "recommended_action": "選擇正確作品與季度後重新提取。", "requires_user_action": True,
    },
    "prompt_leak": {
        "severity": "warning", "title": "翻譯混入模型指令", "description": "品質檢查發現 Prompt 或格式指令出現在字幕中。",
        "system_action": "拒絕發布受影響字幕並保留日文快取。", "recommended_action": "使用日文快取重新翻譯。", "requires_user_action": True,
    },
    "translation_safe_omission": {
        "severity": "warning", "title": "翻譯缺漏，已安全暫停", "description": "品質檢查確認翻譯有缺漏，字幕未發布。",
        "system_action": "保持封鎖並保留工作與診斷。", "recommended_action": "若持續暫停，再到審核中心查看。",
    },
    "asr_prompt_echo": {
        "severity": "warning", "title": "轉錄提示混入日文字幕", "description": "Whisper 把系統轉錄指令誤認成影片語音。",
        "system_action": "拒絕發布受影響字幕並封存錯誤快取。", "recommended_action": "從影片音訊重新轉錄。", "requires_user_action": True,
    },
    "residual_japanese_kana": {
        "severity": "warning", "title": "中文字幕仍有未翻譯日文", "description": "品質檢查發現部分字幕行仍含日文假名。",
        "system_action": "只標記問題行，不覆蓋既有良好字幕。", "recommended_action": "只重翻問題行。", "requires_user_action": True,
    },
    "asr_low_confidence": {
        "severity": "warning", "title": "日文轉錄信心不足", "description": "部分語音區段可能聽寫錯誤或有過長空洞。",
        "system_action": "保留已驗證區段並標記問題時間範圍。", "recommended_action": "只重新轉錄問題區段。", "requires_user_action": True,
    },
    "leading_gap": {
        "severity": "warning", "title": "片頭開場可能漏轉", "description": "第一條字幕出現得異常晚，片頭或前幾句語音可能沒有被辨識。",
        "system_action": "保留後續正確字幕並標記片頭時間範圍。", "recommended_action": "只重新轉錄片頭問題區段。", "requires_user_action": True,
    },
    "database_locked": {
        "severity": "warning", "title": "狀態資料庫暫時忙碌", "description": "另一個安全交易正在寫入，這次操作已延後。",
        "system_action": "使用退避機制自動重試，不重啟 Worker。", "recommended_action": "不需要操作；若反覆持續再查看診斷。",
    },
    "ai_failed_retry": {
        "severity": "warning", "title": "AI 工作未完成", "description": "這次處理未通過，工作與快取都已保留，可安全重試。",
        "system_action": "保留失敗階段、重試時間與既有良好輸出。", "recommended_action": "按「重試」；若再次失敗再查看 AI 診斷。", "requires_user_action": True,
    },
    "terminal_failed": {
        "severity": "error", "title": "字幕提取需要人工處理", "description": "自動替補與安全重試仍無法完成這個來源。",
        "system_action": "停止自動循環並保留下載與診斷。", "recommended_action": "按「只重試這一筆」，或在審核中心確認配對。", "requires_user_action": True,
    },
}


def _problem_code_from_text(value: object) -> str:
    text = str(value or "").casefold()
    patterns = (
        ("target_ambiguity", ("target_ambiguity", "ambiguous target", "multiple target")),
        ("no_subtitle_streams", ("no_subtitle_streams", "no subtitle streams", "no usable chinese")),
        ("source_torrent_missing_before_extract", ("source_torrent_missing_before_extract", "torrent missing before extract")),
        ("source_video_missing", ("source_video_missing", "source video missing", "source file missing")),
        (
            "translation_safe_omission",
            (
                "translation_safe_omission",
                "translation safe omission",
                "translation safe-omission",
            ),
        ),
        ("asr_prompt_echo", ("asr_prompt_echo", "asr prompt echo", "echoed transcription instruction")),
        ("prompt_leak", ("prompt_leak", "prompt leak", "model instruction")),
        ("residual_japanese_kana", ("residual_japanese", "residual kana", "japanese kana")),
        ("leading_gap", ("leading_gap", "leading gap", "first subtitle starts unusually late")),
        ("asr_low_confidence", ("asr_low_confidence", "low confidence", "long gap", "asr review")),
        ("database_locked", ("database is locked", "database_locked", "database busy")),
        ("target_missing", ("target_missing", "target video not found", "no matching target")),
    )
    for code, needles in patterns:
        if any(needle in text for needle in needles):
            return code
    return ""


def _problem_presentation(
    code: object,
    *,
    status: object = "",
    retry_at: float = 0,
) -> dict[str, Any]:
    normalized_status = str(status or "").strip().casefold()
    normalized_code = str(code or "").strip().casefold()
    if not re.fullmatch(r"[a-z0-9_:-]{1,80}", normalized_code):
        normalized_code = _problem_code_from_text(code)
    lookup = normalized_code if normalized_code in _PROBLEM_PRESENTATIONS else normalized_status
    if lookup in {"success", "done"}:
        lookup = "completed"
    if lookup not in _PROBLEM_PRESENTATIONS:
        lookup = "ai_failed_retry" if "fail" in normalized_status else (normalized_status or "queued")
    template = dict(_PROBLEM_PRESENTATIONS.get(lookup) or _PROBLEM_PRESENTATIONS["queued"])
    return {
        "code": normalized_code or lookup,
        "severity": str(template.get("severity") or "info"),
        "title": str(template.get("title") or "狀態已更新"),
        "description": str(template.get("description") or "系統已保留目前狀態。"),
        "system_action": str(template.get("system_action") or "保留狀態。"),
        "recommended_action": str(template.get("recommended_action") or "不需要操作。"),
        "retry_at": float(retry_at or 0),
        "requires_user_action": bool(template.get("requires_user_action")),
    }


def _mikan_download_problem(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "")
    if status in {"completed", "success"}:
        code = "completed"
    else:
        raw_reason = row.get("last_extract_failure_reason") or row.get("last_failure_reason") or row.get("last_error")
        code = _problem_code_from_text(raw_reason)
        if not code and re.fullmatch(r"[a-z0-9_:-]{1,80}", str(raw_reason or "").casefold()):
            code = str(raw_reason).casefold()
        code = code or status
    return _problem_presentation(code, status=status, retry_at=float(row.get("no_candidate_until") or 0))


def _ai_task_problem(*, status: str, stage: str, message: str, retry_at: float) -> dict[str, Any]:
    normalized_status = str(status or "").casefold()
    code = _problem_code_from_text(message)
    if not code:
        code = "ai_failed_retry" if normalized_status == "failed_retry" else normalized_status
    return _problem_presentation(code, status=normalized_status, retry_at=retry_at)


def _review_problem(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    if kind == "target_ambiguity":
        return _problem_presentation("target_ambiguity", status="review")
    diagnosis = dict(item.get("diagnosis") or {})
    encoded = json.dumps(diagnosis, ensure_ascii=False, default=str)
    code = _problem_code_from_text(encoded)
    if not code:
        code = "asr_low_confidence" if kind == "asr_quality" else "ai_failed_retry"
    return _problem_presentation(code, status="review")


def _eta_summary() -> dict[str, Any]:
    db_path = WORK_PATH / "scanner_state.sqlite3"
    cache_key = str(db_path)
    cached = _ttl_cache_get(_ETA_SUMMARY_CACHE, cache_key, SUMMARY_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    if not db_path.exists():
        return _ttl_cache_set(
            _ETA_SUMMARY_CACHE,
            cache_key,
            {
                "database": str(db_path),
                "exists": False,
                "available": False,
                "error_code": "scanner_database_missing",
                "remaining": None,
                "rate_per_hour": None,
                "eta_hours": None,
            },
        )

    now = time.time()
    try:
        with _sqlite_connect(db_path, readonly=True) as conn:
            queue_counts = (
                {
                    str(status): int(count)
                    for status, count in conn.execute(
                        "SELECT status, COUNT(*) FROM ai_candidate_queue GROUP BY status"
                    ).fetchall()
                }
                if _sqlite_table_exists(conn, "ai_candidate_queue")
                else {}
            )
            needs_ai = (
                int(
                    conn.execute(
                        "SELECT COUNT(*) FROM video_scan_cache WHERE status = 'needs_ai'"
                    ).fetchone()[0]
                )
                if _sqlite_table_exists(conn, "video_scan_cache")
                else 0
            )
            completed = {
                hours: _completed_jobs_since(conn, now - hours * 3600)
                for hours in (1, 6, 24)
            }
            duration_samples = _completed_job_duration_samples(conn, limit=20)
    except sqlite3.Error as exc:
        return _ttl_cache_set(_ETA_SUMMARY_CACHE, cache_key, {
            "database": str(db_path),
            "exists": True,
            "available": False,
            "error_code": "scanner_database_unavailable",
            "remaining": None,
            "rate_per_hour": None,
            "eta_hours": None,
        })

    active_queue = sum(queue_counts.get(key, 0) for key in ("queued", "running", "failed_retry"))
    remaining = max(needs_ai, active_queue)
    rates = {hours: completed[hours] / hours for hours in completed}
    source_window = next((hours for hours in (6, 24, 1) if rates[hours] > 0), None)
    observed_rate = rates[source_window] if source_window is not None else 0.0
    completed_sample_count = completed[source_window] if source_window is not None else 0
    median_duration_seconds = statistics.median(duration_samples) if duration_samples else None
    historical_rate = 3600.0 / median_duration_seconds if median_duration_seconds else 0.0
    if completed_sample_count >= 3:
        rate = observed_rate
        eta_method = "recent_throughput"
    elif historical_rate > 0:
        rate = historical_rate
        eta_method = "historical_median"
    else:
        rate = 0.0
        eta_method = "insufficient_samples"
    eta_hours = remaining / rate if rate > 0 else None
    return _ttl_cache_set(_ETA_SUMMARY_CACHE, cache_key, {
        "database": str(db_path),
        "exists": True,
        "available": True,
        "error_code": None,
        "remaining": remaining,
        "needs_ai": needs_ai,
        "active_queue": active_queue,
        "queue_counts": queue_counts,
        "completed_last_1h": completed[1],
        "completed_last_6h": completed[6],
        "completed_last_24h": completed[24],
        "rate_per_hour": round(rate, 3),
        "observed_rate_per_hour": round(observed_rate, 3),
        "source_window_hours": source_window,
        "eta_method": eta_method,
        "duration_sample_count": len(duration_samples),
        "median_duration_seconds": round(median_duration_seconds, 1) if median_duration_seconds else None,
        "eta_hours": round(eta_hours, 2) if eta_hours is not None else None,
        "eta_days": round(eta_hours / 24, 2) if eta_hours is not None else None,
    })


def _completed_job_duration_samples(conn: sqlite3.Connection, *, limit: int) -> list[float]:
    if not _sqlite_table_exists(conn, "ai_job_state"):
        return []
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ai_job_state)").fetchall()}
    if not {"status", "started_at", "finished_at"}.issubset(columns):
        return []
    rows = conn.execute(
        """
        SELECT finished_at - started_at
        FROM ai_job_state
        WHERE status IN ('ok', 'done', 'success', 'finished')
          AND COALESCE(started_at, 0) > 0
          AND COALESCE(finished_at, 0) > started_at
          AND finished_at - started_at BETWEEN 1 AND 28800
        ORDER BY finished_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [float(row[0]) for row in rows if row and float(row[0] or 0) > 0]


def _normalize_failure_target(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def _open_reviews_for_failure_summary(
    config: dict[str, Any],
) -> tuple[bool, bool, int, list[dict[str, Any]]]:
    """Read the review inbox without ever making the WebUI a SQLite writer."""

    database = _control_state_db_path(config)
    if not database.exists():
        return False, False, 0, []
    try:
        with _sqlite_connect(database, readonly=True) as conn:
            if not _sqlite_table_exists(conn, "review_items"):
                return False, False, 0, []
    except sqlite3.Error:
        return False, False, 0, []

    page_size = 200
    max_items = 5000
    offset = 0
    total = 0
    items: list[dict[str, Any]] = []
    while offset < max_items:
        page, page_total = list_reviews(
            database,
            status="open",
            kind="",
            limit=page_size,
            offset=offset,
        )
        total = int(page_total or total)
        if not page:
            break
        items.extend(item for item in page if isinstance(item, dict))
        offset += len(page)
        if offset >= total or len(page) < page_size:
            break
    return True, len(items) < total, total, items


def _ai_failure_review_overlap(
    current_paths: set[str],
    *,
    config: dict[str, Any],
    extract_jobs: dict[str, Any] | None,
) -> dict[str, Any]:
    available, reviews_truncated, open_total, reviews = _open_reviews_for_failure_summary(config)
    terminal_summary = extract_jobs if isinstance(extract_jobs, dict) else {}
    terminal_counts = (
        terminal_summary.get("counts")
        if isinstance(terminal_summary.get("counts"), dict)
        else {}
    )
    terminal_total = int(terminal_counts.get("terminal_failed") or 0)
    terminal_rows = [
        row
        for row in terminal_summary.get("recent_attention") or []
        if isinstance(row, dict) and str(row.get("status") or "") == "terminal_failed"
    ]
    terminal_rows_by_key = {
        _normalize_failure_target(row.get("job_key")): row
        for row in terminal_rows
        if _normalize_failure_target(row.get("job_key"))
    }
    terminal_rows_by_hash = {
        _normalize_failure_target(row.get("torrent_hash")): row
        for row in terminal_rows
        if _normalize_failure_target(row.get("torrent_hash"))
    }
    open_review_ids = {
        str(item.get("review_id") or "")
        for item in reviews
        if str(item.get("review_id") or "")
    }

    ai_review_ids: set[str] = set()
    ai_paths: set[str] = set()
    terminal_review_ids: set[str] = set()
    terminal_job_keys: set[str] = set()

    for row in terminal_rows:
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        direct_review_id = str(result.get("review_id") or "")
        if direct_review_id and direct_review_id in open_review_ids:
            terminal_review_ids.add(direct_review_id)
            terminal_job_keys.add(str(row.get("job_key") or ""))

    for review in reviews:
        review_id = str(review.get("review_id") or "")
        diagnosis = review.get("diagnosis") if isinstance(review.get("diagnosis"), dict) else {}
        video = _normalize_failure_target(
            diagnosis.get("video")
            or (
                diagnosis.get("media_file", {}).get("path")
                if isinstance(diagnosis.get("media_file"), dict)
                else ""
            )
            or review.get("target_key")
        )
        if video and video in current_paths:
            ai_review_ids.add(review_id)
            ai_paths.add(video)

        job_key = _normalize_failure_target(diagnosis.get("job_key") or review.get("target_key"))
        torrent_hash = _normalize_failure_target(diagnosis.get("torrent_hash"))
        terminal_row = terminal_rows_by_key.get(job_key) or terminal_rows_by_hash.get(torrent_hash)
        if terminal_row is not None:
            terminal_review_ids.add(review_id)
            terminal_job_keys.add(str(terminal_row.get("job_key") or ""))

    terminal_scan_complete = len(terminal_rows) >= terminal_total
    overlap_ids = ai_review_ids | terminal_review_ids
    raw_attention_total = open_total + len(current_paths) + terminal_total
    deduplicated_attention_total = (
        raw_attention_total - len(ai_paths) - len(terminal_job_keys)
        if available and not reviews_truncated and terminal_scan_complete
        else None
    )
    return {
        "available": available,
        "open_total": open_total,
        "reviews_truncated": reviews_truncated,
        "ai_failed_retry": {
            "video_count": len(ai_paths),
            "review_count": len(ai_review_ids),
            "review_ids": sorted(ai_review_ids),
        },
        "terminal_extract": {
            "total": terminal_total,
            "scanned_job_count": len(terminal_rows),
            "scan_complete": terminal_scan_complete,
            "job_count": len(terminal_job_keys),
            "review_count": len(terminal_review_ids),
            "review_ids": sorted(terminal_review_ids),
        },
        "unique_review_count": len(overlap_ids),
        "review_ids": sorted(overlap_ids),
        "raw_attention_total": raw_attention_total,
        "deduplicated_attention_total": deduplicated_attention_total,
    }


def _ai_failure_root_summary(
    config: dict[str, Any] | None = None,
    *,
    extract_jobs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db_path = WORK_PATH / "scanner_state.sqlite3"
    config = config or {}
    if not db_path.exists():
        return {
            "exists": False,
            "current_total": 0,
            "affected_videos_7d": 0,
            "outcomes_7d": {
                "queued": 0,
                "failed_retry": 0,
                "done": 0,
                "missing": 0,
                "other": 0,
            },
            "outcome_counts_7d": {
                "queued": 0,
                "failed_retry": 0,
                "done": 0,
                "missing": 0,
                "other": 0,
            },
            "buckets": [],
            "bucket_mode": "latest_failure_per_video",
            "buckets_are_additive": True,
        }
    cutoff = time.time() - 7 * 86400
    queue_rows: list[tuple[str, str, str]] = []
    recent: list[tuple[str, str, str]] = []
    try:
        with _sqlite_connect(db_path, readonly=True) as conn:
            if _sqlite_table_exists(conn, "ai_candidate_queue"):
                queue_rows = [
                    (str(path), str(status or ""), str(message or ""))
                    for path, status, message in conn.execute(
                        "SELECT path, status, last_error FROM ai_candidate_queue"
                    ).fetchall()
                ]
            if _sqlite_table_exists(conn, "ai_stage_events"):
                recent = [
                    (str(path), str(stage or ""), str(message or ""))
                    for path, stage, message in conn.execute(
                        """
                        SELECT path, stage, message
                        FROM ai_stage_events
                        WHERE status = 'failed' AND created_at >= ?
                        ORDER BY created_at DESC, rowid DESC
                        """,
                        (cutoff,),
                    ).fetchall()
                ]
    except sqlite3.Error as exc:
        return {
            "exists": True,
            "error": str(exc),
            "current_total": 0,
            "affected_videos_7d": 0,
            "outcomes_7d": {
                "queued": 0,
                "failed_retry": 0,
                "done": 0,
                "missing": 0,
                "other": 0,
            },
            "outcome_counts_7d": {
                "queued": 0,
                "failed_retry": 0,
                "done": 0,
                "missing": 0,
                "other": 0,
            },
            "buckets": [],
            "bucket_mode": "latest_failure_per_video",
            "buckets_are_additive": True,
        }

    latest_event_by_path: dict[str, tuple[str, str]] = {}
    for path, stage, message in recent:
        latest_event_by_path.setdefault(path, (stage, message))

    current: list[tuple[str, str, str]] = []
    for path, status, last_error in queue_rows:
        if status != "failed_retry":
            continue
        event_stage, event_message = latest_event_by_path.get(path, ("", ""))
        blocking_message = "\n".join(
            value for value in (last_error, event_message) if str(value or "").strip()
        )
        current.append((path, event_stage, blocking_message))
    current_counts: dict[str, int] = {}
    for _path, stage, message in current:
        bucket = _ai_failure_bucket(stage, message)
        current_counts[bucket] = current_counts.get(bucket, 0) + 1

    # One historical bucket per affected video, chosen from its latest failure.
    # This makes the bucket column additive instead of counting one video in
    # several root-cause rows.
    affected: dict[str, set[str]] = {}
    latest_bucket_by_path: dict[str, str] = {}
    for path, (stage, message) in latest_event_by_path.items():
        bucket = _ai_failure_bucket(stage, message)
        latest_bucket_by_path[path] = bucket
        affected.setdefault(bucket, set()).add(path)

    queue_status_by_path = {
        path: status
        for path, status, _message in queue_rows
    }
    outcome_counts = {
        "queued": 0,
        "failed_retry": 0,
        "done": 0,
        "missing": 0,
        "other": 0,
    }
    other_statuses: dict[str, int] = {}
    for path in latest_bucket_by_path:
        status = queue_status_by_path.get(path)
        if status in {"queued", "failed_retry", "done"}:
            outcome_counts[status] += 1
        elif status is None:
            outcome_counts["missing"] += 1
        else:
            outcome_counts["other"] += 1
            other_statuses[status] = other_statuses.get(status, 0) + 1

    current_paths = {
        _normalize_failure_target(path)
        for path, _stage, _message in current
        if _normalize_failure_target(path)
    }
    review_overlap = _ai_failure_review_overlap(
        current_paths,
        config=config,
        extract_jobs=extract_jobs,
    )
    names = sorted(set(current_counts) | set(affected), key=lambda name: (-current_counts.get(name, 0), -len(affected.get(name, set())), name))
    return {
        "exists": True,
        "window_days": 7,
        "current_total": len(current),
        "current": {
            "status": "failed_retry",
            "total": len(current),
        },
        "affected_videos_7d": len(latest_bucket_by_path),
        "outcomes_7d": outcome_counts,
        "outcome_counts_7d": outcome_counts,
        "historical_7d": {
            "affected_videos": len(latest_bucket_by_path),
            "current_outcomes": outcome_counts,
            "other_statuses": other_statuses,
        },
        "bucket_mode": "latest_failure_per_video",
        "buckets_are_additive": True,
        "buckets": [
            {
                "key": name,
                "label": _AI_FAILURE_BUCKET_LABELS.get(name, name),
                "current": current_counts.get(name, 0),
                "affected_videos_7d": len(affected.get(name, set())),
            }
            for name in names
        ],
        "review_overlap": review_overlap,
    }


_AI_FAILURE_BUCKET_LABELS = {
    "asr_review": "ASR 需要人工確認",
    "asr": "Whisper／轉錄",
    "prompt_leak": "Prompt 污染",
    "residual_japanese": "翻譯殘留日文",
    "translation_timeout": "翻譯逾時",
    "translation": "翻譯格式／內容",
    "subtitle_timing": "字幕時間軸／可讀性",
    "database": "資料庫忙碌",
    "audio": "音軌／音訊",
    "worker": "Worker 系統錯誤",
}


def _ai_failure_bucket(stage: str, message: str) -> str:
    stage_text = str(stage or "").casefold()
    text = str(message or "").casefold()
    if "asr review" in text or "source transcription is unreliable" in text or "transcription_review" in stage_text:
        return "asr_review"
    if "prompt_leak" in text or "prompt leak" in text or "model-output pollution" in text:
        return "prompt_leak"
    if "residual" in text and ("kana" in text or "japanese" in text):
        return "residual_japanese"
    if "database is locked" in text or "database is busy" in text:
        return "database"
    if "timeout" in text or "timed out" in text:
        return "translation_timeout" if "translat" in stage_text or "translation" in text else "worker"
    if "translation_safe_omission" in text or "translation safe omission" in text:
        return "translation"
    if any(marker in text for marker in ("leading_gap", "large_gap", "long_line", "long_duration", "very_long_line")):
        return "subtitle_timing"
    if "transcri" in stage_text or "whisper" in text or "asr" in text:
        return "asr"
    if "translat" in stage_text or "translation" in text:
        return "translation"
    if "audio" in stage_text or "audio" in text:
        return "audio"
    return "worker"


def _database_health_summary(config: dict[str, Any]) -> dict[str, Any]:
    cache_key = str(WORK_PATH)
    cached = _ttl_cache_get(_DATABASE_HEALTH_CACHE, cache_key, DATABASE_HEALTH_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    paths = [
        ("AI 佇列", WORK_PATH / "scanner_state.sqlite3"),
        ("Mikan", WORK_PATH / "mikan_state.sqlite3"),
        ("作品資訊", _series_metadata_db_path(config)),
    ]
    databases: list[dict[str, Any]] = []
    for label, path in paths:
        if not path.exists():
            databases.append({"label": label, "path": str(path), "exists": False})
            continue
        try:
            with _sqlite_connect(path, readonly=True) as conn:
                page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
                page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
                freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            databases.append({
                "label": label,
                "path": str(path),
                "exists": True,
                "size_mib": round(path.stat().st_size / 1048576.0, 2),
                "reclaim_mib": round(freelist * page_size / 1048576.0, 2),
                "freelist_ratio": round(freelist / page_count, 4) if page_count else 0.0,
            })
        except (OSError, sqlite3.Error) as exc:
            databases.append({"label": label, "path": str(path), "exists": True, "error": str(exc)})
    return _ttl_cache_set(_DATABASE_HEALTH_CACHE, cache_key, {"databases": databases})


def _completed_jobs_since(conn: sqlite3.Connection, cutoff: float) -> int:
    if _sqlite_table_exists(conn, "ai_job_state"):
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ai_job_state)").fetchall()}
        if {"path", "status", "started_at", "finished_at"}.issubset(columns):
            stage_filter = "OR stage = 'complete'" if "stage" in columns else ""
            return int(
                conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT path)
                    FROM ai_job_state
                    WHERE COALESCE(started_at, 0) > 0
                      AND COALESCE(finished_at, 0) >= ?
                      AND (
                        status IN ('ok', 'done', 'success', 'finished')
                        {stage_filter}
                      )
                    """,
                    (cutoff,),
                ).fetchone()[0]
            )
    return int(
        conn.execute(
            """
            SELECT COUNT(DISTINCT path)
            FROM ai_stage_events
            WHERE stage = 'complete'
              AND status = 'ok'
              AND created_at >= ?
            """,
            (cutoff,),
        ).fetchone()[0]
    )


def _file_sha256_prefix(path: Path, *, length: int = 12) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:length]
    except OSError:
        return None


def _version_source_paths() -> list[Path]:
    candidates = [
        APP_DIR / "app.py",
        APP_DIR / "package.json",
        APP_DIR / "src" / "App.vue",
        APP_DIR / "src" / "dashboard.js",
        APP_DIR / "src" / "components" / "MikanDownloads.vue",
        APP_DIR / "Dockerfile",
    ]
    paths = [path for path in candidates if path.exists() and path.is_file()]
    static_root = _frontend_static_dir()
    if static_root.exists():
        for path in sorted(static_root.rglob("*"), key=lambda item: str(item).casefold()):
            if not path.is_file():
                continue
            if path.suffix.casefold() not in {".html", ".js", ".css", ".json", ".svg"}:
                continue
            paths.append(path)
            if len(paths) >= 96:
                break
    return paths


def _source_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item).casefold()):
        try:
            rel = path.relative_to(APP_DIR)
        except ValueError:
            rel = path
        digest.update(str(rel).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def _version_summary() -> dict[str, Any]:
    paths = _version_source_paths()
    key_parts: list[str] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        key_parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    cache_key = "|".join(key_parts) or str(APP_DIR)
    cached = _ttl_cache_get(_VERSION_SUMMARY_CACHE, cache_key, SUMMARY_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    config_hash = _file_sha256_prefix(CONFIG_PATH)
    files: dict[str, dict[str, Any]] = {}
    for path in paths:
        try:
            rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
        except ValueError:
            rel = str(path)
        try:
            stat = path.stat()
        except OSError:
            continue
        files[rel] = {
            "sha256": _file_sha256_prefix(path),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }
    return _ttl_cache_set(_VERSION_SUMMARY_CACHE, cache_key, {
        "webui_fingerprint": _source_fingerprint(paths),
        "config_sha256": config_hash,
        "files": files,
    })


def _health_summary(config: dict[str, Any], *, fast: bool = False) -> dict[str, Any]:
    cache_key = f"{DOCKER_SOCKET}:{WORKER_CONTAINER_NAME}:{CONFIG_PATH}:{WORK_PATH}:{LOG_PATH}:{'fast' if fast else 'full'}"
    cached = _ttl_cache_get(_HEALTH_SUMMARY_CACHE, cache_key, FAST_WORKER_SUMMARY_CACHE_TTL_SECONDS if fast else SUMMARY_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        checks.append({
            "name": name,
            "ok": bool(ok),
            "detail": detail,
            "severity": severity if severity in {"error", "warn"} else "error",
        })

    worker = _worker_summary(fast=fast)
    add_check("docker_socket", DOCKER_SOCKET.exists(), str(DOCKER_SOCKET))
    add_check("worker_available", bool(worker.get("available")), str(worker.get("error") or worker.get("name") or ""), "error")
    worker_steady = bool(worker.get("running")) and not bool(worker.get("restarting"))
    worker_detail = str(worker.get("status") or "")
    restart_count = int(worker.get("restart_count") or 0)
    if worker.get("restarting"):
        worker_detail = f"restarting; restart_count={restart_count}"
    elif worker.get("exit_code") not in (None, 0):
        worker_detail += f"; exit_code={worker.get('exit_code')}"
    if worker.get("state_error"):
        worker_detail += f"; error={worker.get('state_error')}"
    add_check("worker_running", worker_steady, worker_detail, "error")
    add_check("config_file", CONFIG_PATH.exists(), str(CONFIG_PATH), "error")
    add_check("work_path", WORK_PATH.exists(), str(WORK_PATH), "error")
    add_check("log_path", LOG_PATH.exists(), str(LOG_PATH), "warn")
    try:
        usage = shutil.disk_usage(WORK_PATH)
        min_free_gb = float(config.get("disk_min_free_gb") or 0)
        free_gb = usage.free / 1024 / 1024 / 1024
        add_check("work_free_space", free_gb >= min_free_gb, f"{free_gb:.1f} GB free / min {min_free_gb:.1f} GB", "error")
    except (OSError, TypeError, ValueError):
        add_check("work_free_space", False, "cannot read work path disk usage", "warn")

    scanner_db = WORK_PATH / "scanner_state.sqlite3"
    scanner_db_ok = True
    scanner_db_detail = f"{scanner_db} (not initialized yet)"
    if scanner_db.exists():
        scanner_db_detail = str(scanner_db)
        try:
            with _sqlite_connect(scanner_db, readonly=True) as conn:
                conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except (OSError, sqlite3.Error) as exc:
            scanner_db_ok = False
            scanner_db_detail = f"{scanner_db}: {exc}"
    add_check("scanner_state_db", scanner_db_ok, scanner_db_detail, "error")

    ai_scheduler = _ai_scheduler_summary(config)
    if ai_scheduler.get("exists"):
        scheduler_detail = (
            f"state={ai_scheduler.get('state')} "
            f"reason={ai_scheduler.get('reason_code') or '-'} "
            f"heartbeat_age={float(ai_scheduler.get('heartbeat_age_seconds') or 0):.1f}s"
        )
        if ai_scheduler.get("error"):
            scheduler_detail += f" error={ai_scheduler.get('error')}"
        if ai_scheduler.get("problem"):
            persistent = bool(
                ai_scheduler.get("stale")
                or int(ai_scheduler.get("consecutive_errors") or 0) >= 2
                or float(ai_scheduler.get("state_age_seconds") or 0) >= 60
            )
            add_check(
                "ai_scheduler",
                False,
                scheduler_detail,
                "error" if persistent else "warn",
            )
        else:
            add_check("ai_scheduler", True, scheduler_detail, "error")

    mikan_db = _mikan_state_db_path(config)
    mikan_db_ok = mikan_db.exists()
    if mikan_db_ok and not fast:
        conn: sqlite3.Connection | None = None
        try:
            conn = _sqlite_connect(mikan_db, readonly=True)
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            mikan_db_ok = False
            add_check("mikan_state_db", False, f"{mikan_db}: {exc}", "error")
        finally:
            if conn is not None:
                conn.close()
    if mikan_db_ok or fast:
        add_check("mikan_state_db", mikan_db.exists(), str(mikan_db), "warn")
    elif not mikan_db.exists():
        add_check("mikan_state_db", False, str(mikan_db), "warn")

    series_db = _series_metadata_db_path(config)
    series_db_ok = True
    series_detail = f"{series_db} (not initialized yet)"
    if series_db.exists():
        series_detail = str(series_db)
        if not fast:
            conn = None
            try:
                conn = _sqlite_connect(series_db, readonly=True)
                result = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
                series_db_ok = result == ["ok"]
                if not series_db_ok:
                    series_detail = f"{series_db}: {result}"
            except sqlite3.Error as exc:
                series_db_ok = False
                series_detail = f"{series_db}: {exc}"
            finally:
                if conn is not None:
                    conn.close()
    add_check("series_metadata_db", series_db_ok, series_detail, "warn")

    backup_path = _expand_config_env(
        str(config.get("state_backup_path") or "state_backups")
    ).strip() or "state_backups"
    backup_root = Path(backup_path)
    if not backup_root.is_absolute():
        backup_root = WORK_PATH / backup_root
    latest_manifest = None
    stale_partial: list[Path] = []
    if backup_root.is_dir():
        manifests = sorted(backup_root.glob("*/manifest.json"), key=_path_mtime, reverse=True)
        latest_manifest = manifests[0] if manifests else None
        now = time.time()
        stale_partial = [
            item for item in backup_root.glob(".*.partial")
            if item.is_dir() and now - _path_mtime(item) > 3600
        ]
    backup_ok = not stale_partial
    backup_detail = str(latest_manifest) if latest_manifest else f"{backup_root} (no backup created yet)"
    if stale_partial:
        backup_detail = f"stale partial backups: {', '.join(str(item) for item in stale_partial[:3])}"
    add_check("state_backups", backup_ok, backup_detail, "warn")

    failed_errors = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    failed_warnings = [check for check in checks if not check["ok"] and check["severity"] == "warn"]
    overall = "error" if failed_errors else ("warn" if failed_warnings else "ok")
    return _ttl_cache_set(_HEALTH_SUMMARY_CACHE, cache_key, {
        "overall": overall,
        "checks": checks,
        "failed_errors": len(failed_errors),
        "failed_warnings": len(failed_warnings),
    })


def _worker_summary(*, fast: bool = False) -> dict[str, Any]:
    cache_key = f"{DOCKER_SOCKET}:{WORKER_CONTAINER_NAME}:{'fast' if fast else 'full'}"
    cache_ttl = FAST_WORKER_SUMMARY_CACHE_TTL_SECONDS if fast else SUMMARY_CACHE_TTL_SECONDS
    cached = _ttl_cache_get(_WORKER_SUMMARY_CACHE, cache_key, cache_ttl)
    if cached is not None:
        return cached
    if not DOCKER_SOCKET.exists():
        return _ttl_cache_set(
            _WORKER_SUMMARY_CACHE,
            cache_key,
            {"available": False, "error": f"Docker socket not mounted: {DOCKER_SOCKET}"},
        )
    try:
        data = _docker_request(
            "GET",
            f"/containers/{WORKER_CONTAINER_NAME}/json",
            timeout_seconds=1.0 if fast else None,
        )
    except HTTPException as exc:
        return _ttl_cache_set(_WORKER_SUMMARY_CACHE, cache_key, {"available": False, "error": str(exc.detail)})
    except OSError as exc:
        return _ttl_cache_set(_WORKER_SUMMARY_CACHE, cache_key, {"available": False, "error": str(exc)})

    state = data.get("State") if isinstance(data, dict) else {}
    config = data.get("Config") if isinstance(data, dict) else {}
    if not isinstance(state, dict):
        state = {}
    if not isinstance(config, dict):
        config = {}
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        labels = {}
    return _ttl_cache_set(_WORKER_SUMMARY_CACHE, cache_key, {
        "available": True,
        "name": str(data.get("Name", "")).lstrip("/") if isinstance(data, dict) else WORKER_CONTAINER_NAME,
        "image": config.get("Image"),
        "image_id": str(data.get("Image") or "")[:24] if isinstance(data, dict) else "",
        "created": data.get("Created") if isinstance(data, dict) else None,
        "labels": labels,
        "status": state.get("Status"),
        "running": bool(state.get("Running")),
        "restarting": bool(state.get("Restarting")),
        "dead": bool(state.get("Dead")),
        "paused": bool(state.get("Paused")),
        "oom_killed": bool(state.get("OOMKilled")),
        "exit_code": state.get("ExitCode"),
        "state_error": str(state.get("Error") or ""),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "restart_count": data.get("RestartCount") if isinstance(data, dict) else None,
        "health_status": (
            (state.get("Health") or {}).get("Status")
            if isinstance(state.get("Health"), dict)
            else None
        ),
    })


def _worker_runtime_log(tail: int = 120) -> dict[str, Any]:
    bounded_tail = max(20, min(int(tail), 500))
    try:
        payload = _docker_request(
            "GET",
            (
                f"/containers/{WORKER_CONTAINER_NAME}/logs"
                f"?stdout=1&stderr=1&timestamps=1&tail={bounded_tail}"
            ),
            return_bytes=True,
            timeout_seconds=5.0,
        )
        text = _decode_docker_log_stream(payload)
    except (HTTPException, OSError, TimeoutError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return {
            "ok": False,
            "container": WORKER_CONTAINER_NAME,
            "lines": [],
            "error": str(detail),
        }
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return {
        "ok": True,
        "container": WORKER_CONTAINER_NAME,
        "lines": lines[-bounded_tail:],
        "line_count": min(len(lines), bounded_tail),
        "truncated": len(lines) > bounded_tail,
    }


def _decode_docker_log_stream(payload: bytes | bytearray | str) -> str:
    if isinstance(payload, str):
        return payload
    raw = bytes(payload)
    offset = 0
    frames: list[bytes] = []
    while offset + 8 <= len(raw):
        header = raw[offset : offset + 8]
        if header[0] not in {0, 1, 2} or header[1:4] != b"\x00\x00\x00":
            break
        size = int.from_bytes(header[4:8], byteorder="big", signed=False)
        frame_end = offset + 8 + size
        if frame_end > len(raw):
            break
        frames.append(raw[offset + 8 : frame_end])
        offset = frame_end
    if frames and offset == len(raw):
        raw = b"".join(frames)
    return raw.decode("utf-8", errors="replace")


def _disk_summary() -> dict[str, Any]:
    cache_key = f"{WORK_PATH}:{LOG_PATH}:{CONFIG_PATH.parent}"
    cached = _ttl_cache_get(
        _DISK_SUMMARY_CACHE,
        cache_key,
        SUMMARY_CACHE_TTL_SECONDS,
    )
    if cached is not None:
        return cached
    result: dict[str, Any] = {}
    sampled_at = int(time.time())
    for label, path in {"work": WORK_PATH, "logs": LOG_PATH, "config": CONFIG_PATH.parent}.items():
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            result[label] = {
                "available": False,
                "error_code": "disk_probe_failed",
                "path": str(path),
                "sampled_at": sampled_at,
                "max_age_seconds": RESOURCE_OVERVIEW_MAX_AGE_SECONDS,
            }
            continue
        used = max(0, usage.total - usage.free)
        result[label] = {
            "available": True,
            "error_code": None,
            "path": str(path),
            "free_gb": round(usage.free / 1024 / 1024 / 1024, 2),
            "used_gb": round(used / 1024 / 1024 / 1024, 2),
            "total_gb": round(usage.total / 1024 / 1024 / 1024, 2),
            "utilization_percent": (
                round(100.0 * used / usage.total, 1) if usage.total > 0 else None
            ),
            "sampled_at": sampled_at,
            "max_age_seconds": RESOURCE_OVERVIEW_MAX_AGE_SECONDS,
        }
    return _ttl_cache_set(_DISK_SUMMARY_CACHE, cache_key, result)


def _resource_admission_unavailable(
    error_code: str,
    *,
    sampled_at: float | None = None,
    max_age_seconds: float | None = None,
    sample_age_seconds: float | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "error_code": error_code,
        "schema": RESOURCE_ADMISSION_STATE_SCHEMA,
        "sampled_at": sampled_at,
        "max_age_seconds": max_age_seconds,
        "sample_age_seconds": sample_age_seconds,
        "stale": error_code == "state_stale",
    }


def _resource_admission_state_path(config: dict[str, Any]) -> Path:
    work_root = _configured_work_path(config).resolve(strict=False)
    raw = _expand_config_env(
        str(config.get("resource_admission_state_path") or RESOURCE_ADMISSION_STATE_NAME)
    ).strip()
    if not raw:
        raise ValueError("empty resource admission state path")
    configured = Path(raw)
    candidate = configured if configured.is_absolute() else work_root / configured
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(work_root)
    except ValueError as exc:
        raise ValueError("resource admission state path must stay under work path") from exc
    return resolved


def _resource_admission_summary(
    config: dict[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Read and sanitize the Worker's atomic resource decision snapshot.

    The WebUI never derives an admission decision from its own probes.  Any
    missing, stale, oversized, malformed, or unsupported state fails closed to
    ``available=false`` and no stale decision fields are returned.
    """

    try:
        path = _resource_admission_state_path(config)
    except (OSError, ValueError):
        return _resource_admission_unavailable("state_path_invalid")

    try:
        with path.open("rb") as handle:
            raw = handle.read(RESOURCE_ADMISSION_STATE_MAX_BYTES + 1)
    except FileNotFoundError:
        return _resource_admission_unavailable("state_missing")
    except OSError:
        return _resource_admission_unavailable("state_unreadable")
    if len(raw) > RESOURCE_ADMISSION_STATE_MAX_BYTES:
        return _resource_admission_unavailable("state_too_large")
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError):
        return _resource_admission_unavailable("state_invalid_json")
    if not isinstance(payload, dict):
        return _resource_admission_unavailable("state_invalid_root")
    expected_root_keys = {
        "schema_version",
        "contract",
        "updated_at",
        "sampled_at",
        "max_age_seconds",
        "last_oom_at",
        "last_oom",
        "hysteresis_state",
        "telemetry",
        "decision",
        "launch_plan",
    }
    if (
        set(payload) != expected_root_keys
        or payload.get("schema_version") != 1
        or payload.get("contract") != RESOURCE_ADMISSION_STATE_SCHEMA
    ):
        return _resource_admission_unavailable("state_schema_mismatch")

    def finite_number(
        value: Any,
        *,
        minimum: float = 0.0,
        maximum: float | None = None,
    ) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected numeric field")
        number = float(value)
        if not math.isfinite(number) or number < minimum:
            raise ValueError("numeric field outside range")
        if maximum is not None and number > maximum:
            raise ValueError("numeric field outside range")
        return number

    def optional_text(value: Any, *, maximum: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("expected text field")
        normalized = value.strip()
        if not normalized or len(normalized) > maximum:
            raise ValueError("invalid text field")
        return normalized

    def optional_positive_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("expected positive integer field")
        return value

    try:
        sampled_at = finite_number(payload.get("sampled_at"), minimum=1.0)
        max_age_seconds = finite_number(
            payload.get("max_age_seconds"),
            minimum=0.25,
            maximum=RESOURCE_ADMISSION_MAX_AGE_LIMIT_SECONDS,
        )
    except ValueError:
        return _resource_admission_unavailable("state_invalid_freshness")
    current_time = time.time() if now is None else float(now)
    if not math.isfinite(current_time) or sampled_at > current_time + 5.0:
        return _resource_admission_unavailable(
            "state_invalid_freshness",
            sampled_at=sampled_at,
            max_age_seconds=max_age_seconds,
        )
    sample_age_seconds = max(0.0, current_time - sampled_at)
    if sample_age_seconds > max_age_seconds:
        return _resource_admission_unavailable(
            "state_stale",
            sampled_at=sampled_at,
            max_age_seconds=max_age_seconds,
            sample_age_seconds=round(sample_age_seconds, 3),
        )

    try:
        hysteresis = payload.get("hysteresis_state")
        if not isinstance(hysteresis, dict) or set(hysteresis) != {
            "effective_tier", "candidate_tier", "consecutive_samples",
        }:
            raise ValueError("invalid hysteresis state")
        valid_tiers = {"green", "yellow", "red", "unavailable"}
        if hysteresis.get("effective_tier") not in valid_tiers:
            raise ValueError("invalid hysteresis tier")
        if hysteresis.get("candidate_tier") not in {*valid_tiers, None}:
            raise ValueError("invalid hysteresis candidate")
        consecutive_samples = hysteresis.get("consecutive_samples")
        if (
            isinstance(consecutive_samples, bool)
            or not isinstance(consecutive_samples, int)
            or consecutive_samples < 0
        ):
            raise ValueError("invalid hysteresis count")

        last_oom_at = payload.get("last_oom_at")
        if last_oom_at is not None:
            finite_number(last_oom_at)
        last_oom = payload.get("last_oom")
        if last_oom is not None:
            if not isinstance(last_oom, dict) or set(last_oom) != {"video", "detail"}:
                raise ValueError("invalid OOM state")
            optional_text(last_oom.get("video"), maximum=4096)
            optional_text(last_oom.get("detail"), maximum=4096)

        telemetry_state = payload.get("telemetry")
        if telemetry_state is not None:
            if not isinstance(telemetry_state, dict) or set(telemetry_state) != {
                "sampled_at_epoch_seconds", "available", "cpu_percent", "ram_available_mib",
                "ram_total_mib", "gpu_util_percent", "vram_free_mib",
                "vram_total_mib", "error_codes", "age_seconds",
            }:
                raise ValueError("invalid telemetry state")
            finite_number(telemetry_state.get("sampled_at_epoch_seconds"), minimum=1.0)
            finite_number(telemetry_state.get("age_seconds"))
            if not isinstance(telemetry_state.get("available"), bool):
                raise ValueError("invalid telemetry availability")
            telemetry_errors = telemetry_state.get("error_codes")
            if not isinstance(telemetry_errors, list) or len(telemetry_errors) > 32:
                raise ValueError("invalid telemetry errors")
            for error_code in telemetry_errors:
                if not isinstance(error_code, str) or not re.fullmatch(
                    r"[a-z0-9_.:-]{1,80}", error_code
                ):
                    raise ValueError("invalid telemetry error")
            for key in (
                "cpu_percent", "ram_available_mib", "ram_total_mib",
                "gpu_util_percent", "vram_free_mib", "vram_total_mib",
            ):
                value = telemetry_state.get(key)
                if value is None and telemetry_state["available"] is False:
                    continue
                finite_number(value)
    except ValueError:
        return _resource_admission_unavailable(
            "state_invalid_payload",
            sampled_at=sampled_at,
            max_age_seconds=max_age_seconds,
            sample_age_seconds=round(sample_age_seconds, 3),
        )

    decision = payload.get("decision")
    if decision is None:
        return _resource_admission_unavailable(
            "state_decision_missing",
            sampled_at=sampled_at,
            max_age_seconds=max_age_seconds,
            sample_age_seconds=round(sample_age_seconds, 3),
        )
    if not isinstance(decision, dict):
        return _resource_admission_unavailable(
            "state_invalid_decision",
            sampled_at=sampled_at,
            max_age_seconds=max_age_seconds,
            sample_age_seconds=round(sample_age_seconds, 3),
        )
    latest_plan = payload.get("launch_plan")
    if not isinstance(latest_plan, dict):
        return _resource_admission_unavailable(
            "state_invalid_decision",
            sampled_at=sampled_at,
            max_age_seconds=max_age_seconds,
            sample_age_seconds=round(sample_age_seconds, 3),
        )

    try:
        if set(decision) != {
            "tier",
            "allow_new_job",
            "allow_running_job",
            "asr_compute_type",
            "asr_model",
            "concurrency_limit",
            "retry_after",
            "reason_codes",
            "hysteresis_state",
            "diagnostics",
        }:
            raise ValueError("invalid admission decision shape")
        tier = optional_text(decision.get("tier"), maximum=24)
        if tier not in {"green", "yellow", "red", "unavailable"}:
            raise ValueError("invalid admission tier")
        allow_new_job = decision.get("allow_new_job")
        allow_running_job = decision.get("allow_running_job")
        if not isinstance(allow_new_job, bool) or not isinstance(allow_running_job, bool):
            raise ValueError("invalid admission boolean")
        if decision.get("hysteresis_state") != payload.get("hysteresis_state"):
            raise ValueError("decision hysteresis mismatch")
        if not isinstance(decision.get("diagnostics"), dict):
            raise ValueError("invalid decision diagnostics")
        for key in ("asr_model", "asr_compute_type"):
            value = decision.get(key)
            if value is not None:
                optional_text(value, maximum=240 if key == "asr_model" else 80)
        reason_codes = decision.get("reason_codes")
        if not isinstance(reason_codes, list) or len(reason_codes) > 32:
            raise ValueError("invalid reason codes")
        normalized_reasons: list[str] = []
        for reason in reason_codes:
            if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_.:-]{1,80}", reason):
                raise ValueError("invalid reason code")
            if reason not in normalized_reasons:
                normalized_reasons.append(reason)
        plan_effective: dict[str, Any] = {}
        normalized_route: dict[str, Any] | None = None
        decision_id = None
        stage = None
        if latest_plan is not None:
            if set(latest_plan) != {
                "schema_version", "contract", "decision_id", "video",
                "sampled_at", "expires_at", "stage", "admitted",
                "selected_route", "effective", "reason_codes", "tier", "retry_at",
            }:
                raise ValueError("invalid launch plan shape")
            if (
                latest_plan.get("schema_version") != 1
                or latest_plan.get("contract") != "resource-launch-plan-v1"
                or latest_plan.get("admitted") is not allow_new_job
                or latest_plan.get("tier") != tier
                or latest_plan.get("reason_codes") != reason_codes
            ):
                raise ValueError("launch plan does not match decision")
            decision_id = optional_text(latest_plan.get("decision_id"), maximum=32)
            if decision_id is None or not re.fullmatch(r"[0-9a-f]{32}", decision_id):
                raise ValueError("invalid decision id")
            route = latest_plan.get("selected_route")
            if route is not None:
                if not isinstance(route, dict) or set(route) != {
                    "model", "compute_type", "required_vram_mib",
                }:
                    raise ValueError("invalid selected route shape")
                normalized_route = {
                    "model": optional_text(route.get("model"), maximum=240),
                    "compute_type": optional_text(route.get("compute_type"), maximum=80),
                    "required_vram_mib": finite_number(
                        route.get("required_vram_mib"), minimum=0.0
                    ),
                }
                if (
                    decision.get("asr_model") != normalized_route["model"]
                    or decision.get("asr_compute_type") != normalized_route["compute_type"]
                ):
                    raise ValueError("launch route does not match decision")
            elif decision.get("asr_model") is not None or decision.get("asr_compute_type") is not None:
                raise ValueError("missing launch route")
            video_identity = latest_plan.get("video")
            if not isinstance(video_identity, dict) or set(video_identity) != {
                "canonical_path", "size", "mtime_ns",
            }:
                raise ValueError("invalid launch plan video identity")
            optional_text(video_identity.get("canonical_path"), maximum=4096)
            for key in ("size", "mtime_ns"):
                value = video_identity.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("invalid launch plan video identity")
            plan_sampled_at = finite_number(latest_plan.get("sampled_at"), minimum=1.0)
            plan_expires_at = finite_number(latest_plan.get("expires_at"), minimum=1.0)
            if plan_sampled_at > plan_expires_at or plan_expires_at < current_time:
                raise ValueError("expired launch plan")
            stage = optional_text(latest_plan.get("stage"), maximum=80)
            plan_effective = latest_plan.get("effective")
            if not isinstance(plan_effective, dict) or set(plan_effective) != {
                "concurrency", "batch_size", "translation_context_max_blocks",
                "translation_context_max_chars", "whisperx_batch_size",
                "transformers_whisper_batch_size",
            }:
                raise ValueError("invalid effective launch plan")
            for key in plan_effective:
                optional_positive_int(plan_effective.get(key))
        task_id = None
        selected_model = normalized_route.get("model") if normalized_route else None
        selected_compute_type = normalized_route.get("compute_type") if normalized_route else None
        selected_required_vram_mib = (
            normalized_route.get("required_vram_mib") if normalized_route else None
        )
        fallback_selected = "lower_memory_fallback_selected" in normalized_reasons
        batch_size = optional_positive_int(plan_effective.get("batch_size"))
        context_max_blocks = optional_positive_int(
            plan_effective.get("translation_context_max_blocks")
        )
        context_max_chars = optional_positive_int(
            plan_effective.get("translation_context_max_chars")
        )
        concurrency = optional_positive_int(
            plan_effective.get("concurrency", decision.get("concurrency_limit"))
        )
        if concurrency is None:
            raise ValueError("missing effective concurrency")
        retry_after_seconds = finite_number(
            decision.get("retry_after"), minimum=0.0, maximum=86400.0
        )
        retry_at_raw = latest_plan.get("retry_at") if latest_plan is not None else None
        retry_at = (
            finite_number(retry_at_raw, minimum=0.0)
            if retry_at_raw is not None
            else None
        )
    except ValueError:
        return _resource_admission_unavailable(
            "state_invalid_decision",
            sampled_at=sampled_at,
            max_age_seconds=max_age_seconds,
            sample_age_seconds=round(sample_age_seconds, 3),
        )

    return {
        "available": True,
        "error_code": None,
        "schema": RESOURCE_ADMISSION_STATE_SCHEMA,
        "sampled_at": sampled_at,
        "max_age_seconds": max_age_seconds,
        "sample_age_seconds": round(sample_age_seconds, 3),
        "stale": False,
        "decision_id": decision_id,
        "task_id": task_id,
        "job_stage": stage,
        "tier": tier,
        "allow_new_job": allow_new_job,
        "allow_running_job": allow_running_job,
        "reason_codes": normalized_reasons,
        "selected_route": {
            "model": selected_model,
            "compute_type": selected_compute_type,
            "required_vram_mib": selected_required_vram_mib,
            "fallback_selected": fallback_selected,
        },
        "effective": {
            "batch_size": batch_size,
            "context_max_blocks": context_max_blocks,
            "context_max_chars": context_max_chars,
            "concurrency": concurrency,
            "whisperx_batch_size": optional_positive_int(
                plan_effective.get("whisperx_batch_size")
            ),
            "transformers_whisper_batch_size": optional_positive_int(
                plan_effective.get("transformers_whisper_batch_size")
            ),
        },
        "retry_after_seconds": retry_after_seconds,
        "retry_at": retry_at,
    }


def _telemetry_unavailable(error_code: str) -> dict[str, Any]:
    return {"available": False, "error_code": error_code}


def _collect_cpu_telemetry() -> dict[str, Any]:
    if psutil is not None:
        try:
            utilization = float(psutil.cpu_percent(interval=0.05))
            logical_processors = psutil.cpu_count(logical=True)
        except PermissionError:
            return _telemetry_unavailable("cpu_permission_denied")
        except Exception:
            return _telemetry_unavailable("cpu_probe_failed")
        if not math.isfinite(utilization) or not 0.0 <= utilization <= 100.0:
            return _telemetry_unavailable("cpu_parse_error")
        return {
            "available": True,
            "error_code": None,
            "provider": "psutil",
            "utilization_percent": round(utilization, 1),
            "logical_processors": int(logical_processors or os.cpu_count() or 0) or None,
        }

    try:
        first_line = Path("/proc/stat").read_text(encoding="ascii", errors="strict").splitlines()[0]
        parts = first_line.split()
        if not parts or parts[0] != "cpu" or len(parts) < 5:
            raise ValueError("invalid aggregate CPU row")
        values = [float(value) for value in parts[1:]]
        # Linux reports guest time as a subset of user/nice; only the first
        # eight fields are independent CPU time buckets.
        total = sum(values[:8])
        idle = values[3] + (values[4] if len(values) > 4 else 0.0)
    except FileNotFoundError:
        return _telemetry_unavailable("cpu_probe_missing")
    except PermissionError:
        return _telemetry_unavailable("cpu_permission_denied")
    except (OSError, UnicodeError, ValueError, IndexError):
        return _telemetry_unavailable("cpu_parse_error")

    global _PROC_CPU_SAMPLE
    with _PROC_CPU_SAMPLE_LOCK:
        previous = _PROC_CPU_SAMPLE
        _PROC_CPU_SAMPLE = (total, idle)
    if previous is None:
        return _telemetry_unavailable("cpu_warming_up")
    total_delta = total - previous[0]
    idle_delta = idle - previous[1]
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        return _telemetry_unavailable("cpu_sample_invalid")
    utilization = 100.0 * (1.0 - (idle_delta / total_delta))
    return {
        "available": True,
        "error_code": None,
        "provider": "procfs",
        "utilization_percent": round(min(100.0, max(0.0, utilization)), 1),
        "logical_processors": int(os.cpu_count() or 0) or None,
    }


def _collect_ram_telemetry() -> dict[str, Any]:
    if psutil is not None:
        try:
            memory = psutil.virtual_memory()
            total = int(memory.total)
            available = int(memory.available)
            used = int(memory.used)
            utilization = float(memory.percent)
        except PermissionError:
            return _telemetry_unavailable("ram_permission_denied")
        except Exception:
            return _telemetry_unavailable("ram_probe_failed")
        provider = "psutil"
    else:
        try:
            fields: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="ascii", errors="strict").splitlines():
                key, separator, raw_value = line.partition(":")
                if not separator:
                    continue
                match = re.fullmatch(r"\s*(\d+)\s+kB\s*", raw_value)
                if match:
                    fields[key] = int(match.group(1)) * 1024
            total = fields["MemTotal"]
            available = fields.get("MemAvailable")
            if available is None:
                available = fields.get("MemFree", 0) + fields.get("Buffers", 0) + fields.get("Cached", 0)
            used = total - available
            utilization = 100.0 * used / total
        except FileNotFoundError:
            return _telemetry_unavailable("ram_probe_missing")
        except PermissionError:
            return _telemetry_unavailable("ram_permission_denied")
        except (KeyError, OSError, UnicodeError, ValueError, ZeroDivisionError):
            return _telemetry_unavailable("ram_parse_error")
        provider = "procfs"

    if (
        total <= 0
        or available < 0
        or used < 0
        or available > total
        or used > total
        or not math.isfinite(utilization)
        or not 0.0 <= utilization <= 100.0
    ):
        return _telemetry_unavailable("ram_parse_error")
    return {
        "available": True,
        "error_code": None,
        "provider": provider,
        "utilization_percent": round(utilization, 1),
        "used_bytes": used,
        "total_bytes": total,
        "available_bytes": available,
    }


def _nvidia_smi_number(value: str, *, integer: bool = False) -> float | int | None:
    normalized = str(value or "").strip()
    if not normalized or normalized.casefold() in {
        "n/a", "[n/a]", "na", "not supported", "[not supported]",
    }:
        return None
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", normalized):
        raise ValueError("invalid nvidia-smi numeric field")
    number = float(normalized)
    if not math.isfinite(number):
        raise ValueError("non-finite nvidia-smi numeric field")
    return int(number) if integer and number.is_integer() else number


def _parse_nvidia_smi_gpu_rows(output: str) -> list[dict[str, Any]]:
    if len(output.encode("utf-8", errors="replace")) > 256 * 1024:
        raise ValueError("nvidia-smi output exceeds parser bound")
    devices: list[dict[str, Any]] = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if not row or all(not value.strip() for value in row):
            continue
        if len(row) != 8:
            raise ValueError("unexpected nvidia-smi GPU column count")
        index = _nvidia_smi_number(row[0], integer=True)
        if not isinstance(index, int) or index < 0:
            raise ValueError("invalid nvidia-smi GPU index")
        uuid = row[1].strip()
        name = row[2].strip()
        if not uuid or not name:
            raise ValueError("missing nvidia-smi GPU identity")
        utilization = _nvidia_smi_number(row[3])
        memory_used = _nvidia_smi_number(row[4])
        memory_total = _nvidia_smi_number(row[5])
        memory_free = _nvidia_smi_number(row[6])
        temperature = _nvidia_smi_number(row[7])
        for value in (utilization, memory_used, memory_total, memory_free, temperature):
            if value is not None and float(value) < 0:
                raise ValueError("negative nvidia-smi metric")
        if utilization is not None and float(utilization) > 100:
            raise ValueError("invalid nvidia-smi utilization")
        devices.append(
            {
                "index": index,
                "_uuid": uuid,
                "name": name,
                "utilization_percent": float(utilization) if utilization is not None else None,
                "memory_used_mib": float(memory_used) if memory_used is not None else None,
                "memory_total_mib": float(memory_total) if memory_total is not None else None,
                "memory_free_mib": float(memory_free) if memory_free is not None else None,
                "temperature_celsius": float(temperature) if temperature is not None else None,
            }
        )
    if not devices:
        raise ValueError("nvidia-smi returned no GPU rows")
    return devices


def _parse_nvidia_smi_process_rows(output: str) -> dict[str, int]:
    if len(output.encode("utf-8", errors="replace")) > 256 * 1024:
        raise ValueError("nvidia-smi process output exceeds parser bound")
    processes: dict[str, set[int]] = {}
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if not row or all(not value.strip() for value in row):
            continue
        if len(row) == 1 and row[0].strip().casefold().startswith("no running"):
            continue
        if len(row) != 2:
            raise ValueError("unexpected nvidia-smi process column count")
        uuid = row[0].strip()
        pid = _nvidia_smi_number(row[1], integer=True)
        if not uuid or not isinstance(pid, int) or pid <= 0:
            raise ValueError("invalid nvidia-smi process row")
        processes.setdefault(uuid, set()).add(pid)
    return {uuid: len(pids) for uuid, pids in processes.items()}


def _run_nvidia_smi(executable: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable, *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _run_worker_nvidia_smi(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["nvidia-smi", *arguments]
    try:
        created = _docker_request(
            "POST",
            f"/containers/{WORKER_CONTAINER_NAME}/exec",
            {
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": True,
                "Cmd": command,
            },
            timeout_seconds=NVIDIA_SMI_TIMEOUT_SECONDS,
        )
        exec_id = created.get("Id")
        if not exec_id:
            raise OSError("Docker exec did not return an id")
        output = _docker_request(
            "POST",
            f"/exec/{exec_id}/start",
            {"Detach": False, "Tty": True},
            parse_json=False,
            timeout_seconds=NVIDIA_SMI_TIMEOUT_SECONDS,
        )
        inspected = _docker_request(
            "GET",
            f"/exec/{exec_id}/json",
            timeout_seconds=NVIDIA_SMI_TIMEOUT_SECONDS,
        )
    except HTTPException as exc:
        if int(getattr(exc, "status_code", 0) or 0) == 504:
            raise subprocess.TimeoutExpired(command, NVIDIA_SMI_TIMEOUT_SECONDS) from None
        raise OSError("worker container nvidia-smi unavailable") from None
    exit_code = inspected.get("ExitCode")
    return subprocess.CompletedProcess(
        args=command,
        returncode=int(exit_code) if isinstance(exit_code, int) else 1,
        stdout=str(output),
        stderr="",
    )


def _collect_gpu_telemetry() -> dict[str, Any]:
    try:
        executable = shutil.which("nvidia-smi")
    except OSError:
        executable = None
    try:
        docker_socket_available = DOCKER_SOCKET.exists()
    except OSError:
        docker_socket_available = False
    use_worker_container = not executable and docker_socket_available
    if not executable and not use_worker_container:
        return _telemetry_unavailable("nvidia_smi_missing")

    def run_query(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if executable:
            return _run_nvidia_smi(executable, arguments)
        return _run_worker_nvidia_smi(arguments)

    try:
        gpu_query = run_query(
            [
                "--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,memory.free,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
        )
    except subprocess.TimeoutExpired:
        return _telemetry_unavailable("nvidia_smi_timeout")
    except OSError:
        return _telemetry_unavailable("nvidia_smi_execute_failed")
    if gpu_query.returncode != 0:
        return _telemetry_unavailable("nvidia_smi_failed")
    try:
        devices = _parse_nvidia_smi_gpu_rows(gpu_query.stdout)
    except (csv.Error, UnicodeError, ValueError):
        return _telemetry_unavailable("nvidia_smi_parse_error")

    process_error_code: str | None = None
    process_counts: dict[str, int] | None = None
    try:
        process_query = run_query(
            ["--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"],
        )
        if process_query.returncode != 0:
            process_error_code = "nvidia_smi_process_query_failed"
        else:
            process_counts = _parse_nvidia_smi_process_rows(process_query.stdout)
    except subprocess.TimeoutExpired:
        process_error_code = "nvidia_smi_process_timeout"
    except OSError:
        process_error_code = "nvidia_smi_process_execute_failed"
    except (csv.Error, UnicodeError, ValueError):
        process_error_code = "nvidia_smi_process_parse_error"

    for device in devices:
        uuid = str(device.pop("_uuid"))
        device["process_count"] = process_counts.get(uuid, 0) if process_counts is not None else None

    def summed(key: str) -> float | None:
        values = [float(device[key]) for device in devices if device.get(key) is not None]
        return round(sum(values), 1) if len(values) == len(devices) else None

    utilizations = [
        float(device["utilization_percent"])
        for device in devices
        if device.get("utilization_percent") is not None
    ]
    temperatures = [
        float(device["temperature_celsius"])
        for device in devices
        if device.get("temperature_celsius") is not None
    ]
    aggregate_process_count = (
        sum(int(device["process_count"]) for device in devices)
        if process_counts is not None
        else None
    )
    return {
        "available": True,
        "error_code": None,
        "provider": "nvidia-smi-worker-container" if use_worker_container else "nvidia-smi",
        "process_error_code": process_error_code,
        "devices": devices,
        "aggregate": {
            "device_count": len(devices),
            "utilization_percent": round(max(utilizations), 1) if utilizations else None,
            "memory_used_mib": summed("memory_used_mib"),
            "memory_total_mib": summed("memory_total_mib"),
            "memory_free_mib": summed("memory_free_mib"),
            "temperature_celsius": round(max(temperatures), 1) if temperatures else None,
            "process_count": aggregate_process_count,
        },
    }


def _collect_resource_telemetry() -> dict[str, Any]:
    return {
        "sampled_at": int(time.time()),
        "max_age_seconds": RESOURCE_OVERVIEW_MAX_AGE_SECONDS,
        "cpu": _collect_cpu_telemetry(),
        "ram": _collect_ram_telemetry(),
        "gpu": _collect_gpu_telemetry(),
    }


def _resource_telemetry_pending(error_code: str = "probe_pending") -> dict[str, Any]:
    return {
        "sampled_at": None,
        "max_age_seconds": RESOURCE_OVERVIEW_MAX_AGE_SECONDS,
        "cpu": _telemetry_unavailable(error_code),
        "ram": _telemetry_unavailable(error_code),
        "gpu": _telemetry_unavailable(error_code),
    }


def _resource_telemetry_refresh_worker() -> None:
    try:
        value = _collect_resource_telemetry()
    except Exception:
        value = _resource_telemetry_pending("probe_failed")
    with _RESOURCE_TELEMETRY_CACHE_LOCK:
        _RESOURCE_TELEMETRY_CACHE["value"] = value
        _RESOURCE_TELEMETRY_CACHE["expires_at"] = (
            time.monotonic() + RESOURCE_TELEMETRY_CACHE_TTL_SECONDS
        )
        _RESOURCE_TELEMETRY_CACHE["refreshing"] = False


def _resource_telemetry_summary() -> dict[str, Any]:
    now = time.monotonic()
    start_refresh = False
    with _RESOURCE_TELEMETRY_CACHE_LOCK:
        value = _RESOURCE_TELEMETRY_CACHE.get("value")
        expires_at = float(_RESOURCE_TELEMETRY_CACHE.get("expires_at") or 0.0)
        if value is not None and now < expires_at:
            result = copy.deepcopy(value)
            result["stale"] = False
            result["refreshing"] = False
            return result
        if not bool(_RESOURCE_TELEMETRY_CACHE.get("refreshing")):
            _RESOURCE_TELEMETRY_CACHE["refreshing"] = True
            start_refresh = True

    if start_refresh:
        try:
            threading.Thread(
                target=_resource_telemetry_refresh_worker,
                name="resource-telemetry",
                daemon=True,
            ).start()
        except Exception:
            with _RESOURCE_TELEMETRY_CACHE_LOCK:
                _RESOURCE_TELEMETRY_CACHE["refreshing"] = False
            if value is None:
                value = _resource_telemetry_pending("probe_start_failed")

    with _RESOURCE_TELEMETRY_CACHE_LOCK:
        current = _RESOURCE_TELEMETRY_CACHE.get("value")
        current_expires_at = float(_RESOURCE_TELEMETRY_CACHE.get("expires_at") or 0.0)
        refreshing = bool(_RESOURCE_TELEMETRY_CACHE.get("refreshing"))
    if current is not None:
        result = copy.deepcopy(current)
        result["stale"] = time.monotonic() >= current_expires_at
        result["refreshing"] = refreshing
        sampled_at = result.get("sampled_at")
        result["sample_age_seconds"] = (
            max(0, int(time.time() - float(sampled_at)))
            if isinstance(sampled_at, (int, float)) and sampled_at > 0
            else None
        )
        return result
    result = copy.deepcopy(value) if value is not None else _resource_telemetry_pending()
    result["stale"] = False
    result["refreshing"] = refreshing
    result["sample_age_seconds"] = None
    return result


def _log_file_info(filename: str) -> dict[str, Any]:
    path = LOG_PATH / filename
    if not path.exists():
        return {"path": str(path), "exists": False, "size": 0}
    stat = path.stat()
    return {"path": str(path), "exists": True, "size": stat.st_size, "mtime": stat.st_mtime}


def _mikan_operation_state() -> dict[str, Any]:
    lock_specs = (
        ("state", WORK_PATH / "mikan_worker.lock"),
        ("subtitle_extract", WORK_PATH / "mikan_extract.lock"),
        ("enqueue", WORK_PATH / "mikan_enqueue.lock"),
        ("redownload", WORK_PATH / "mikan_redownload.lock"),
    )
    locks = [(label, _lock_file_summary(path)) for label, path in lock_specs]
    _mark_reused_pid_stale_locks([lock for _label, lock in locks])
    active_operations = [label for label, lock in locks if _lock_is_active(lock)]
    redownload_request = _request_file_summary(WORK_PATH / "mikan_redownload_all.request.json")
    redownload_active = _request_file_summary(WORK_PATH / "mikan_redownload_all.active.json")
    redownload_cancel = _request_file_summary(WORK_PATH / MIKAN_REDOWNLOAD_CANCEL_NAME)
    active_age = int(redownload_active.get("age_seconds") or 0) if redownload_active.get("exists") else None
    redownload_is_live = bool(
        redownload_active.get("exists")
        and active_age is not None
        and active_age <= MIKAN_REDOWNLOAD_ACTIVE_STALE_SECONDS
    )
    if redownload_active.get("exists"):
        redownload_active["active"] = redownload_is_live
        redownload_active["stale"] = not redownload_is_live
    if redownload_is_live and "redownload" not in active_operations:
        active_operations.append("redownload")
    return {
        "busy": bool(active_operations),
        "active_operations": active_operations,
        "redownload_request": redownload_request,
        "redownload_active": redownload_active,
        "redownload_cancel": redownload_cancel,
    }


def _mikan_summary(config: dict[str, Any] | None = None, *, include_downloads: bool = True) -> dict[str, Any]:
    if config is None:
        config = _load_config()
    lock_path = WORK_PATH / "mikan_worker.lock"
    extract_lock_path = WORK_PATH / "mikan_extract.lock"
    queue_lock_path = WORK_PATH / "mikan_enqueue.lock"
    redownload_lock_path = WORK_PATH / "mikan_redownload.lock"
    reset_request_path = WORK_PATH / "mikan_reset_all.request.json"
    redownload_request_path = WORK_PATH / "mikan_redownload_all.request.json"
    redownload_active_path = WORK_PATH / "mikan_redownload_all.active.json"
    redownload_cancel_path = WORK_PATH / MIKAN_REDOWNLOAD_CANCEL_NAME
    completed_state_update_request_path = WORK_PATH / "mikan_completed_state_update.request.json"
    lock = _lock_file_summary(lock_path)
    extract_lock = _lock_file_summary(extract_lock_path)
    queue_lock = _lock_file_summary(queue_lock_path)
    redownload_lock = _lock_file_summary(redownload_lock_path)
    _mark_reused_pid_stale_locks([lock, extract_lock, queue_lock, redownload_lock])
    reset_request = _request_file_summary(reset_request_path)
    redownload_request = _request_file_summary(redownload_request_path)
    redownload_active = _request_file_summary(redownload_active_path)
    redownload_cancel = _request_file_summary(redownload_cancel_path)
    if redownload_active.get("exists"):
        active_age = int(redownload_active.get("age_seconds") or 0)
        active_is_live = bool(
            active_age <= MIKAN_REDOWNLOAD_ACTIVE_STALE_SECONDS
            or _lock_is_active(redownload_lock)
            or _lock_is_active(queue_lock)
            or _lock_is_active(lock)
        )
        redownload_active["active"] = active_is_live
        redownload_active["stale"] = not active_is_live
        if not active_is_live:
            redownload_active["stale_reason"] = "active marker exists but no redownload, queue, or state lock is active"
    completed_state_update_request = _request_file_summary(completed_state_update_request_path)
    completed_poll_interval = _coerce_int(config.get("mikan_completed_poll_interval_seconds")) or 30
    state_db = _mikan_state_db_summary(config)
    pipeline = state_db.get("pipeline") if isinstance(state_db.get("pipeline"), dict) else {}
    extract_jobs = state_db.get("extract_jobs") if isinstance(state_db.get("extract_jobs"), dict) else {}
    extracting = max(
        int(pipeline.get("extracting") or 0),
        int(extract_jobs.get("active") or 0),
    )
    active_operations = []
    if _lock_is_active(lock):
        active_operations.append("state")
    if _lock_is_active(extract_lock) or extracting > 0:
        active_operations.append("subtitle_extract")
    if _lock_is_active(queue_lock):
        active_operations.append("enqueue")
    if _lock_is_active(redownload_lock) or bool(redownload_active.get("active")):
        active_operations.append("redownload")
    result = {
        "busy": bool(active_operations),
        "active_operations": active_operations,
        "lock": lock,
        "extract_lock": extract_lock,
        "queue_lock": queue_lock,
        "redownload_lock": redownload_lock,
        "completed_poll_interval_seconds": max(1, completed_poll_interval),
        "reset_request": reset_request,
        "redownload_request": redownload_request,
        "redownload_active": redownload_active,
        "redownload_cancel": redownload_cancel,
        "completed_state_update_request": completed_state_update_request,
        "state_db": state_db,
    }
    if include_downloads:
        result["downloads"] = _mikan_downloads_summary(config)
    return result


def _mikan_downloads_summary(
    config: dict[str, Any],
    *,
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
    search: str = "",
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    normalized_status = str(status_filter or "").strip()
    if normalized_status == "all":
        normalized_status = ""
    normalized_search = str(search or "").strip()
    pending_path = _mikan_pending_path(config)
    result: dict[str, Any] = {
        "path": str(pending_path),
        "exists": False,
        "updated_at": 0.0,
        "total": 0,
        "filtered": 0,
        "counts": {},
        "recent": [],
        "page": page,
        "page_size": page_size,
        "page_count": 1,
        "filter": {"status": normalized_status, "search": normalized_search},
    }

    sqlite_result = _mikan_downloads_summary_from_sqlite(
        config,
        result,
        page=page,
        page_size=page_size,
        status_filter=normalized_status,
        search=normalized_search,
    )
    if sqlite_result is not None:
        return sqlite_result

    if not pending_path.exists():
        return result

    try:
        stat = pending_path.stat()
    except OSError as exc:
        return {**result, "exists": True, "error": str(exc)}

    cache_key = (str(pending_path), stat.st_mtime_ns, stat.st_size)
    cached = _MIKAN_DOWNLOADS_CACHE.get(cache_key)
    if cached is None:
        try:
            payload = json.loads(pending_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            return {**result, "exists": True, "updated_at": stat.st_mtime, "error": str(exc)}

        if not isinstance(payload, dict):
            return {**result, "exists": True, "updated_at": stat.st_mtime, "error": "Mikan pending file root must be an object"}
        raw_items = payload.get("items", {})
        if not isinstance(raw_items, dict):
            raw_items = {}

        grouped: dict[str, dict[str, Any]] = {}
        now = time.time()
        for key, raw_entry in raw_items.items():
            if not isinstance(raw_entry, dict):
                continue
            entry = _mikan_download_entry(str(key), raw_entry, now)
            group_key = entry["group_key"]
            existing = grouped.get(group_key)
            if existing is None:
                entry["children"] = [_mikan_download_child_entry(entry)]
                grouped[group_key] = entry
                continue
            _merge_mikan_download_entry(existing, entry)

        rows = sorted(grouped.values(), key=_mikan_download_sort_key)
        counts: dict[str, int] = {}
        for row in rows:
            row["children"] = sorted(row.get("children", []), key=_mikan_download_child_sort_key)
            status = str(row.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        extracted_total = sum(int(row.get("last_extracted_count") or 0) for row in rows)
        extracted_unknown_completed = sum(
            1
            for row in rows
            if row.get("status") == "completed" and int(row.get("last_extracted_count") or 0) <= 0
        )
        cached = {
            "rows": rows,
            "counts": counts,
            "extracted_total": extracted_total,
            "extracted_unknown_completed": extracted_unknown_completed,
            "updated_at": stat.st_mtime,
        }
        _MIKAN_DOWNLOADS_CACHE.clear()
        _MIKAN_DOWNLOADS_CACHE[cache_key] = cached

    rows = _copy_mikan_download_rows(cached["rows"])
    _apply_active_mikan_extract_jobs(rows, _mikan_extract_jobs_summary_from_state_db(config))
    rows = sorted(rows, key=_mikan_download_sort_key)
    filtered_rows = _filter_mikan_download_rows(rows, status_filter=normalized_status, search=normalized_search)
    counts = _mikan_download_counts(rows)
    extracted_total = cached["extracted_total"]
    extracted_unknown_completed = cached["extracted_unknown_completed"]
    page_count = max(1, (len(filtered_rows) + page_size - 1) // page_size)
    page = min(page, page_count)
    page_start = (page - 1) * page_size
    page_rows = filtered_rows[page_start : page_start + page_size]

    public_rows = _public_mikan_download_rows(page_rows)

    return {
        **result,
        "exists": True,
        "source": "json",
        "updated_at": cached["updated_at"],
        "total": len(rows),
        "filtered": len(filtered_rows),
        "counts": counts,
        "extracted_total": extracted_total,
        "extracted_unknown_completed": extracted_unknown_completed,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "recent": public_rows,
    }


def _mikan_downloads_summary_from_sqlite(
    config: dict[str, Any],
    base_result: dict[str, Any],
    *,
    page: int,
    page_size: int,
    status_filter: str,
    search: str,
) -> dict[str, Any] | None:
    db_path = _mikan_state_db_path(config)
    if not db_path.exists():
        return None
    try:
        stat = db_path.stat()
    except OSError:
        return None
    cache_key = (
        "downloads",
        str(db_path),
        page,
        page_size,
        status_filter,
        search,
    )
    cached_result = _ttl_cache_get(_MIKAN_SQLITE_DOWNLOADS_CACHE, cache_key, SQLITE_DOWNLOADS_CACHE_TTL_SECONDS)
    if cached_result is not None:
        return cached_result

    conn: sqlite3.Connection | None = None
    try:
        conn = _sqlite_connect(db_path, readonly=True)
        if not _sqlite_table_exists(conn, "mikan_download_items"):
            return None
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(mikan_download_items)").fetchall()}
        required_columns = {
            "key",
            "bangumi_id",
            "episode",
            "status",
            "title",
            "torrent_url",
            "updated_at",
            "last_extracted_count",
            "total_extracted_count",
            "raw_json",
        }
        if not required_columns.issubset(columns):
            return None

        has_extract_jobs = _sqlite_table_exists(conn, "mikan_extract_jobs")
        active_jobs = _mikan_active_extract_jobs_from_conn(conn) if has_extract_jobs else []
        # Keep SQL fast: build pages from persisted download state, then overlay active extract jobs
        # on the returned rows in Python. The old correlated EXISTS overlay made first-page loads
        # block for many seconds on busy SQLite files.
        grouped_cte = _mikan_sqlite_download_groups_cte(
            columns,
            has_extract_jobs=False,
            include_search_groups=bool(search),
        )
        search_pattern = f"%{search}%" if search else ""
        cte_params: list[Any] = [search_pattern] * 4 if search else []
        counts: dict[str, int] = {}
        total = 0
        extracted_total = 0
        extracted_unknown_completed = 0
        for status, count, extracted_count, unknown_completed_count in conn.execute(
            f"""
            {grouped_cte}
            SELECT
                status,
                COUNT(*),
                COALESCE(SUM(last_extracted_count), 0),
                COALESCE(SUM(
                    CASE
                        WHEN status = 'completed' AND COALESCE(last_extracted_count, 0) <= 0 THEN 1
                        ELSE 0
                    END
                ), 0)
            FROM rep
            GROUP BY status
            """
            ,
            cte_params,
        ).fetchall():
            row_count = int(count or 0)
            counts[str(status)] = row_count
            total += row_count
            extracted_total += int(extracted_count or 0)
            extracted_unknown_completed += int(unknown_completed_count or 0)
        active_overlays = _mikan_active_extract_overlays(conn, columns, active_jobs)
        counts = _mikan_counts_with_active_extract_jobs(conn, columns, counts, active_jobs)
        if has_extract_jobs and status_filter in {"failed", "replaced", "terminal_failed", "success"}:
            return _mikan_extract_jobs_downloads_result(
                conn,
                base_result,
                db_path=db_path,
                stat=stat,
                page=page,
                page_size=page_size,
                status_filter=status_filter,
                search=search,
                counts=counts,
                extracted_total=extracted_total,
                extracted_unknown_completed=extracted_unknown_completed,
                cache_key=cache_key,
            )
        if status_filter in {"extracting_subtitles", "completed_waiting_extract"}:
            desired_job_status = "running" if status_filter == "extracting_subtitles" else "queued"
            active_page_keys_all = _mikan_sqlite_group_keys_for_active_extract_jobs(
                conn,
                columns,
                active_jobs,
                desired_job_status,
            )
            filtered = len(active_page_keys_all)
            page_count = max(1, (filtered + page_size - 1) // page_size)
            page = min(max(1, page), page_count)
            offset = (page - 1) * page_size
            page_keys = active_page_keys_all[offset : offset + page_size]
            page_rows = _mikan_sqlite_download_rows(conn, page_keys)
            _apply_active_mikan_extract_jobs(page_rows, {"recent": active_jobs})
            page_rows = sorted(page_rows, key=_mikan_download_sort_key)
            return _ttl_cache_set(_MIKAN_SQLITE_DOWNLOADS_CACHE, cache_key, {
                **base_result,
                "exists": True,
                "source": "sqlite",
                "database": str(db_path),
                "updated_at": stat.st_mtime,
                "total": total,
                "filtered": filtered,
                "counts": counts,
                "extracted_total": extracted_total,
                "extracted_unknown_completed": extracted_unknown_completed,
                "page": page,
                "page_size": page_size,
                "page_count": page_count,
                "recent": _public_mikan_download_rows(page_rows),
            })
        filter_parts: list[str] = []
        filter_params: list[Any] = []
        if status_filter:
            filter_parts.append("rep.status = ?")
            filter_params.append(status_filter)
            if active_overlays:
                overlay_placeholders = ",".join("?" for _ in active_overlays)
                filter_parts.append(f"rep.group_key NOT IN ({overlay_placeholders})")
                filter_params.extend(active_overlays)
        if search:
            filter_parts.append("rep.group_key IN (SELECT group_key FROM matching_groups)")
        filter_sql = f"WHERE {' AND '.join(filter_parts)}" if filter_parts else ""
        if filter_sql:
            filtered = int(
                conn.execute(
                    f"{grouped_cte} SELECT COUNT(*) FROM rep {filter_sql}",
                    [*cte_params, *filter_params],
                ).fetchone()[0]
                or 0
            )
        else:
            filtered = total
        page_count = max(1, (filtered + page_size - 1) // page_size)
        page = min(max(1, page), page_count)
        offset = (page - 1) * page_size
        page_keys = [
            str(group_key)
            for (group_key,) in conn.execute(
                f"""
                {grouped_cte}
                SELECT group_key
                FROM rep
                {filter_sql}
                ORDER BY sort_priority, updated_at DESC, sort_title
                LIMIT ? OFFSET ?
                """,
                [*cte_params, *filter_params, page_size, offset],
            ).fetchall()
        ]
        page_rows = _mikan_sqlite_download_rows(conn, page_keys)
        _apply_active_mikan_extract_jobs(page_rows, {"recent": active_jobs})
        page_rows = sorted(page_rows, key=_mikan_download_sort_key)
        return _ttl_cache_set(_MIKAN_SQLITE_DOWNLOADS_CACHE, cache_key, {
            **base_result,
            "exists": True,
            "source": "sqlite",
            "database": str(db_path),
            "updated_at": stat.st_mtime,
            "total": total,
            "filtered": filtered,
            "counts": counts,
            "extracted_total": extracted_total,
            "extracted_unknown_completed": extracted_unknown_completed,
            "page": page,
            "page_size": page_size,
            "page_count": page_count,
            "recent": _public_mikan_download_rows(page_rows),
        })
    except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError):
        return None
    finally:
        if conn is not None:
            conn.close()


def _copy_mikan_download_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for row in rows:
        clone = dict(row)
        children = row.get("children")
        if isinstance(children, list):
            clone["children"] = [dict(child) for child in children if isinstance(child, dict)]
        copied.append(clone)
    return copied


def _public_mikan_download_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_rows: list[dict[str, Any]] = []
    for row in rows:
        clean = dict(row)
        clean.pop("group_key", None)
        clean.pop("sort_priority", None)
        clean["problem"] = _mikan_download_problem(clean)
        children = clean.get("children")
        if isinstance(children, list):
            clean["children"] = [
                {
                    key: value
                    for key, value in dict(child).items()
                    if key not in {"group_key", "sort_priority"}
                }
                for child in children
                if isinstance(child, dict)
            ]
        public_rows.append(clean)
    return public_rows


def _compact_mikan_downloads_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the fields rendered by the dashboard without nested raw diagnostics."""
    compact_fields = (
        "key",
        "job_key",
        "status",
        "title",
        "torrent_name",
        "source",
        "bangumi_id",
        "episode",
        "episodes",
        "episode_count",
        "source_published_at",
        "source_published_precision",
        "torrent_created_at",
        "torrent_added_at",
        "torrent_completed_at",
        "queued_at",
        "completed_at",
        "last_extracted_at",
        "last_extracted_count",
        "total_extracted_count",
        "last_qbit_sync_at",
        "last_qbit_state",
        "last_qbit_name",
        "last_extract_failed_at",
        "no_candidate_until",
        "deferred_reason",
        "last_failure_reason",
        "last_extract_failure_reason",
        "last_extract_deferred_reason",
        "subtitle_state",
        "failed_count",
        "progress",
        "downloaded",
        "dlspeed",
        "next_action",
        "updated_at",
        "age_seconds",
        "extract_job_status",
        "extract_job_attempts",
        "extract_job_started_at",
        "extract_job_updated_at",
        "extract_file_path",
        "extract_file_timestamp",
        "extract_file_time_kind",
        "extract_file_size",
    )
    compact_rows: list[dict[str, Any]] = []
    for raw_row in payload.get("recent") or []:
        if not isinstance(raw_row, dict):
            continue
        row = {key: raw_row[key] for key in compact_fields if key in raw_row}
        row["problem"] = _mikan_download_problem(raw_row)
        children = raw_row.get("children")
        if isinstance(children, list):
            row["child_count"] = len(children)
        context = raw_row.get("last_extract_context")
        if isinstance(context, dict):
            compact_context = {
                key: context[key]
                for key in (
                    "source_video",
                    "source_video_exists",
                    "target_video",
                    "qbit_content_path",
                    "mapped_root",
                    "mapped_root_exists",
                )
                if key in context and context[key] not in (None, "")
            }
            candidates = context.get("target_candidates")
            if isinstance(candidates, list):
                compact_context["target_candidates"] = [
                    {
                        key: candidate.get(key)
                        for key in ("path", "score", "reasons")
                        if candidate.get(key) not in (None, "")
                    }
                    for candidate in candidates[:3]
                    if isinstance(candidate, dict)
                ]
            if compact_context:
                row["last_extract_context"] = compact_context
        compact_rows.append(row)

    return {**payload, "recent": compact_rows, "compact": True}


def _mikan_extract_jobs_empty() -> dict[str, Any]:
    return {
        "counts": {},
        "active": 0,
        "retryable_count": 0,
        "recent": [],
        "recent_failed": [],
        "recent_retryable": [],
        "recent_attention": [],
        "recent_replaced": [],
        "recent_completed": [],
    }


def _mikan_extract_jobs_summary_from_state_db(
    config: dict[str, Any],
    *,
    include_history: bool = True,
    recent_limit: int = 200,
) -> dict[str, Any]:
    db_path = _mikan_state_db_path(config)
    if not db_path.exists():
        return _mikan_extract_jobs_empty()
    cache_key = ("extract-jobs", str(db_path), bool(include_history), max(1, int(recent_limit)))
    cached = _ttl_cache_get(_MIKAN_EXTRACT_JOBS_CACHE, cache_key, SUMMARY_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    conn: sqlite3.Connection | None = None
    try:
        conn = _sqlite_connect(db_path, readonly=True)
        return _ttl_cache_set(
            _MIKAN_EXTRACT_JOBS_CACHE,
            cache_key,
            _mikan_extract_jobs_from_conn(
                conn,
                include_history=include_history,
                recent_limit=recent_limit,
            ),
        )
    except sqlite3.Error:
        return _mikan_extract_jobs_empty()
    finally:
        if conn is not None:
            conn.close()


def _mikan_torrent_time_fields(torrent_json: Any) -> dict[str, float]:
    payload = _json_object(torrent_json)
    return {
        "torrent_created_at": _parse_timestamp(payload.get("creation_date")),
        "torrent_added_at": _parse_timestamp(payload.get("added_on")),
        "torrent_completed_at": _parse_timestamp(payload.get("completion_on")),
    }


def _mikan_lite_state(config: dict[str, Any]) -> dict[str, Any]:
    sqlite_state = _mikan_lite_state_from_sqlite(config)
    if sqlite_state is not None:
        return sqlite_state
    return _mikan_lite_state_from_pending(config)


def _mikan_lite_state_from_sqlite(config: dict[str, Any]) -> dict[str, Any] | None:
    db_path = _mikan_state_db_path(config)
    if not db_path.exists():
        return None
    try:
        stat = db_path.stat()
    except OSError:
        return None
    # The lite dashboard is polled frequently and only needs a short freshness
    # window.  Keying this cache by SQLite mtime made it miss whenever the
    # worker touched the DB, which defeated the purpose on large libraries.
    cache_key = ("lite", str(db_path))
    cached = _ttl_cache_get(_MIKAN_LITE_CACHE, cache_key, SQLITE_DOWNLOADS_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    conn: sqlite3.Connection | None = None
    try:
        conn = _sqlite_connect(db_path, readonly=True)
        extract_jobs = _mikan_extract_jobs_from_conn(conn, include_recent=False)
        if not _sqlite_table_exists(conn, "mikan_download_items"):
            pipeline = _mikan_pipeline_counts({}, extract_jobs.get("counts") or {})
            return _ttl_cache_set(_MIKAN_LITE_CACHE, cache_key, {
                "exists": True,
                "total": 0,
                "counts": {},
                "stalled": 0,
                "zero_speed_downloading": 0,
                "extract_jobs": extract_jobs,
                "pipeline": pipeline,
                "database_mtime": stat.st_mtime,
                "database_size": stat.st_size,
                "cache_ttl_seconds": SQLITE_DOWNLOADS_CACHE_TTL_SECONDS,
            })
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(mikan_download_items)").fetchall()}
        status_expr = _mikan_sqlite_completed_aware_status_expr(columns)
        counts = {
            str(status): int(count)
            for status, count in conn.execute(
                f"SELECT {status_expr} AS status, COUNT(*) FROM mikan_download_items GROUP BY {status_expr}"
            ).fetchall()
        }
        total = int(conn.execute("SELECT COUNT(*) FROM mikan_download_items").fetchone()[0] or 0)
        stalled = 0
        zero_speed_downloading = 0
        if {"last_qbit_state", "last_dlspeed"}.issubset(columns):
            stalled = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM mikan_download_items
                    WHERE last_qbit_state IN ('stalledDL', 'stalleddl', 'StalledDL', 'stalledDl', 'STALLEDDL')
                      AND COALESCE(last_dlspeed, 0) <= 0
                    """
                ).fetchone()[0]
                or 0
            )
            zero_speed_downloading = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM mikan_download_items
                    WHERE status = 'downloading'
                      AND COALESCE(last_dlspeed, 0) <= 0
                    """
                ).fetchone()[0]
                or 0
            )
        active_jobs = _mikan_active_extract_jobs_from_conn(conn)
        counts = _mikan_counts_with_active_extract_jobs(conn, columns, counts, active_jobs)
        extract_counts = extract_jobs.get("counts") if isinstance(extract_jobs.get("counts"), dict) else {}
        pipeline = _mikan_pipeline_counts(counts, extract_counts)
        return _ttl_cache_set(_MIKAN_LITE_CACHE, cache_key, {
            "exists": True,
            "total": total,
            "counts": counts,
            "stalled": stalled,
            "zero_speed_downloading": zero_speed_downloading,
            "extract_jobs": extract_jobs,
            "pipeline": pipeline,
            "database_mtime": stat.st_mtime,
            "database_size": stat.st_size,
            "cache_ttl_seconds": SQLITE_DOWNLOADS_CACHE_TTL_SECONDS,
        })
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            conn.close()


def _mikan_lite_state_from_pending(config: dict[str, Any]) -> dict[str, Any]:
    pending_path = _mikan_pending_path(config)
    result = {
        "exists": False,
        "total": 0,
        "counts": {},
        "stalled": 0,
        "zero_speed_downloading": 0,
        "extract_jobs": _mikan_extract_jobs_empty(),
        "pipeline": _mikan_pipeline_counts({}, {}),
    }
    if not pending_path.exists():
        return result
    try:
        payload = json.loads(pending_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {**result, "exists": True}
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, dict):
        return {**result, "exists": True}

    grouped: dict[str, dict[str, Any]] = {}
    now = time.time()
    for key, raw_entry in raw_items.items():
        if not isinstance(raw_entry, dict):
            continue
        entry = _mikan_download_entry(str(key), raw_entry, now)
        existing = grouped.get(str(entry["group_key"]))
        if existing is None:
            entry["children"] = [_mikan_download_child_entry(entry)]
            grouped[str(entry["group_key"])] = entry
        else:
            _merge_mikan_download_entry(existing, entry)
    rows = list(grouped.values())
    _apply_active_mikan_extract_jobs(rows, _mikan_extract_jobs_summary_from_state_db(config))
    counts = _mikan_download_counts(rows)
    completed_rows = sorted(
        (row for row in rows if row.get("status") == "completed"),
        key=lambda row: -float(row.get("last_extracted_at") or row.get("completed_at") or row.get("updated_at") or 0),
    )
    recent_completed = [
        {
            "job_key": f"download:{row.get('key') or index}",
            "status": "success",
            "attempts": 1,
            "torrent_name": str(row.get("title") or row.get("last_qbit_name") or row.get("key") or ""),
            "finished_at": float(row.get("last_extracted_at") or row.get("completed_at") or row.get("updated_at") or 0),
            "updated_at": float(row.get("updated_at") or 0),
        }
        for index, row in enumerate(completed_rows[:8])
    ]
    extract_jobs = _mikan_extract_jobs_empty()
    extract_jobs["counts"] = {"success": int(counts.get("completed") or 0)}
    extract_jobs["active"] = int(counts.get("extracting_subtitles") or 0) + int(counts.get("completed_waiting_extract") or 0)
    extract_jobs["recent_completed"] = recent_completed
    return {
        "exists": True,
        "total": len(rows),
        "counts": counts,
        "stalled": 0,
        "zero_speed_downloading": 0,
        "extract_jobs": extract_jobs,
        "pipeline": _mikan_pipeline_counts(counts, extract_jobs["counts"]),
    }


def _mikan_download_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _apply_active_mikan_extract_jobs(rows: list[dict[str, Any]], extract_jobs: dict[str, Any]) -> None:
    active_jobs = [
        job
        for job in extract_jobs.get("recent", [])
        if isinstance(job, dict) and str(job.get("status") or "").lower() in {"queued", "running"}
    ]
    if not active_jobs:
        return

    for row in rows:
        matching_job = next((job for job in active_jobs if _mikan_download_row_matches_extract_job(row, job)), None)
        children = row.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    child_job = next((job for job in active_jobs if _mikan_download_row_matches_extract_job(child, job)), None)
                    if child_job is not None:
                        _apply_mikan_extract_job_status(child, child_job)
        if matching_job is not None:
            _apply_mikan_extract_job_status(row, matching_job)


def _apply_mikan_extract_job_status(row: dict[str, Any], job: dict[str, Any]) -> None:
    job_status = str(job.get("status") or "").lower()
    if job_status == "running":
        row["status"] = "extracting_subtitles"
        row["next_action"] = "extracting_subtitles"
    elif job_status == "queued":
        row["status"] = "completed_waiting_extract"
        row["next_action"] = "extract_subtitles"
    else:
        return
    row["extract_job_status"] = job_status
    row["extract_job_attempts"] = int(job.get("attempts") or 0)
    row["extract_job_started_at"] = float(job.get("started_at") or 0)
    row["extract_job_updated_at"] = float(job.get("updated_at") or 0)
    row["torrent_created_at"] = float(job.get("torrent_created_at") or row.get("torrent_created_at") or 0)
    row["torrent_added_at"] = float(job.get("torrent_added_at") or row.get("torrent_added_at") or 0)
    row["torrent_completed_at"] = float(
        job.get("torrent_completed_at") or row.get("torrent_completed_at") or 0
    )
    row["extract_file_path"] = str(job.get("current_file_path") or "")
    row["extract_file_timestamp"] = float(job.get("current_file_timestamp") or 0)
    row["extract_file_time_kind"] = str(job.get("current_file_time_kind") or "")
    row["extract_file_size"] = int(job.get("current_file_size") or 0)
    if job.get("torrent_hash") and not row.get("last_qbit_hash"):
        row["last_qbit_hash"] = str(job.get("torrent_hash") or "")
    if job.get("torrent_name") and not row.get("last_qbit_name"):
        row["last_qbit_name"] = str(job.get("torrent_name") or "")
    row["updated_at"] = max(
        float(row.get("updated_at") or 0),
        float(job.get("updated_at") or 0),
        float(job.get("started_at") or 0),
    )
    row["sort_priority"] = _mikan_download_status_priority(str(row.get("status") or ""))


def _mikan_download_row_matches_extract_job(row: dict[str, Any], job: dict[str, Any]) -> bool:
    job_hash = str(job.get("torrent_hash") or "").strip().lower()
    if job_hash:
        if job_hash in _mikan_download_row_hashes(row):
            return True

    job_name = str(job.get("torrent_name") or "").strip().casefold()
    if not job_name:
        return False
    job_episodes = _mikan_extract_job_episode_set(job)
    row_episodes = _mikan_download_row_episode_set(row)
    if job_episodes and row_episodes and job_episodes.isdisjoint(row_episodes):
        return False
    row_names = {
        str(value or "").strip().casefold()
        for value in (row.get("title"), row.get("last_qbit_name"), row.get("key"))
        if str(value or "").strip()
    }
    return any(name == job_name or name in job_name or job_name in name for name in row_names)


def _mikan_download_row_hashes(row: dict[str, Any]) -> set[str]:
    hashes: set[str] = set()
    for value in (row.get("last_qbit_hash"),):
        text = str(value or "").strip().lower()
        if text:
            hashes.add(text)
    torrent_url = str(row.get("torrent_url") or "").strip()
    if torrent_url.lower().startswith("qbit://"):
        qbit_hash = torrent_url.split("://", 1)[1].strip().lower()
        if qbit_hash:
            hashes.add(qbit_hash)
    context = row.get("last_extract_context")
    if isinstance(context, dict):
        qbit_hash = str(context.get("qbit_hash") or "").strip().lower()
        if qbit_hash:
            hashes.add(qbit_hash)
    return hashes


def _mikan_extract_job_episode_set(job: dict[str, Any]) -> set[int]:
    result: set[int] = set()
    episodes = job.get("episodes")
    if isinstance(episodes, (list, tuple, set)):
        for value in episodes:
            coerced = _coerce_int(value)
            if coerced is not None:
                result.add(coerced)
    return result


def _mikan_download_row_episode_set(row: dict[str, Any]) -> set[int]:
    result: set[int] = set()
    episodes = row.get("episodes")
    if isinstance(episodes, (list, tuple, set)):
        for value in episodes:
            coerced = _coerce_int(value)
            if coerced is not None:
                result.add(coerced)
    episode = _coerce_int(row.get("episode"))
    if episode is not None:
        result.add(episode)
    return result


def _filter_mikan_download_rows(
    rows: list[dict[str, Any]],
    *,
    status_filter: str,
    search: str,
) -> list[dict[str, Any]]:
    keyword = search.casefold()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if status_filter and str(row.get("status") or "") != status_filter:
            continue
        if keyword:
            children = row.get("children") if isinstance(row.get("children"), list) else []
            searchable = " ".join(
                str(value or "")
                for value in (
                    row.get("title"),
                    row.get("key"),
                    row.get("last_qbit_name"),
                    row.get("last_extract_failure_detail"),
                    row.get("last_failure_reason"),
                    row.get("source"),
                    *(child.get("key") for child in children if isinstance(child, dict)),
                    *(child.get("source") for child in children if isinstance(child, dict)),
                )
            ).casefold()
            if keyword not in searchable:
                continue
        filtered.append(row)
    return filtered


def _mikan_sqlite_completed_aware_status_expr(columns: set[str]) -> str:
    completed_terms: list[str] = []
    if "completed_at" in columns:
        completed_terms.append("COALESCE(completed_at, 0) > 0")
    if "last_extracted_count" in columns:
        completed_terms.append("COALESCE(last_extracted_count, 0) > 0")
    if "total_extracted_count" in columns:
        completed_terms.append("COALESCE(total_extracted_count, 0) > 0")
    completed_status = " OR ".join(completed_terms)
    if not completed_status:
        return "status"
    return f"CASE WHEN {completed_status} THEN 'completed' ELSE status END"


def _mikan_sqlite_download_groups_cte(
    columns: set[str],
    *,
    has_extract_jobs: bool,
    include_search_groups: bool = False,
) -> str:
    last_qbit_hash = "COALESCE(last_qbit_hash, '')" if "last_qbit_hash" in columns else "''"
    last_qbit_name = "COALESCE(last_qbit_name, '')" if "last_qbit_name" in columns else "''"
    stored_status = _mikan_sqlite_completed_aware_status_expr(columns)
    active_status = stored_status
    if has_extract_jobs:
        active_status = f"""
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM mikan_extract_jobs job
                    WHERE job.status = 'running'
                      AND (
                          (
                              COALESCE(job.torrent_hash, '') <> ''
                              AND (
                                  lower({last_qbit_hash}) = lower(job.torrent_hash)
                                  OR lower(COALESCE(torrent_url, '')) = 'qbit://' || lower(job.torrent_hash)
                              )
                          )
                          OR (
                              COALESCE(job.torrent_name, '') <> ''
                              AND (
                                  lower(COALESCE(title, '')) = lower(job.torrent_name)
                                  OR lower({last_qbit_name}) = lower(job.torrent_name)
                              )
                          )
                      )
                ) THEN 'extracting_subtitles'
                WHEN EXISTS (
                    SELECT 1
                    FROM mikan_extract_jobs job
                    WHERE job.status = 'queued'
                      AND (
                          (
                              COALESCE(job.torrent_hash, '') <> ''
                              AND (
                                  lower({last_qbit_hash}) = lower(job.torrent_hash)
                                  OR lower(COALESCE(torrent_url, '')) = 'qbit://' || lower(job.torrent_hash)
                              )
                          )
                          OR (
                              COALESCE(job.torrent_name, '') <> ''
                              AND (
                                  lower(COALESCE(title, '')) = lower(job.torrent_name)
                                  OR lower({last_qbit_name}) = lower(job.torrent_name)
                              )
                          )
                        )
                ) THEN 'completed_waiting_extract'
                ELSE {stored_status}
            END
        """
    search_groups_cte = ""
    if include_search_groups:
        search_groups_cte = """
        matching_groups AS (
            SELECT DISTINCT
                CASE
                    WHEN COALESCE(torrent_url, '') <> '' THEN torrent_url
                    ELSE COALESCE(CAST(bangumi_id AS TEXT), '') || ':' || COALESCE(CAST(episode AS TEXT), key) || ':' || status
                END AS group_key
            FROM mikan_download_items
            WHERE key LIKE ? COLLATE NOCASE
               OR title LIKE ? COLLATE NOCASE
               OR torrent_url LIKE ? COLLATE NOCASE
               OR raw_json LIKE ? COLLATE NOCASE
        ),
        """
    return f"""
        WITH
{search_groups_cte.rstrip()}
        base_item AS (
            SELECT
                CASE
                    WHEN COALESCE(torrent_url, '') <> '' THEN torrent_url
                    ELSE COALESCE(CAST(bangumi_id AS TEXT), '') || ':' || COALESCE(CAST(episode AS TEXT), key) || ':' || status
                END AS group_key,
                {active_status} AS display_status,
                COALESCE(updated_at, 0) AS updated_at,
                CASE WHEN COALESCE(title, '') <> '' THEN title ELSE key END AS title_for_sort,
                COALESCE(last_extracted_count, 0) AS last_extracted_count,
                COALESCE(total_extracted_count, 0) AS total_extracted_count
            FROM mikan_download_items
        ),
        item AS (
            SELECT
                base_item.*,
                CASE display_status
                    WHEN 'extracting_subtitles' THEN 0
                    WHEN 'completed_waiting_extract' THEN 1
                    WHEN 'target_missing' THEN 2
                    WHEN 'downloading' THEN 3
                    WHEN 'queued' THEN 4
                    WHEN 'deferred' THEN 5
                    WHEN 'extract_failed' THEN 6
                    WHEN 'failed_candidate' THEN 7
                    WHEN 'no_candidate_retry' THEN 8
                    WHEN 'unknown' THEN 9
                    WHEN 'completed' THEN 10
                    ELSE 9
                END AS sort_priority
            FROM base_item
        ),
        ranked AS (
            SELECT
                item.*,
                ROW_NUMBER() OVER (
                    PARTITION BY group_key
                    ORDER BY sort_priority, updated_at DESC
                ) AS display_rank
            FROM item
        ),
        rep AS (
            SELECT
                group_key,
                MIN(sort_priority) AS sort_priority,
                MAX(updated_at) AS updated_at,
                MIN(title_for_sort) AS sort_title,
                MAX(last_extracted_count) AS last_extracted_count,
                MAX(total_extracted_count) AS total_extracted_count,
                MAX(CASE WHEN display_rank = 1 THEN display_status ELSE NULL END) AS status
            FROM ranked
            GROUP BY group_key
        )
    """


def _mikan_sqlite_download_rows(conn: sqlite3.Connection, page_keys: list[str]) -> list[dict[str, Any]]:
    if not page_keys:
        return []
    placeholders = ",".join("?" for _ in page_keys)
    page_order = {group_key: index for index, group_key in enumerate(page_keys)}
    grouped: dict[str, dict[str, Any]] = {}
    now = time.time()
    for group_key, key, raw_json in conn.execute(
        f"""
        WITH item AS (
            SELECT
                key,
                raw_json,
                CASE
                    WHEN COALESCE(torrent_url, '') <> '' THEN torrent_url
                    ELSE COALESCE(CAST(bangumi_id AS TEXT), '') || ':' || COALESCE(CAST(episode AS TEXT), key) || ':' || status
                END AS group_key
            FROM mikan_download_items
        )
        SELECT group_key, key, raw_json
        FROM item
        WHERE group_key IN ({placeholders})
        """,
        page_keys,
    ).fetchall():
        entry = json.loads(str(raw_json or "{}"))
        if not isinstance(entry, dict):
            continue
        row = _mikan_download_entry(str(key), entry, now)
        row["group_key"] = str(group_key)
        existing = grouped.get(str(group_key))
        if existing is None:
            row["children"] = [_mikan_download_child_entry(row)]
            grouped[str(group_key)] = row
            continue
        _merge_mikan_download_entry(existing, row)

    rows: list[dict[str, Any]] = []
    for group_key, row in sorted(grouped.items(), key=lambda item: page_order.get(item[0], 999999)):
        row["children"] = sorted(row.get("children", []), key=_mikan_download_child_sort_key)
        rows.append(row)
    return rows


def _mikan_active_extract_download_counts(active_jobs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in active_jobs:
        status = str(job.get("status") or "")
        if status == "running":
            counts["extracting_subtitles"] = counts.get("extracting_subtitles", 0) + 1
        elif status == "queued":
            counts["completed_waiting_extract"] = counts.get("completed_waiting_extract", 0) + 1
    return counts


def _mikan_pipeline_counts(
    download_counts: dict[str, Any] | None,
    extract_counts: dict[str, Any] | None,
) -> dict[str, int]:
    """Return mutually meaningful dashboard counters.

    Download rows and extract jobs describe the same work from different tables.
    Taking their sum made one active extraction appear as both "waiting" and
    "extracting", while the cumulative ``replaced`` count looked like current
    failures.  The pipeline contract keeps active stages separate and excludes
    replacement history from current workload.
    """

    downloads = download_counts if isinstance(download_counts, dict) else {}
    extracts = extract_counts if isinstance(extract_counts, dict) else {}

    def count(mapping: dict[str, Any], key: str) -> int:
        try:
            return max(0, int(mapping.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    download_extracting = count(downloads, "extracting_subtitles")
    running_extracts = count(extracts, "running")
    extracting = max(download_extracting, running_extracts)
    unrepresented_running = max(0, running_extracts - download_extracting)
    waiting_extract = max(
        count(extracts, "queued"),
        max(0, count(downloads, "completed_waiting_extract") - unrepresented_running),
    )
    auto_replacing = max(count(downloads, "extract_failed"), count(extracts, "failed"))
    imported = max(count(downloads, "completed"), count(extracts, "success"))
    needs_attention = count(downloads, "target_missing") + count(extracts, "terminal_failed")
    return {
        "queued_downloads": count(downloads, "queued") + count(downloads, "deferred"),
        "downloading": count(downloads, "downloading"),
        "extracting": extracting,
        "waiting_extract": waiting_extract,
        "candidate_retry": count(downloads, "no_candidate_retry"),
        "auto_replacing": auto_replacing,
        "needs_attention": needs_attention,
        "imported": imported,
    }


def _mikan_active_extract_overlays(
    conn: sqlite3.Connection,
    columns: set[str],
    active_jobs: list[dict[str, Any]],
) -> dict[str, str]:
    required_columns = {"key", "status", "title", "torrent_url", "bangumi_id", "episode", "updated_at"}
    if not active_jobs or not required_columns.issubset(columns):
        return {}
    overlays: dict[str, str] = {}
    for job_status, display_status in (
        ("queued", "completed_waiting_extract"),
        ("running", "extracting_subtitles"),
    ):
        for group_key in _mikan_sqlite_group_keys_for_active_extract_jobs(
            conn,
            columns,
            active_jobs,
            job_status,
        ):
            # A running job wins if stale state briefly exposes both statuses.
            if display_status == "extracting_subtitles" or group_key not in overlays:
                overlays[group_key] = display_status
    return overlays


def _mikan_counts_with_active_extract_jobs(
    conn: sqlite3.Connection,
    columns: set[str],
    counts: dict[str, int],
    active_jobs: list[dict[str, Any]],
) -> dict[str, int]:
    overlays = _mikan_active_extract_overlays(conn, columns, active_jobs)
    if not overlays:
        return dict(counts)

    group_keys = list(overlays)
    placeholders = ",".join("?" for _ in group_keys)
    grouped_cte = _mikan_sqlite_download_groups_cte(
        columns,
        has_extract_jobs=False,
        include_search_groups=False,
    )
    persisted_statuses = {
        str(group_key): str(status or "unknown")
        for group_key, status in conn.execute(
            f"{grouped_cte} SELECT group_key, status FROM rep WHERE group_key IN ({placeholders})",
            group_keys,
        ).fetchall()
    }

    normalized = {str(key): max(0, int(value or 0)) for key, value in counts.items()}
    for group_key, display_status in overlays.items():
        persisted_status = persisted_statuses.get(group_key)
        if not persisted_status or persisted_status == display_status:
            continue
        normalized[persisted_status] = max(0, normalized.get(persisted_status, 0) - 1)
        normalized[display_status] = normalized.get(display_status, 0) + 1
    return {key: value for key, value in normalized.items() if value > 0}


def _mikan_sqlite_group_keys_for_active_extract_jobs(
    conn: sqlite3.Connection,
    columns: set[str],
    active_jobs: list[dict[str, Any]],
    desired_job_status: str,
) -> list[str]:
    filtered_jobs = [
        job
        for job in active_jobs
        if str(job.get("status") or "") == desired_job_status
    ]
    hashes = [
        str(job.get("torrent_hash") or "").casefold()
        for job in filtered_jobs
        if str(job.get("torrent_hash") or "")
    ]
    hashes = [value for value in dict.fromkeys(hashes) if value]
    names = [
        str(job.get("torrent_name") or "").casefold()
        for job in filtered_jobs
        if str(job.get("torrent_name") or "").strip()
    ]
    names = [value for value in dict.fromkeys(names) if value]
    if not hashes and not names:
        return []
    conditions: list[str] = []
    params: list[Any] = []
    if hashes:
        hash_placeholders = ",".join("?" for _ in hashes)
        qbit_urls = [f"qbit://{value}" for value in hashes]
        url_placeholders = ",".join("?" for _ in qbit_urls)
        conditions.append(f"lower(COALESCE(torrent_url, '')) IN ({url_placeholders})")
        params.extend(qbit_urls)
    if hashes and "last_qbit_hash" in columns:
        hash_placeholders = ",".join("?" for _ in hashes)
        conditions.append(f"lower(COALESCE(last_qbit_hash, '')) IN ({hash_placeholders})")
        params.extend(hashes)
    if names:
        name_placeholders = ",".join("?" for _ in names)
        conditions.append(f"lower(COALESCE(title, '')) IN ({name_placeholders})")
        params.extend(names)
        if "last_qbit_name" in columns:
            conditions.append(f"lower(COALESCE(last_qbit_name, '')) IN ({name_placeholders})")
            params.extend(names)
    rows = conn.execute(
        f"""
        SELECT DISTINCT
            CASE
                WHEN COALESCE(torrent_url, '') <> '' THEN torrent_url
                ELSE COALESCE(CAST(bangumi_id AS TEXT), '') || ':' || COALESCE(CAST(episode AS TEXT), key) || ':' || status
            END AS group_key
        FROM mikan_download_items
        WHERE {' OR '.join(conditions)}
        ORDER BY updated_at DESC
        """,
        params,
    ).fetchall()
    return [str(row[0]) for row in rows]


def _mikan_pending_path(config: dict[str, Any]) -> Path:
    raw_path = _expand_config_env(str(config.get("mikan_pending_path") or "mikan_pending.json")).strip() or "mikan_pending.json"
    path = Path(raw_path)
    if not path.is_absolute():
        path = WORK_PATH / path
    return path


def _mikan_state_db_path(config: dict[str, Any]) -> Path:
    return _mikan_pending_path(config).with_name("mikan_state.sqlite3")


def _mikan_extract_latency_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Return a compact completion-to-first-claim latency SLO summary."""

    db_path = _mikan_state_db_path(config)
    cache_key = str(db_path)
    cached = _ttl_cache_get(_MIKAN_EXTRACT_LATENCY_CACHE, cache_key, SUMMARY_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    empty = {
        "sample_count": 0,
        "p50_seconds": None,
        "p95_seconds": None,
        "target_seconds": 15,
        "meets_target": None,
    }
    if not db_path.is_file():
        return _ttl_cache_set(_MIKAN_EXTRACT_LATENCY_CACHE, cache_key, empty)
    cutoff = time.time() - 30 * 86400
    try:
        with _sqlite_connect(db_path, readonly=True) as connection:
            if not _sqlite_table_exists(connection, "mikan_extract_jobs"):
                return _ttl_cache_set(_MIKAN_EXTRACT_LATENCY_CACHE, cache_key, empty)
            rows = connection.execute(
                """
                SELECT started_at, torrent_json
                FROM mikan_extract_jobs
                WHERE started_at >= ?
                ORDER BY started_at DESC
                LIMIT 500
                """,
                (cutoff,),
            ).fetchall()
    except sqlite3.Error:
        return _ttl_cache_set(_MIKAN_EXTRACT_LATENCY_CACHE, cache_key, empty)
    latencies: list[float] = []
    for started_at, torrent_json in rows:
        payload = _json_object(torrent_json)
        try:
            started = float(started_at or 0)
            completed = float(payload.get("completion_on") or 0)
        except (TypeError, ValueError):
            continue
        if completed <= 0 or started < completed:
            continue
        latency = started - completed
        if latency <= 7 * 86400:
            latencies.append(latency)
    if not latencies:
        return _ttl_cache_set(_MIKAN_EXTRACT_LATENCY_CACHE, cache_key, empty)
    ordered = sorted(latencies)

    def percentile(fraction: float) -> float:
        index = max(0, min(len(ordered) - 1, int((len(ordered) * fraction + 0.999999)) - 1))
        return round(ordered[index], 3)

    p95 = percentile(0.95)
    return _ttl_cache_set(
        _MIKAN_EXTRACT_LATENCY_CACHE,
        cache_key,
        {
            "sample_count": len(ordered),
            "p50_seconds": percentile(0.50),
            "p95_seconds": p95,
            "target_seconds": 15,
            "meets_target": p95 <= 15,
        },
    )


def _encode_cursor(offset: int) -> str:
    payload = json.dumps({"offset": max(0, int(offset))}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = str(cursor) + "=" * (-len(str(cursor)) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        offset = int(payload.get("offset", 0)) if isinstance(payload, dict) else -1
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    if offset < 0:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return offset


def _compact_ai_task(task: dict[str, Any]) -> dict[str, Any]:
    path = str(task.get("path") or "")
    status = str(task.get("effective_status") or task.get("raw_status") or task.get("status") or "")
    message = str(task.get("message") or "")
    stage = str(task.get("stage") or "")
    problem = task.get("problem") if isinstance(task.get("problem"), dict) else _ai_task_problem(
        status=status,
        stage=stage,
        message=message,
        retry_at=float(task.get("next_retry_at") or 0),
    )
    return {
        "task_id": stable_id("ai", path),
        "path": path,
        "file_name": str(task.get("file_name") or Path(path).name),
        "status": status,
        "stage": stage,
        "display_message": _compact_ai_display_message(status=status, stage=stage, message=message, problem=problem),
        "problem": problem,
        "progress": task.get("progress"),
        "attempts": int(task.get("attempts") or 0),
        "updated_at": float(task.get("updated_at") or 0),
        "queued_at": float(task.get("queued_at") or 0),
        "running_started_at": float(task.get("running_started_at") or 0),
        "completed_at": float(task.get("completed_at") or 0),
        "next_retry_at": float(task.get("next_retry_at") or 0),
        "force_ai": bool(task.get("force_ai")),
        "running_stale": bool(task.get("running_stale")),
        "running_orphaned": bool(task.get("running_orphaned")),
    }


def _compact_ai_display_message(
    *,
    status: str,
    stage: str,
    message: str,
    problem: dict[str, Any],
) -> str:
    batch = re.search(r"\b(?:translating|translated)?\s*batch\s+(\d+)\s*/\s*(\d+)\b", message, re.I)
    if batch:
        return f"正在翻譯字幕（{batch.group(1)} / {batch.group(2)}）"
    if re.search(r"running asr|whisper|transcrib", message, re.I):
        return "正在將日文語音轉成字幕。"
    if re.search(r"extracting audio|audio extraction", message, re.I):
        return "正在提取影片音訊。"
    if re.search(r"language detect", message, re.I):
        return "正在確認音訊語言。"
    if re.search(r"quality check", message, re.I):
        return "正在檢查字幕品質。"
    normalized = str(status or "").casefold()
    if normalized == "queued":
        return "已排入佇列，等待 Worker 處理。"
    if normalized == "running":
        return "Worker 正在處理這個字幕工作。"
    return str(problem.get("description") or "系統已保留目前工作狀態。")


def _v2_revision(snapshot: dict[str, Any] | None = None) -> str:
    payload = snapshot if snapshot is not None else _stream_state_version()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _fast_open_review_count(config: dict[str, Any]) -> int:
    database = _control_state_db_path(config)
    cache_key = str(database)
    cached = _ttl_cache_get(
        _OPEN_REVIEW_COUNT_CACHE,
        cache_key,
        SUMMARY_CACHE_TTL_SECONDS,
    )
    if cached is not None:
        return int(cached)
    _reviews, total = list_reviews(
        database,
        status="open",
        kind="",
        limit=1,
        offset=0,
    )
    return int(
        _ttl_cache_set(
            _OPEN_REVIEW_COUNT_CACHE,
            cache_key,
            int(total),
        )
    )


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Evaluate the incomplete-beta continued fraction without dependencies."""

    maximum_iterations = 512
    epsilon = 3.0e-14
    minimum = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < minimum:
        d = minimum
    d = 1.0 / d
    result = d
    for iteration in range(1, maximum_iterations + 1):
        doubled = 2 * iteration
        coefficient = (
            iteration
            * (b - iteration)
            * x
            / ((qam + doubled) * (a + doubled))
        )
        d = 1.0 + coefficient * d
        if abs(d) < minimum:
            d = minimum
        c = 1.0 + coefficient / c
        if abs(c) < minimum:
            c = minimum
        d = 1.0 / d
        result *= d * c

        coefficient = -(
            (a + iteration)
            * (qab + iteration)
            * x
            / ((a + doubled) * (qap + doubled))
        )
        d = 1.0 + coefficient * d
        if abs(d) < minimum:
            d = minimum
        c = 1.0 + coefficient / c
        if abs(c) < minimum:
            c = minimum
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) <= epsilon:
            return result
    raise ArithmeticError("incomplete beta continued fraction did not converge")


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Return I_x(a, b), using log space for the potentially tiny prefactor."""

    if a <= 0.0 or b <= 0.0:
        raise ValueError("beta shape parameters must be positive")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_prefactor = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    prefactor = math.exp(log_prefactor) if log_prefactor > -745.0 else 0.0
    if x < (a + 1.0) / (a + b + 2.0):
        value = prefactor * _beta_continued_fraction(a, b, x) / a
    else:
        value = 1.0 - prefactor * _beta_continued_fraction(b, a, 1.0 - x) / b
    return min(1.0, max(0.0, value))


def _clopper_pearson_lower_bound(
    successes: int,
    denominator: int,
    *,
    confidence_level: float = AI_DELIVERY_SLO_CONFIDENCE_LEVEL,
) -> float | None:
    """Return the exact one-sided Clopper-Pearson success-probability bound."""

    if denominator < 0 or successes < 0 or successes > denominator:
        raise ValueError("successes must be between zero and denominator")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence level must be between zero and one")
    if denominator == 0:
        return None
    if successes == 0:
        return 0.0
    alpha = 1.0 - confidence_level
    if successes == denominator:
        # Beta(alpha; n, 1) has the exact closed form alpha ** (1 / n).
        return math.exp(math.log(alpha) / denominator)

    beta_a = float(successes)
    beta_b = float(denominator - successes + 1)
    low = 0.0
    high = successes / denominator
    for _iteration in range(80):
        midpoint = (low + high) / 2.0
        if midpoint == low or midpoint == high:
            break
        probability = _regularized_incomplete_beta(midpoint, beta_a, beta_b)
        if not math.isfinite(probability):
            raise ArithmeticError("incomplete beta returned a non-finite result")
        if probability < alpha:
            low = midpoint
        else:
            high = midpoint
    # The lower bracket is conservative by at most one floating-point step.
    return low


def _minimum_zero_miss_sample(
    target: float,
    *,
    confidence_level: float = AI_DELIVERY_SLO_CONFIDENCE_LEVEL,
) -> int:
    """Smallest all-success sample whose one-sided exact lower bound meets target."""

    if not 0.0 < target < 1.0:
        raise ValueError("target must be between zero and one")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence level must be between zero and one")
    log_alpha = math.log1p(-confidence_level)
    log_target = math.log(target)
    sample = max(1, math.ceil(log_alpha / log_target))
    while sample > 1 and log_alpha / (sample - 1) >= log_target:
        sample -= 1
    while log_alpha / sample < log_target:
        sample += 1
    return sample


AI_DELIVERY_SLO_MINIMUM_ZERO_MISS_SAMPLE = _minimum_zero_miss_sample(
    AI_DELIVERY_SLO_TARGET
)


def _ai_delivery_anytime_log_e(theta: float, successes: int, misses: int) -> float:
    """Log e-value for a fixed, non-Bayesian two-strategy betting portfolio."""

    probability = float(theta)
    success_count = int(successes)
    miss_count = int(misses)
    if not 0.0 < probability < 1.0:
        raise ValueError("theta must be strictly between zero and one")
    if success_count < 0 or miss_count < 0:
        raise ValueError("successes and misses must be non-negative")
    terms = []
    for fraction in AI_DELIVERY_ANYTIME_BETTING_FRACTIONS:
        terms.append(
            math.log(0.5)
            + success_count
            * math.log1p(fraction * ((1.0 - probability) / probability))
            + miss_count * math.log1p(-fraction)
        )
    pivot = max(terms)
    value = pivot + math.log(sum(math.exp(term - pivot) for term in terms))
    if not math.isfinite(value):
        raise ArithmeticError("anytime e-process returned a non-finite log value")
    return value


def _ai_delivery_anytime_lower_bound(successes: int, misses: int) -> float | None:
    """Conservative one-sided 95% lower confidence sequence endpoint."""

    success_count = int(successes)
    miss_count = int(misses)
    if success_count < 0 or miss_count < 0:
        raise ValueError("successes and misses must be non-negative")
    if success_count + miss_count == 0:
        return None
    if success_count == 0:
        return 0.0
    rejecting = 0.0
    accepting = 1.0
    for _iteration in range(80):
        midpoint = (rejecting + accepting) / 2.0
        if midpoint == rejecting or midpoint == accepting:
            break
        if _ai_delivery_anytime_log_e(midpoint, success_count, miss_count) >= AI_DELIVERY_ANYTIME_LOG_THRESHOLD:
            rejecting = midpoint
        else:
            accepting = midpoint
    return rejecting


def _ai_delivery_publication_semantics(
    verification_json: Any,
    *,
    expected_policy_revision: str,
) -> dict[str, Any] | None:
    """Mirror the Worker's fail-closed strict publication evidence contract."""

    try:
        verification = (
            json.loads(verification_json)
            if isinstance(verification_json, str)
            else verification_json
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(verification, dict):
        return None
    if verification.get("publication_semantics_verified") is not True:
        return None
    if str(verification.get("publication_contract") or "") != AI_DELIVERY_PUBLICATION_CONTRACT:
        return None
    policy_revision = str(expected_policy_revision or "").strip()
    if not policy_revision:
        return None
    if str(verification.get("expected_policy_revision") or "") != policy_revision:
        return None
    if str(verification.get("manifest_policy_revision") or "") != policy_revision:
        return None
    if verification.get("policy_revision_matched") is not True:
        return None
    kind = str(verification.get("publication_kind") or "").strip()
    languages = verification.get("output_languages")
    if not isinstance(languages, list) or not languages:
        return None
    if not all(
        isinstance(language, str) and language.strip() == language
        for language in languages
    ):
        return None
    normalized_languages = tuple(languages)
    if kind == "translated_trilingual":
        if normalized_languages != AI_DELIVERY_TRANSLATED_LANGUAGES:
            return None
    elif kind in {"adopted_zh_tw", "converted_zh_cn"}:
        if normalized_languages != AI_DELIVERY_TRADITIONAL_CHINESE_LANGUAGES:
            return None
    else:
        # source_language can be a useful intermediate artifact, but the SLO
        # proves usable Traditional Chinese delivery and must count it as a
        # miss rather than a success.
        return None
    return {
        "contract": AI_DELIVERY_PUBLICATION_CONTRACT,
        "kind": kind,
        "output_languages": list(normalized_languages),
    }


def _ai_active_queue_coverage(
    connection: sqlite3.Connection,
    *,
    policy_revision: str,
) -> dict[str, Any]:
    """Explain every active queue row that lacks an exact open obligation."""

    rows = connection.execute(
        """
        WITH facts AS (
            SELECT q.path, q.status,
                   MAX(CASE WHEN o.obligation_id IS NOT NULL THEN 1 ELSE 0 END) AS has_path,
                   MAX(CASE WHEN o.media_mtime_ns=q.mtime_ns THEN 1 ELSE 0 END) AS has_media,
                   MAX(CASE WHEN o.media_mtime_ns=q.mtime_ns
                                  AND o.policy_revision=? THEN 1 ELSE 0 END) AS has_policy,
                   MAX(CASE WHEN o.media_mtime_ns=q.mtime_ns
                                  AND o.policy_revision=?
                                  AND o.state='open' THEN 1 ELSE 0 END) AS tracked
            FROM ai_candidate_queue AS q
            LEFT JOIN ai_delivery_obligations AS o ON o.canonical_path=q.path
            WHERE q.status IN ('queued', 'running', 'failed_retry', 'paused')
            GROUP BY q.path, q.status, q.mtime_ns
        ), classified AS (
            SELECT status,
                   CASE
                     WHEN tracked=1 THEN 'tracked'
                     WHEN has_path=0 THEN 'missing_obligation'
                     WHEN has_media=0 THEN 'media_revision_mismatch'
                     WHEN has_policy=0 THEN 'policy_revision_mismatch'
                     ELSE 'matching_obligation_not_open'
                   END AS reason
            FROM facts
        )
        SELECT status, reason, COUNT(*)
        FROM classified
        GROUP BY status, reason
        ORDER BY status COLLATE NOCASE, reason COLLATE NOCASE
        """,
        (policy_revision, policy_revision),
    ).fetchall()
    total = 0
    tracked = 0
    by_reason: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for status, reason, raw_count in rows:
        count = max(0, int(raw_count or 0))
        total += count
        if str(reason) == "tracked":
            tracked += count
            continue
        reason_key = str(reason)
        status_key = str(status)
        by_reason[reason_key] = by_reason.get(reason_key, 0) + count
        by_status[status_key] = by_status.get(status_key, 0) + count
    return {
        "total": total,
        "tracked": tracked,
        "untracked": total - tracked,
        "untracked_breakdown": {
            "by_reason": dict(sorted(by_reason.items())),
            "by_status": dict(sorted(by_status.items())),
        },
    }


def _ai_inventory_coverage(
    connection: sqlite3.Connection,
    *,
    meta: dict[str, Any],
    instrumented_at: float,
    observed_at: float,
) -> dict[str, Any] | None:
    """Validate the latest durable full-root epoch without touching the NAS."""

    if not _sqlite_table_exists(connection, "ai_inventory_epochs"):
        return None
    if not _sqlite_table_exists(connection, "ai_media_inventory"):
        return None
    epoch_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(ai_inventory_epochs)").fetchall()
    }
    inventory_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(ai_media_inventory)").fetchall()
    }
    required_epoch_columns = {
        "epoch_id", "schema_version", "measurement_revision", "policy_revision",
        "root_signature", "state", "started_at", "updated_at", "completed_at",
        "walk_error_count", "observed_count", "classified_count",
        "delivery_required_count", "tracked_count", "untracked_count",
        "legacy_preinstrumented_ai_count", "coverage_complete", "dirty_generation",
    }
    required_inventory_columns = {
        "epoch_id", "canonical_path", "media_fingerprint", "media_size",
        "media_mtime_ns", "policy_revision", "classification", "disposition",
        "requires_ledger", "obligation_id",
    }
    if not required_epoch_columns.issubset(epoch_columns):
        return None
    if not required_inventory_columns.issubset(inventory_columns):
        return None
    if str(meta.get("inventory_schema_version") or "") != str(AI_INVENTORY_SCHEMA_VERSION):
        return None
    policy_revision = str(meta.get("inventory_current_policy_revision") or "").strip()
    root_signature = str(meta.get("inventory_current_root_signature") or "").strip()
    if not policy_revision or not root_signature:
        return None
    base = {
        "available": True,
        "state": "inventory_missing",
        "reason": "epoch_not_started",
        "epoch_id": None,
        "completed_at": 0.0,
        "age_seconds": None,
        "total": None,
        "delivery_required": None,
        "tracked": None,
        "untracked": None,
        "legacy_grandfathered": None,
        "complete": False,
        "continuous_coverage_since": None,
        "coverage_complete_through": None,
        "coverage_chain_epoch_count": 0,
        "coverage_max_gap_seconds": AI_INVENTORY_MAX_AGE_SECONDS,
    }
    completed = connection.execute(
        """
        SELECT epoch_id, completed_at, observed_count, classified_count,
               delivery_required_count, tracked_count, untracked_count,
               legacy_preinstrumented_ai_count, coverage_complete, dirty_generation
        FROM ai_inventory_epochs
        WHERE state='completed' AND schema_version=? AND measurement_revision=?
          AND policy_revision=? AND root_signature=? AND completed_at>=?
        ORDER BY completed_at DESC LIMIT 1
        """,
        (
            AI_INVENTORY_SCHEMA_VERSION,
            AI_DELIVERY_MEASUREMENT_REVISION,
            policy_revision,
            root_signature,
            instrumented_at,
        ),
    ).fetchone()
    if completed is None:
        latest_scope_epoch = connection.execute(
            """
            SELECT epoch_id, state, started_at, updated_at, completed_at
            FROM ai_inventory_epochs
            WHERE schema_version=? AND measurement_revision=?
              AND policy_revision=? AND root_signature=?
            ORDER BY started_at DESC, epoch_id DESC LIMIT 1
            """,
            (
                AI_INVENTORY_SCHEMA_VERSION,
                AI_DELIVERY_MEASUREMENT_REVISION,
                policy_revision,
                root_signature,
            ),
        ).fetchone()
        if latest_scope_epoch is None:
            any_epoch = connection.execute(
                "SELECT 1 FROM ai_inventory_epochs LIMIT 1"
            ).fetchone()
            return {
                **base,
                "reason": (
                    "current_scope_epoch_missing"
                    if any_epoch is not None
                    else "epoch_not_started"
                ),
            }
        epoch_id = str(latest_scope_epoch[0])
        epoch_state = str(latest_scope_epoch[1] or "").strip().casefold()
        updated_at = float(latest_scope_epoch[3] or 0)
        age_seconds = observed_at - updated_at if updated_at > 0 else None
        # ``observed_at`` is captured before the read-only SQLite query.  The
        # Worker can commit a fresh heartbeat while this request is reading,
        # making the fetched timestamp fractionally newer than that snapshot.
        # Clamp only this bounded race; materially future timestamps remain
        # fail-closed below.
        if (
            age_seconds is not None
            and -AI_INVENTORY_CLOCK_SKEW_TOLERANCE_SECONDS <= age_seconds < 0
        ):
            age_seconds = 0.0
        if epoch_state == "running":
            running_stale = bool(
                age_seconds is None
                or age_seconds < 0
                or age_seconds > AI_INVENTORY_RUNNING_STALE_SECONDS
            )
            return {
                **base,
                "state": (
                    "inventory_running_stale"
                    if running_stale
                    else "inventory_running"
                ),
                "reason": (
                    "matching_epoch_running_stale"
                    if running_stale
                    else "matching_epoch_running"
                ),
                "epoch_id": epoch_id,
                "age_seconds": age_seconds,
            }
        if epoch_state in {"failed", "abandoned"}:
            return {
                **base,
                "state": f"inventory_{epoch_state}",
                "reason": f"matching_epoch_{epoch_state}",
                "epoch_id": epoch_id,
                "age_seconds": age_seconds,
            }
        return {
            **base,
            "reason": "matching_completed_epoch_predates_instrumentation",
            "epoch_id": epoch_id,
            "completed_at": float(latest_scope_epoch[4] or 0),
            "age_seconds": age_seconds,
        }
    epoch_id = str(completed[0])
    completed_at = float(completed[1] or 0)
    age_seconds = observed_at - completed_at
    result = {
        **base,
        "epoch_id": epoch_id,
        "completed_at": completed_at,
        "age_seconds": age_seconds,
    }
    try:
        dirty_at = float(meta.get("inventory_dirty_at") or 0)
        dirty_generation = int(meta.get("inventory_dirty_generation") or 0)
    except (TypeError, ValueError):
        return {**result, "state": "inventory_dirty", "reason": "invalid_dirty_marker"}
    # The generation disambiguates equal wall-clock timestamps. Equality is
    # safe only when the completed epoch captured that exact generation.
    if dirty_generation != int(completed[9] or 0) or dirty_at > completed_at:
        return {**result, "state": "inventory_dirty", "reason": "newer_dirty_generation"}
    newer_bad = connection.execute(
        """
        SELECT state FROM ai_inventory_epochs
        WHERE measurement_revision=? AND policy_revision=? AND root_signature=?
          AND started_at>? AND state IN ('failed', 'abandoned')
        ORDER BY started_at DESC LIMIT 1
        """,
        (
            AI_DELIVERY_MEASUREMENT_REVISION,
            policy_revision,
            root_signature,
            completed_at,
        ),
    ).fetchone()
    if newer_bad is not None:
        return {
            **result,
            "state": f"inventory_{str(newer_bad[0])}",
            "reason": "newer_failed_or_abandoned_epoch",
        }
    stale_running = connection.execute(
        """
        SELECT 1 FROM ai_inventory_epochs
        WHERE measurement_revision=? AND policy_revision=? AND root_signature=?
          AND state='running' AND ?-updated_at>?
        LIMIT 1
        """,
        (
            AI_DELIVERY_MEASUREMENT_REVISION,
            policy_revision,
            root_signature,
            observed_at,
            AI_INVENTORY_RUNNING_STALE_SECONDS,
        ),
    ).fetchone()
    if stale_running is not None:
        return {
            **result,
            "state": "inventory_running_stale",
            "reason": "newer_running_epoch_stale",
        }
    if age_seconds < 0 or age_seconds > AI_INVENTORY_MAX_AGE_SECONDS:
        return {**result, "state": "inventory_stale", "reason": "completed_epoch_stale"}

    counts = connection.execute(
        """
        SELECT COUNT(*),
               COALESCE(SUM(CASE WHEN classification!='' THEN 1 ELSE 0 END), 0),
               COUNT(DISTINCT canonical_path),
               COALESCE(SUM(CASE WHEN requires_ledger=1 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN disposition='legacy_preinstrumented_ai' THEN 1 ELSE 0 END), 0)
        FROM ai_media_inventory WHERE epoch_id=?
        """,
        (epoch_id,),
    ).fetchone()
    total = int(counts[0] or 0)
    classified = int(counts[1] or 0)
    distinct_paths = int(counts[2] or 0)
    required = int(counts[3] or 0)
    legacy = int(counts[4] or 0)
    ledger_rows = connection.execute(
        """
        SELECT i.disposition, i.obligation_id,
               o.obligation_id, o.state, o.policy_revision,
               o.manifest_path, o.manifest_sha256, o.verification_json, o.verified_at
        FROM ai_media_inventory AS i
        LEFT JOIN ai_delivery_obligations AS o
          ON o.obligation_id=i.obligation_id
         AND o.canonical_path=i.canonical_path
         AND o.media_fingerprint=i.media_fingerprint
         AND o.media_size=i.media_size
         AND o.media_mtime_ns=i.media_mtime_ns
         AND o.policy_revision=i.policy_revision
        WHERE i.epoch_id=? AND i.requires_ledger=1
        """,
        (epoch_id,),
    ).fetchall()
    tracked = 0
    for disposition, expected_id, actual_id, state, policy, manifest, sha256, verification, verified_at in ledger_rows:
        if not actual_id or str(actual_id) != str(expected_id):
            continue
        strict_success = bool(
            str(state) == "succeeded"
            and float(verified_at or 0) > 0
            and str(manifest or "").strip()
            and re.fullmatch(r"[0-9a-f]{64}", str(sha256 or "").strip().casefold())
            and _ai_delivery_publication_semantics(
                verification,
                expected_policy_revision=str(policy or ""),
            ) is not None
        )
        if str(disposition) == "delivery_required" and (str(state) == "open" or strict_success):
            tracked += 1
        elif str(disposition) == "delivered" and strict_success:
            tracked += 1
    untracked = required - tracked
    stored_consistent = bool(
        total == int(completed[2] or 0)
        and classified == int(completed[3] or 0)
        and distinct_paths == total
        and required == int(completed[4] or 0)
        and tracked == int(completed[5] or 0)
        and untracked == int(completed[6] or 0)
        and legacy == int(completed[7] or 0)
        and bool(completed[8]) == (untracked == 0)
    )
    complete = bool(stored_consistent and untracked == 0)
    if not complete:
        return {
            **result,
            "state": "coverage_incomplete",
            "reason": "completed_epoch_ledger_incomplete",
            "total": total,
            "delivery_required": required,
            "tracked": tracked,
            "untracked": untracked,
            "legacy_grandfathered": legacy,
            "complete": False,
        }
    history = connection.execute(
        """
        SELECT epoch_id, state, started_at, updated_at, completed_at,
               walk_error_count, observed_count, classified_count,
               delivery_required_count, tracked_count, untracked_count,
               legacy_preinstrumented_ai_count, coverage_complete
        FROM ai_inventory_epochs
        WHERE measurement_revision=? AND policy_revision=? AND root_signature=?
          AND started_at>=?
        ORDER BY started_at DESC, epoch_id DESC
        """,
        (
            AI_DELIVERY_MEASUREMENT_REVISION,
            policy_revision,
            root_signature,
            instrumented_at,
        ),
    ).fetchall()
    continuous_since = None
    latest_chain_completed = None
    newer_completed = None
    chain_count = 0
    for epoch in history:
        epoch_state = str(epoch[1] or "")
        if epoch_state in {"failed", "abandoned"}:
            break
        if epoch_state != "completed":
            continue
        epoch_started = float(epoch[2] or 0)
        epoch_completed = float(epoch[4] or 0)
        history_counts = connection.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(CASE WHEN classification!='' THEN 1 ELSE 0 END), 0),
                   COUNT(DISTINCT canonical_path),
                   COALESCE(SUM(CASE WHEN requires_ledger=1 THEN 1 ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN disposition='legacy_preinstrumented_ai' THEN 1 ELSE 0 END), 0)
            FROM ai_media_inventory WHERE epoch_id=?
            """,
            (str(epoch[0]),),
        ).fetchone()
        history_total = int(history_counts[0] or 0)
        history_classified = int(history_counts[1] or 0)
        history_distinct = int(history_counts[2] or 0)
        history_required = int(history_counts[3] or 0)
        history_legacy = int(history_counts[4] or 0)
        history_ledgers = connection.execute(
            """
            SELECT i.disposition, i.obligation_id,
                   o.obligation_id, o.state, o.policy_revision,
                   o.manifest_path, o.manifest_sha256, o.verification_json, o.verified_at
            FROM ai_media_inventory AS i
            LEFT JOIN ai_delivery_obligations AS o
              ON o.obligation_id=i.obligation_id
             AND o.canonical_path=i.canonical_path
             AND o.media_fingerprint=i.media_fingerprint
             AND o.media_size=i.media_size
             AND o.media_mtime_ns=i.media_mtime_ns
             AND o.policy_revision=i.policy_revision
            WHERE i.epoch_id=? AND i.requires_ledger=1
            """,
            (str(epoch[0]),),
        ).fetchall()
        history_tracked = 0
        for disposition, expected_id, actual_id, state, policy, manifest, sha256, verification, verified_at in history_ledgers:
            if not actual_id or str(actual_id) != str(expected_id):
                continue
            try:
                verified_value = float(verified_at or 0)
            except (TypeError, ValueError):
                verified_value = 0.0
            strict_success = bool(
                str(state) == "succeeded"
                and math.isfinite(verified_value)
                and verified_value > 0
                and str(manifest or "").strip()
                and re.fullmatch(r"[0-9a-f]{64}", str(sha256 or "").strip().casefold())
                and _ai_delivery_publication_semantics(
                    verification, expected_policy_revision=str(policy or "")
                ) is not None
            )
            if str(disposition) == "delivery_required" and (str(state) == "open" or strict_success):
                history_tracked += 1
            elif str(disposition) == "delivered" and strict_success:
                history_tracked += 1
        history_untracked = history_required - history_tracked
        valid_epoch = bool(
            epoch_started > 0
            and epoch_completed >= epoch_started
            and int(epoch[5] or 0) == 0
            and history_total == int(epoch[6] or 0)
            and history_classified == int(epoch[7] or 0)
            and history_distinct == history_total
            and history_required == int(epoch[8] or 0)
            and history_tracked == int(epoch[9] or 0)
            and history_untracked == int(epoch[10] or 0) == 0
            and history_legacy == int(epoch[11] or 0)
            and bool(epoch[12])
        )
        if not valid_epoch:
            break
        if newer_completed is not None:
            if newer_completed - epoch_completed > AI_INVENTORY_MAX_AGE_SECONDS:
                break
        else:
            latest_chain_completed = epoch_completed
        continuous_since = epoch_completed
        newer_completed = epoch_completed
        chain_count += 1
    if (
        continuous_since is None
        or latest_chain_completed is None
        or abs(latest_chain_completed - completed_at) > AI_DELIVERY_DUE_TOLERANCE_SECONDS
    ):
        return {
            **result,
            "state": "coverage_incomplete",
            "reason": "continuous_epoch_chain_incomplete",
            "complete": False,
        }
    return {
        **result,
        "state": "complete",
        "reason": "complete",
        "total": total,
        "delivery_required": required,
        "tracked": tracked,
        "untracked": untracked,
        "legacy_grandfathered": legacy,
        "complete": True,
        "continuous_coverage_since": continuous_since,
        "coverage_complete_through": latest_chain_completed,
        "coverage_chain_epoch_count": chain_count,
    }


def _summarize_ai_delivery_rows(
    rows: list[sqlite3.Row | tuple[Any, ...]],
    *,
    expected_due_from: float,
    expected_due_to: float,
    policy_revision: str,
) -> dict[str, Any]:
    """Classify matured media rows from expected, never stored, deadlines."""

    names = (
        "obligation_id", "state", "eligible_at", "due_at", "verified_at",
        "terminal_at", "updated_at", "attempt_count", "exclusion_code",
        "policy_revision", "verification_json",
    )
    matured: list[tuple[dict[str, Any], float, bool]] = []
    invalid_eligible = 0
    for raw in rows:
        item = dict(zip(names, raw, strict=True))
        if str(item["policy_revision"] or "") != policy_revision:
            continue
        try:
            eligible_at = float(item["eligible_at"])
        except (TypeError, ValueError):
            invalid_eligible += 1
            continue
        if not math.isfinite(eligible_at) or eligible_at <= 0:
            invalid_eligible += 1
            continue
        expected_due = eligible_at + AI_DELIVERY_DEADLINE_SECONDS
        if not expected_due_from <= expected_due < expected_due_to:
            continue
        try:
            stored_due = float(item["due_at"])
        except (TypeError, ValueError):
            stored_due = math.nan
        due_valid = bool(
            math.isfinite(stored_due)
            and abs(stored_due - expected_due) <= AI_DELIVERY_DUE_TOLERANCE_SECONDS
        )
        matured.append((item, expected_due, due_valid))

    included: list[tuple[dict[str, Any], float, bool]] = []
    valid_exclusions: list[dict[str, Any]] = []
    invalid_contract_misses = 0
    invalid_exclusions = 0
    for item, expected_due, due_valid in matured:
        try:
            terminal_at = float(item["terminal_at"] or 0)
            updated_at = float(item["updated_at"] or 0)
        except (TypeError, ValueError):
            terminal_at = updated_at = math.nan
        try:
            attempt_count = int(item["attempt_count"] if item["attempt_count"] is not None else -1)
        except (TypeError, ValueError, OverflowError):
            attempt_count = -1
        valid_exclusion = bool(
            due_valid
            and str(item["state"]) == "excluded"
            and str(item["exclusion_code"]) in AI_DELIVERY_EXCLUSION_CODES
            and attempt_count == 0
            and math.isfinite(terminal_at)
            and math.isfinite(updated_at)
            and float(item["eligible_at"]) <= terminal_at <= updated_at <= expected_due
        )
        if valid_exclusion:
            valid_exclusions.append(item)
            continue
        if str(item["state"]) == "excluded":
            invalid_exclusions += 1
        if not due_valid:
            invalid_contract_misses += 1
        included.append((item, expected_due, due_valid))

    numerator = 0
    invalid_success_evidence = 0
    late_successes = 0
    successes: list[dict[str, Any]] = []
    for item, expected_due, due_valid in included:
        if str(item["state"]) != "succeeded":
            continue
        publication = _ai_delivery_publication_semantics(
            item["verification_json"],
            expected_policy_revision=policy_revision,
        )
        try:
            verified_at = float(item["verified_at"] or 0)
        except (TypeError, ValueError):
            verified_at = math.nan
        if not due_valid or publication is None or not math.isfinite(verified_at):
            invalid_success_evidence += 1
            continue
        if 0 < verified_at <= expected_due:
            numerator += 1
            successes.append(publication)
        elif verified_at > expected_due:
            late_successes += 1
    denominator = len(included)
    misses = denominator - numerator
    exclusions_by_code: dict[str, int] = {}
    for item in valid_exclusions:
        code = str(item["exclusion_code"])
        exclusions_by_code[code] = exclusions_by_code.get(code, 0) + 1
    traditional_by_kind = {
        kind: sum(1 for publication in successes if publication["kind"] == kind)
        for kind in sorted(AI_DELIVERY_TRADITIONAL_CHINESE_PUBLICATION_KINDS)
    }
    return {
        "numerator": numerator,
        "verified_on_time": numerator,
        "denominator": denominator,
        "misses": misses,
        "success_rate": numerator / denominator if denominator else None,
        "invalid_contract_misses": invalid_contract_misses,
        "invalid_eligible_obligations": invalid_eligible,
        "invalid_success_evidence": invalid_success_evidence,
        "late_successes": late_successes,
        "overdue_open": sum(1 for item, _due, _valid in included if str(item["state"]) == "open"),
        "invalid_exclusions": invalid_exclusions,
        "exclusions": {"total": len(valid_exclusions), "by_code": exclusions_by_code},
        "publication_breakdown": {
            "translated_chinese": {
                "publication_kinds": sorted(
                    AI_DELIVERY_TRADITIONAL_CHINESE_PUBLICATION_KINDS
                ),
                "verified_on_time": sum(traditional_by_kind.values()),
                "by_publication_kind": traditional_by_kind,
                "required_output_language": "zh-TW",
            },
            "source_language": {
                "publication_kind": "source_language",
                "verified_on_time": 0,
                "by_output_language": {},
                "counts_as_traditional_chinese_success": False,
            },
            "unclassified_misses": misses,
            "invalid_success_evidence": invalid_success_evidence,
        },
    }


def _ai_delivery_slo_summary_uncached(*, now: float | None = None) -> dict[str, Any]:
    """Return strict rolling delivery health and cumulative anytime evidence."""

    observed_at = float(time.time() if now is None else now)
    empty_rollup = {
        "numerator": 0, "verified_on_time": 0, "denominator": 0, "misses": 0,
        "success_rate": None, "invalid_contract_misses": 0,
        "invalid_eligible_obligations": 0, "invalid_success_evidence": 0,
        "late_successes": 0, "overdue_open": 0,
        "invalid_exclusions": 0, "exclusions": {"total": 0, "by_code": {}},
        "publication_breakdown": {
            "translated_chinese": {
                "publication_kinds": sorted(
                    AI_DELIVERY_TRADITIONAL_CHINESE_PUBLICATION_KINDS
                ),
                "verified_on_time": 0,
                "by_publication_kind": {
                    kind: 0
                    for kind in sorted(AI_DELIVERY_TRADITIONAL_CHINESE_PUBLICATION_KINDS)
                },
                "required_output_language": "zh-TW",
            },
            "source_language": {
                "publication_kind": "source_language", "verified_on_time": 0,
                "by_output_language": {},
                "counts_as_traditional_chinese_success": False,
            },
            "unclassified_misses": 0, "invalid_success_evidence": 0,
        },
    }
    empty = {
        "schema_version": None,
        "measurement_revision": None,
        "evaluated_at": observed_at,
        "window_days": AI_DELIVERY_SLO_WINDOW_DAYS,
        "delivery_deadline_seconds": AI_DELIVERY_DEADLINE_SECONDS,
        "target": AI_DELIVERY_SLO_TARGET,
        **empty_rollup,
        "confidence_level": AI_DELIVERY_SLO_CONFIDENCE_LEVEL,
        "confidence_method": AI_DELIVERY_SLO_CONFIDENCE_METHOD,
        "confidence_lower_bound": None,
        "confidence_target_met": None,
        "fixed_sample_descriptive_only": True,
        "proof_eligible": False,
        "minimum_zero_miss_sample": AI_DELIVERY_SLO_MINIMUM_ZERO_MISS_SAMPLE,
        "coverage_active_queue_total": None,
        "coverage_active_queue_tracked": None,
        "coverage_active_queue_untracked": None,
        "coverage_active_queue_untracked_breakdown": None,
        "coverage_active_queue_complete": None,
        "coverage_inventory_available": False,
        "coverage_inventory_state": "unavailable",
        "coverage_inventory_reason": "unavailable",
        "coverage_inventory_epoch_id": None,
        "coverage_inventory_completed_at": 0.0,
        "coverage_inventory_age_seconds": None,
        "coverage_inventory_total": None,
        "coverage_inventory_delivery_required": None,
        "coverage_inventory_tracked": None,
        "coverage_inventory_untracked": None,
        "coverage_inventory_legacy_grandfathered": None,
        "coverage_inventory_complete": None,
        "coverage_complete": False,
        "continuous_coverage_since": None,
        "coverage_complete_through": None,
        "coverage_chain_epoch_count": 0,
        "coverage_max_gap_seconds": AI_INVENTORY_MAX_AGE_SECONDS,
        "error_budget_remaining": None,
        "minimum_sample": AI_DELIVERY_SLO_MINIMUM_SAMPLE,
        "instrumented_at": 0.0,
        "coverage_started_at": 0.0,
        "full_window": False,
        "target_met": None,
        "state": "unavailable",
        "sample_state": "unavailable",
        "rolling_operational": {
            **empty_rollup, "mode": "rolling_observed_media_census",
            "state": "unavailable", "point_target_met": None,
            "fixed_sample_descriptive_only": True, "proof_eligible": False,
        },
        "cumulative_evidence": {
            **empty_rollup,
            "mode": "fixed_measurement_revision_cumulative_media_cohort",
            "scope": "strict_on_time_delivery_not_semantic_accuracy",
            "state": "unavailable", "anytime_valid": True,
            "confidence_method": AI_DELIVERY_ANYTIME_METHOD,
            "confidence_level": 1.0 - AI_DELIVERY_ANYTIME_ALPHA,
            "betting_fractions": list(AI_DELIVERY_ANYTIME_BETTING_FRACTIONS),
            "e_value_threshold": 1.0 / AI_DELIVERY_ANYTIME_ALPHA,
            "log_e_value": None, "e_value": None, "e_value_overflow": False,
            "lower_confidence_bound": None, "point_target_met": None,
            "target_evidence_met": None,
        },
    }
    database = WORK_PATH / "scanner_state.sqlite3"
    if not database.is_file():
        return empty
    try:
        with _sqlite_connect(database, readonly=True) as connection:
            for table in ("ai_delivery_obligations", "ai_delivery_meta", "ai_candidate_queue"):
                if not _sqlite_table_exists(connection, table):
                    return empty
            meta = dict(connection.execute("SELECT key, value FROM ai_delivery_meta").fetchall())
            revision = str(meta.get("measurement_revision") or "").strip()
            if revision != AI_DELIVERY_MEASUREMENT_REVISION:
                return {**empty, "measurement_revision": revision or None}
            if str(meta.get("schema_version") or "") != "1":
                return empty
            try:
                instrumented_at = float(meta.get("instrumented_at") or 0)
            except (TypeError, ValueError):
                return empty
            if not math.isfinite(instrumented_at) or instrumented_at <= 0:
                return empty
            required = {
                "obligation_id", "state", "eligible_at", "due_at", "verified_at",
                "terminal_at", "updated_at", "attempt_count", "exclusion_code",
                "canonical_path", "media_mtime_ns", "policy_revision",
                "verification_json",
            }
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(ai_delivery_obligations)")}
            if not required.issubset(columns):
                return empty
            policy = str(meta.get("inventory_current_policy_revision") or "")
            active_coverage = _ai_active_queue_coverage(
                connection,
                policy_revision=policy,
            )
            inventory = _ai_inventory_coverage(
                connection, meta=meta, instrumented_at=instrumented_at, observed_at=observed_at
            )
            if inventory is None:
                return empty
            rows = connection.execute(
                """
                SELECT obligation_id, state, eligible_at, due_at, verified_at,
                       terminal_at, updated_at, attempt_count, exclusion_code,
                       policy_revision, verification_json
                FROM ai_delivery_obligations WHERE policy_revision=?
                """,
                (policy,),
            ).fetchall()
    except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
        return empty

    active_total = max(0, int(active_coverage["total"] or 0))
    active_tracked = max(0, min(active_total, int(active_coverage["tracked"] or 0)))
    active_untracked = active_total - active_tracked
    invalid_eligible = 0
    for row in rows:
        try:
            eligible = float(row[2])
        except (TypeError, ValueError, OverflowError):
            invalid_eligible += 1
            continue
        if not math.isfinite(eligible) or eligible <= 0:
            invalid_eligible += 1
    coverage_complete = bool(inventory["complete"] and active_untracked == 0 and invalid_eligible == 0)
    continuous_since = float(inventory.get("continuous_coverage_since") or 0)
    as_of = float(inventory.get("coverage_complete_through") or 0)
    cutoff = as_of - AI_DELIVERY_SLO_WINDOW_DAYS * 86400 if as_of else 0.0
    rolling = _summarize_ai_delivery_rows(
        rows,
        expected_due_from=max(cutoff, continuous_since + AI_DELIVERY_DEADLINE_SECONDS),
        expected_due_to=as_of,
        policy_revision=policy,
    ) if as_of else dict(empty_rollup)
    cumulative = _summarize_ai_delivery_rows(
        rows,
        expected_due_from=continuous_since + AI_DELIVERY_DEADLINE_SECONDS,
        expected_due_to=as_of,
        policy_revision=policy,
    ) if as_of and continuous_since else dict(empty_rollup)
    full_window = bool(
        coverage_complete and cutoff >= continuous_since + AI_DELIVERY_DEADLINE_SECONDS
    )
    rolling_point = (
        bool(rolling["success_rate"] is not None and rolling["success_rate"] >= AI_DELIVERY_SLO_TARGET)
        if coverage_complete and full_window and rolling["denominator"] else None
    )
    rolling_state = (
        "coverage_incomplete" if not coverage_complete else
        "warming" if not full_window else
        "no_matured_obligations" if not rolling["denominator"] else
        "meeting" if rolling_point else "breached"
    )
    try:
        cp_lower = _clopper_pearson_lower_bound(rolling["numerator"], rolling["denominator"])
    except (ArithmeticError, OverflowError, ValueError):
        cp_lower = None
    n = int(cumulative["denominator"])
    s = int(cumulative["numerator"])
    m = int(cumulative["misses"])
    try:
        log_e = _ai_delivery_anytime_log_e(AI_DELIVERY_SLO_TARGET, s, m) if n else None
        lower = _ai_delivery_anytime_lower_bound(s, m)
    except (ArithmeticError, OverflowError, ValueError):
        log_e = lower = None
        coverage_complete = False
    cumulative_point = (
        bool(cumulative["success_rate"] is not None and cumulative["success_rate"] >= AI_DELIVERY_SLO_TARGET)
        if coverage_complete and n else None
    )
    evidence_met = (
        bool(cumulative_point and log_e is not None and log_e >= AI_DELIVERY_ANYTIME_LOG_THRESHOLD)
        if coverage_complete and n else None
    )
    evidence_state = (
        "coverage_incomplete" if not coverage_complete else
        "warming" if not n else
        "supported" if evidence_met else
        "below_target" if cumulative_point is False else "collecting"
    )
    overall_met = bool(rolling_point and evidence_met) if rolling_point is not None and evidence_met is not None else None
    overall_state = (
        "coverage_incomplete" if not coverage_complete else
        "warming" if rolling_point is None or evidence_met is None else
        "meeting" if overall_met else "not_verified"
    )
    max_log = math.log(float.fromhex("0x1.fffffffffffffp+1023"))
    e_value = math.exp(log_e) if log_e is not None and log_e <= max_log else None
    rolling_nested = {
        **rolling, "mode": "rolling_observed_media_census", "as_of": as_of or None,
        "window_days": AI_DELIVERY_SLO_WINDOW_DAYS, "expected_due_from": cutoff or None,
        "expected_due_to": as_of or None, "point_target_met": rolling_point,
        "state": rolling_state, "confidence_level": AI_DELIVERY_SLO_CONFIDENCE_LEVEL,
        "confidence_method": AI_DELIVERY_SLO_CONFIDENCE_METHOD,
        "confidence_lower_bound": cp_lower, "fixed_sample_descriptive_only": True,
        "proof_eligible": False,
    }
    cumulative_nested = {
        **cumulative, "mode": "fixed_measurement_revision_cumulative_media_cohort",
        "scope": "strict_on_time_delivery_not_semantic_accuracy",
        "cohort_started_at": continuous_since or None,
        "coverage_complete_through": as_of or None, "state": evidence_state,
        "confidence_level": 1.0 - AI_DELIVERY_ANYTIME_ALPHA,
        "confidence_method": AI_DELIVERY_ANYTIME_METHOD, "anytime_valid": True,
        "betting_fractions": list(AI_DELIVERY_ANYTIME_BETTING_FRACTIONS),
        "e_value_threshold": 1.0 / AI_DELIVERY_ANYTIME_ALPHA,
        "log_e_value": log_e, "e_value": e_value,
        "e_value_overflow": bool(log_e is not None and e_value is None),
        "lower_confidence_bound": lower, "point_target_met": cumulative_point,
        "target_evidence_met": evidence_met,
    }
    return {
        **empty,
        "schema_version": 1, "measurement_revision": revision,
        "evaluated_at": observed_at, **rolling,
        "confidence_lower_bound": cp_lower, "confidence_target_met": None,
        "coverage_active_queue_total": active_total,
        "coverage_active_queue_tracked": active_tracked,
        "coverage_active_queue_untracked": active_untracked,
        "coverage_active_queue_untracked_breakdown": dict(
            active_coverage["untracked_breakdown"]
        ),
        "coverage_active_queue_complete": active_untracked == 0,
        "coverage_inventory_available": bool(inventory["available"]),
        "coverage_inventory_state": str(inventory["state"]),
        "coverage_inventory_reason": str(inventory["reason"]),
        "coverage_inventory_epoch_id": inventory["epoch_id"],
        "coverage_inventory_completed_at": float(inventory["completed_at"] or 0),
        "coverage_inventory_age_seconds": inventory["age_seconds"],
        "coverage_inventory_total": inventory["total"],
        "coverage_inventory_delivery_required": inventory["delivery_required"],
        "coverage_inventory_tracked": inventory["tracked"],
        "coverage_inventory_untracked": inventory["untracked"],
        "coverage_inventory_legacy_grandfathered": inventory["legacy_grandfathered"],
        "coverage_inventory_complete": bool(inventory["complete"]),
        "coverage_complete": coverage_complete,
        "continuous_coverage_since": continuous_since or None,
        "coverage_complete_through": as_of or None,
        "coverage_chain_epoch_count": int(inventory.get("coverage_chain_epoch_count") or 0),
        "error_budget_remaining": (
            rolling["denominator"] * (1.0 - AI_DELIVERY_SLO_TARGET) - rolling["misses"]
            if rolling["denominator"] else None
        ),
        "instrumented_at": instrumented_at, "coverage_started_at": continuous_since,
        "full_window": full_window, "target_met": overall_met,
        "state": overall_state, "sample_state": overall_state,
        "rolling_operational": rolling_nested,
        "cumulative_evidence": cumulative_nested,
    }


def _ai_delivery_slo_summary(*, now: float | None = None) -> dict[str, Any]:
    if now is not None:
        return _ai_delivery_slo_summary_uncached(now=now)
    cache_key = str(WORK_PATH / "scanner_state.sqlite3")
    cached = _ttl_cache_get(
        _AI_DELIVERY_SLO_SUMMARY_CACHE,
        cache_key,
        AI_DELIVERY_SLO_SUMMARY_CACHE_TTL_SECONDS,
    )
    if cached is not None:
        return cached
    return _ttl_cache_set(
        _AI_DELIVERY_SLO_SUMMARY_CACHE,
        cache_key,
        _ai_delivery_slo_summary_uncached(),
    )


def _v2_overview_payload() -> dict[str, Any]:
    config = _load_config()
    current_ai = _fast_current_ai(config)
    lite_state = _mikan_lite_state(config)
    queue_counts = _fast_queue_counts()
    extract_jobs = (
        lite_state.get("extract_jobs")
        if isinstance(lite_state.get("extract_jobs"), dict)
        else _mikan_extract_jobs_empty()
    )
    extract_counts = extract_jobs.get("counts") if isinstance(extract_jobs.get("counts"), dict) else {}
    pipeline = (
        lite_state.get("pipeline")
        if isinstance(lite_state.get("pipeline"), dict)
        else _mikan_pipeline_counts(lite_state.get("counts") or {}, extract_counts)
    )
    eta = _eta_summary()
    extract_latency = _mikan_extract_latency_summary(config)
    review_total = _fast_open_review_count(config)
    io_policy = _io_policy_summary(config, current_ai)
    ai_scheduler = _ai_scheduler_summary(config)
    ai_delivery_slo = _ai_delivery_slo_summary()
    health_summary = _health_summary(config, fast=True)
    bottleneck = "idle"
    if ai_scheduler.get("problem"):
        bottleneck = "ai_scheduler_error"
    elif current_ai:
        bottleneck = str(current_ai.get("stage") or "ai")
    elif int(pipeline.get("extracting") or 0) > 0:
        bottleneck = "subtitle_extract"
    elif int(pipeline.get("downloading") or 0) > 0:
        bottleneck = "download"
    elif int(queue_counts.get("queued") or 0) > 0:
        bottleneck = "queue_wait"
    snapshot = _stream_state_version()
    return {
        "now": int(time.time()),
        "revision": _v2_revision(snapshot),
        "health": {
            "overall": health_summary.get("overall"),
            "failed_errors": int(health_summary.get("failed_errors") or 0),
            "failed_warnings": int(health_summary.get("failed_warnings") or 0),
        },
        "worker": _worker_summary(fast=True),
        "deployment_hold": _deployment_hold_summary(),
        "queue": queue_counts,
        "current_ai": current_ai,
        "ai_scheduler": ai_scheduler,
        "ai_failed_retry_sweep": read_auto_remediation_status(
            _control_state_db_path(config)
        ),
        "ai_delivery_slo": ai_delivery_slo,
        "completed_delivery": _completed_delivery_overview(config),
        "mikan": {
            "pipeline": pipeline,
            "extract_jobs": {
                "counts": extract_counts,
                "active": int(extract_jobs.get("active") or 0),
                "retryable_count": int(extract_jobs.get("retryable_count") or 0),
            },
            "active_downloads": int(pipeline.get("downloading") or 0),
            "extract_start_latency": extract_latency,
        },
        "reviews": {"open": review_total},
        "bottleneck": bottleneck,
        "eta": {
            "remaining": eta.get("remaining"),
            "seconds": (
                eta.get("eta_seconds")
                if eta.get("eta_seconds") is not None
                else round(float(eta.get("eta_hours")) * 3600)
                if eta.get("eta_hours") is not None
                else None
            ),
            "completed_last_1h": eta.get("completed_last_1h"),
            "completed_last_24h": eta.get("completed_last_24h"),
            "method": eta.get("method") or eta.get("eta_method"),
        },
        "resources": {
            "io": io_policy,
            "disk": _disk_summary().get("work", {}),
            "telemetry": _resource_telemetry_summary(),
            "admission": _resource_admission_summary(config),
        },
    }


def _v2_changed_entities(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    mapping = {
        "scanner_db_mtime": "ai",
        "mikan_db_mtime": "mikan",
        "control_db_mtime": "reviews",
        "app_log_mtime": "events",
        "ai_control_mtime": "ai_control",
        "ai_scheduler_mtime": "ai_scheduler",
        "deployment_hold_mtime": "deployment_hold",
        "action_running": "action",
    }
    for key, entity in mapping.items():
        if previous.get(key) != current.get(key) and entity not in changed:
            changed.append(entity)
    return changed or ["heartbeat"]


def _stream_state_version() -> dict[str, Any]:
    config = _load_config()
    mikan_db = _mikan_state_db_path(config)
    scanner_db = WORK_PATH / "scanner_state.sqlite3"
    app_log = LOG_PATH / "app.log"
    return {
        "mikan_db_mtime": _path_mtime(mikan_db),
        "scanner_db_mtime": _path_mtime(scanner_db),
        "control_db_mtime": _path_mtime(_control_state_db_path(config)),
        "app_log_mtime": _path_mtime(app_log),
        "ai_control_mtime": _path_mtime(WORK_PATH / AI_CONTROL_NAME),
        "ai_scheduler_mtime": _path_mtime(WORK_PATH / AI_SCHEDULER_STATE_NAME),
        "deployment_hold_mtime": _path_mtime(WORK_PATH / DEPLOYMENT_HOLD_NAME),
        "mikan_redownload_active_mtime": _path_mtime(WORK_PATH / "mikan_redownload_all.active.json"),
        "mikan_redownload_cancel_mtime": _path_mtime(WORK_PATH / MIKAN_REDOWNLOAD_CANCEL_NAME),
        "action_running": bool(ACTION_STATE.get("running")),
    }


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _mikan_state_db_summary(config: dict[str, Any], *, event_limit: int = 10) -> dict[str, Any]:
    db_path = _mikan_state_db_path(config)
    result: dict[str, Any] = {
        "path": str(db_path),
        "exists": False,
        "updated_at": 0.0,
        "last_sync_at": 0.0,
        "total": 0,
        "active": 0,
        "stalled": 0,
        "zero_speed_downloading": 0,
        "replacement_needed": 0,
        "counts": {},
        "pipeline": _mikan_pipeline_counts({}, {}),
        "active_samples": [],
        "recent_events": [],
        "jobs": [],
        "extract_jobs": _mikan_extract_jobs_empty(),
    }
    if not db_path.exists():
        return result

    try:
        stat = db_path.stat()
    except OSError as exc:
        return {**result, "exists": True, "error": str(exc)}

    conn: sqlite3.Connection | None = None
    try:
        conn = _sqlite_connect(db_path, readonly=True)
        if not _sqlite_table_exists(conn, "mikan_download_items"):
            return {
                **result,
                "exists": True,
                "updated_at": stat.st_mtime,
                "table_exists": False,
                "jobs": _mikan_jobs_from_conn(conn),
                "extract_jobs": _mikan_extract_jobs_from_conn(conn),
            }

        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(mikan_download_items)").fetchall()}
        status_expr = _mikan_sqlite_completed_aware_status_expr(columns)
        counts = {
            str(status): int(count)
            for status, count in conn.execute(
                f"SELECT {status_expr} AS status, COUNT(*) FROM mikan_download_items GROUP BY {status_expr}"
            ).fetchall()
        }
        total = sum(counts.values())
        active_statuses = ("queued", "downloading", "extracting_subtitles", "completed_waiting_extract", "deferred", "no_candidate_retry")
        active = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM (
                    SELECT {status_expr} AS status
                    FROM mikan_download_items
                )
                WHERE status IN (?, ?, ?, ?, ?, ?)
                """,
                active_statuses,
            ).fetchone()[0]
            or 0
        )
        stalled = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM mikan_download_items
                WHERE lower(COALESCE(last_qbit_state, '')) = 'stalleddl'
                  AND COALESCE(last_dlspeed, 0) <= 0
                """
            ).fetchone()[0]
            or 0
        )
        zero_speed_downloading = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM mikan_download_items
                WHERE status = 'downloading'
                  AND COALESCE(last_dlspeed, 0) <= 0
                """
            ).fetchone()[0]
            or 0
        )
        replacement_needed = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM mikan_download_items
                WHERE status IN ('extract_failed', 'failed_candidate')
                """
            ).fetchone()[0]
            or 0
        )
        active_samples = [
            {
                "key": str(key),
                "title": str(title or ""),
                "status": str(status or ""),
                "next_action": str(next_action or ""),
                "progress": _coerce_float(progress),
                "dlspeed": _coerce_int(dlspeed) or 0,
                "last_qbit_state": str(last_qbit_state or ""),
                "updated_at": float(updated_at or 0),
            }
            for key, title, status, next_action, progress, dlspeed, last_qbit_state, updated_at in conn.execute(
                f"""
                SELECT key, title, status, next_action, last_progress, last_dlspeed, last_qbit_state, updated_at
                FROM (
                    SELECT
                        key, title, {status_expr} AS status, next_action,
                        last_progress, last_dlspeed, last_qbit_state, updated_at
                    FROM mikan_download_items
                )
                WHERE status IN ('queued', 'downloading', 'extracting_subtitles', 'completed_waiting_extract', 'extract_failed', 'failed_candidate')
                ORDER BY
                    CASE status
                        WHEN 'extracting_subtitles' THEN 0
                        WHEN 'completed_waiting_extract' THEN 1
                        WHEN 'downloading' THEN 2
                        WHEN 'queued' THEN 3
                        WHEN 'extract_failed' THEN 4
                        WHEN 'failed_candidate' THEN 5
                        ELSE 9
                    END,
                    updated_at DESC
                LIMIT 8
                """
            ).fetchall()
        ]

        recent_events: list[dict[str, Any]] = []
        if _sqlite_table_exists(conn, "mikan_download_events"):
            recent_events = [
                {
                    "id": int(event_id),
                    "key": str(key),
                    "bangumi_id": bangumi_id,
                    "episode": episode,
                    "event": str(event or ""),
                    "detail": str(detail or ""),
                    "created_at": float(created_at or 0),
                }
                for event_id, key, bangumi_id, episode, event, detail, created_at in conn.execute(
                    """
                    SELECT id, key, bangumi_id, episode, event, detail, created_at
                    FROM mikan_download_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(event_limit or 10), 50)),),
                ).fetchall()
            ]

        last_sync_at = 0.0
        if _sqlite_table_exists(conn, "mikan_state_meta"):
            row = conn.execute(
                "SELECT value, updated_at FROM mikan_state_meta WHERE key = 'last_sync_at'"
            ).fetchone()
            if row is not None:
                last_sync_at = _coerce_float(row[0]) or _coerce_float(row[1]) or 0.0

        jobs = _mikan_jobs_from_conn(conn)
        extract_jobs = _mikan_extract_jobs_from_conn(conn)
        active_extract_jobs = _mikan_active_extract_jobs_from_conn(conn)
        counts = _mikan_counts_with_active_extract_jobs(conn, columns, counts, active_extract_jobs)
        pipeline = _mikan_pipeline_counts(counts, extract_jobs.get("counts") or {})
        active = sum(
            int(pipeline.get(key) or 0)
            for key in ("queued_downloads", "downloading", "extracting", "waiting_extract", "candidate_retry")
        )

        return {
            **result,
            "exists": True,
            "table_exists": True,
            "updated_at": stat.st_mtime,
            "last_sync_at": last_sync_at,
            "total": total,
            "active": active,
            "stalled": stalled,
            "zero_speed_downloading": zero_speed_downloading,
            "replacement_needed": replacement_needed,
            "counts": counts,
            "pipeline": pipeline,
            "active_samples": active_samples,
            "recent_events": recent_events,
            "jobs": jobs,
            "extract_jobs": extract_jobs,
        }
    except sqlite3.Error as exc:
        return {**result, "exists": True, "updated_at": stat.st_mtime, "error": str(exc)}
    finally:
        if conn is not None:
            conn.close()


def _mikan_jobs_from_conn(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _sqlite_table_exists(conn, "mikan_jobs"):
        return []
    return [
        {
            "job_name": str(job_name),
            "status": str(job_status),
            "request_count": int(request_count or 0),
            "worker_id": str(worker_id or ""),
            "lease_until": float(lease_until or 0),
            "payload": _json_object(payload_json),
            "requested_at": float(requested_at or 0),
            "started_at": float(started_at or 0),
            "updated_at": float(updated_at or 0),
            "finished_at": float(finished_at or 0),
            "last_error": str(last_error or ""),
        }
        for (
            job_name,
            job_status,
            request_count,
            worker_id,
            lease_until,
            payload_json,
            requested_at,
            started_at,
            updated_at,
            finished_at,
            last_error,
        ) in conn.execute(
            f"""
            SELECT job_name, status, request_count, worker_id, lease_until, payload_json,
                   requested_at, started_at, updated_at, finished_at, last_error
            FROM mikan_jobs
            ORDER BY updated_at DESC
            LIMIT 20
            """
        ).fetchall()
    ]


def _mikan_extract_jobs_from_conn(
    conn: sqlite3.Connection,
    *,
    include_recent: bool = True,
    include_history: bool = True,
    recent_limit: int = 200,
) -> dict[str, Any]:
    empty = _mikan_extract_jobs_empty()
    if not _sqlite_table_exists(conn, "mikan_extract_jobs"):
        return empty
    counts = {
        str(status): int(count)
        for status, count in conn.execute(
            "SELECT status, COUNT(*) FROM mikan_extract_jobs GROUP BY status"
        ).fetchall()
    }
    extract_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(mikan_extract_jobs)").fetchall()
    }
    retryable_count = (
        sum(
            1
            for (result_json,) in conn.execute(
                "SELECT result_json FROM mikan_extract_jobs WHERE status = 'failed'"
            ).fetchall()
            if _parse_optional_bool(_json_object(result_json).get("retryable")) is True
        )
        if "result_json" in extract_columns
        else 0
    )
    active = int(
        conn.execute(
            "SELECT COUNT(*) FROM mikan_extract_jobs WHERE status IN ('queued', 'running')"
        ).fetchone()[0]
        or 0
    )
    if not include_recent:
        return {
            "counts": counts,
            "active": active,
            "retryable_count": retryable_count,
            "recent": [],
            "recent_failed": [],
            "recent_retryable": [],
            "recent_attention": [],
            "recent_replaced": [],
            "recent_completed": [],
        }

    result_json_expr = "result_json" if "result_json" in extract_columns else "'{}' AS result_json"
    torrent_json_expr = "torrent_json" if "torrent_json" in extract_columns else "'{}' AS torrent_json"
    target_path_expr = "target_path" if "target_path" in extract_columns else "'' AS target_path"
    file_timestamp_expr = (
        "current_file_timestamp"
        if "current_file_timestamp" in extract_columns
        else "0 AS current_file_timestamp"
    )
    file_time_kind_expr = (
        "current_file_time_kind"
        if "current_file_time_kind" in extract_columns
        else "'' AS current_file_time_kind"
    )
    file_size_expr = (
        "current_file_size"
        if "current_file_size" in extract_columns
        else "0 AS current_file_size"
    )

    def read_jobs(where_sql: str, order_sql: str, limit: int) -> list[dict[str, Any]]:
        return [
            {
                "job_key": str(job_key),
                "status": str(status),
                "attempts": int(attempts or 0),
                "torrent_hash": str(torrent_hash or ""),
                "torrent_name": str(torrent_name or ""),
                "episodes": _json_list(episodes_json),
                **_mikan_torrent_time_fields(torrent_json),
                "result": _json_object(result_json),
                "progress": _json_object(result_json).get("progress")
                if isinstance(_json_object(result_json).get("progress"), dict)
                else None,
                "current_file_path": str(target_path or ""),
                "current_file_timestamp": float(current_file_timestamp or 0),
                "current_file_time_kind": str(current_file_time_kind or ""),
                "current_file_size": int(current_file_size or 0),
                "last_error": str(last_error or ""),
                "created_at": float(created_at or 0),
                "updated_at": float(updated_at or 0),
                "started_at": float(started_at or 0),
                "finished_at": float(finished_at or 0),
            }
            for (
                job_key,
                status,
                attempts,
                torrent_hash,
                torrent_name,
                episodes_json,
                torrent_json,
                result_json,
                target_path,
                current_file_timestamp,
                current_file_time_kind,
                current_file_size,
                last_error,
                created_at,
                updated_at,
                started_at,
                finished_at,
            ) in conn.execute(
                f"""
                SELECT job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                       {torrent_json_expr}, {result_json_expr},
                       {target_path_expr}, {file_timestamp_expr}, {file_time_kind_expr}, {file_size_expr},
                       last_error, created_at, updated_at, started_at, finished_at
                FROM mikan_extract_jobs
                {where_sql}
                ORDER BY {order_sql}
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]

    recent_limit = max(1, min(int(recent_limit), 200))
    recent = read_jobs(
        "WHERE status IN ('queued', 'running')",
        """
            CASE status
                WHEN 'running' THEN 0
                WHEN 'queued' THEN 1
                ELSE 9
            END,
            updated_at DESC
        """,
        recent_limit,
    )
    history_limit = min(20, recent_limit)
    recent_failed = []
    recent_retryable = []
    recent_replaced = []
    if include_history:
        recent_failed = read_jobs(
            "WHERE status IN ('failed', 'replaced', 'terminal_failed')",
            """
                CASE status
                    WHEN 'failed' THEN 0
                    WHEN 'replaced' THEN 1
                    WHEN 'terminal_failed' THEN 2
                    ELSE 9
                END,
                COALESCE(finished_at, updated_at, 0) DESC,
                updated_at DESC
            """,
            history_limit,
        )
        recent_retryable = read_jobs(
            "WHERE status = 'failed'",
            "COALESCE(finished_at, updated_at, 0) DESC, updated_at DESC",
            history_limit,
        )
        recent_replaced = read_jobs(
            "WHERE status = 'replaced'",
            "COALESCE(finished_at, updated_at, 0) DESC, updated_at DESC",
            history_limit,
        )
    recent_attention = read_jobs(
        "WHERE status = 'terminal_failed'",
        "COALESCE(finished_at, updated_at, 0) DESC, updated_at DESC",
        history_limit,
    )
    recent_completed = read_jobs(
        "WHERE status = 'success'",
        "COALESCE(finished_at, updated_at, 0) DESC, updated_at DESC",
        history_limit,
    )
    return {
        "counts": counts,
        "active": active,
        "retryable_count": retryable_count,
        "recent": recent,
        "recent_failed": recent_failed,
        "recent_retryable": recent_retryable,
        "recent_attention": recent_attention,
        "recent_replaced": recent_replaced,
        "recent_completed": recent_completed,
    }


def _mikan_extract_jobs_downloads_result(
    conn: sqlite3.Connection,
    base_result: dict[str, Any],
    *,
    db_path: Path,
    stat: os.stat_result,
    page: int,
    page_size: int,
    status_filter: str,
    search: str,
    counts: dict[str, int],
    extracted_total: int,
    extracted_unknown_completed: int,
    cache_key: tuple[Any, ...],
) -> dict[str, Any]:
    """Expose extract-job failures/successes through the downloads API.

    Download rows and extract jobs are separate tables. The downloads list can show
    active jobs by overlaying them onto matching download rows, but terminal
    extract failures often no longer have a matching active download status. When
    the UI asks for an extract-job status explicitly, page directly over
    mikan_extract_jobs so the visible list matches the summary counts.
    """
    where_parts = ["status = ?"]
    params: list[Any] = [status_filter]
    if search:
        pattern = f"%{search}%"
        where_parts.append(
            "(job_key LIKE ? COLLATE NOCASE OR torrent_hash LIKE ? COLLATE NOCASE "
            "OR torrent_name LIKE ? COLLATE NOCASE OR last_error LIKE ? COLLATE NOCASE)"
        )
        params.extend([pattern, pattern, pattern, pattern])
    where_sql = "WHERE " + " AND ".join(where_parts)

    filtered = int(
        conn.execute(
            f"SELECT COUNT(*) FROM mikan_extract_jobs {where_sql}",
            params,
        ).fetchone()[0]
        or 0
    )
    page_count = max(1, (filtered + page_size - 1) // page_size)
    page = min(max(1, page), page_count)
    offset = (page - 1) * page_size
    extract_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(mikan_extract_jobs)").fetchall()
    }
    torrent_json_expr = "torrent_json" if "torrent_json" in extract_columns else "'{}' AS torrent_json"
    target_path_expr = "target_path" if "target_path" in extract_columns else "'' AS target_path"
    file_timestamp_expr = (
        "current_file_timestamp"
        if "current_file_timestamp" in extract_columns
        else "0 AS current_file_timestamp"
    )
    file_time_kind_expr = (
        "current_file_time_kind"
        if "current_file_time_kind" in extract_columns
        else "'' AS current_file_time_kind"
    )
    file_size_expr = (
        "current_file_size" if "current_file_size" in extract_columns else "0 AS current_file_size"
    )
    rows = [
        _mikan_extract_job_download_row(
            job_key=str(job_key),
            status=str(status),
            attempts=int(attempts or 0),
            torrent_hash=str(torrent_hash or ""),
            torrent_name=str(torrent_name or ""),
            episodes=_json_list(episodes_json),
            torrent_json=torrent_json,
            current_file_path=str(target_path or ""),
            current_file_timestamp=float(current_file_timestamp or 0),
            current_file_time_kind=str(current_file_time_kind or ""),
            current_file_size=int(current_file_size or 0),
            last_error=str(last_error or ""),
            created_at=float(created_at or 0),
            updated_at=float(updated_at or 0),
            started_at=float(started_at or 0),
            finished_at=float(finished_at or 0),
        )
        for (
            job_key,
            status,
            attempts,
            torrent_hash,
            torrent_name,
            episodes_json,
            torrent_json,
            target_path,
            current_file_timestamp,
            current_file_time_kind,
            current_file_size,
            last_error,
            created_at,
            updated_at,
            started_at,
            finished_at,
        ) in conn.execute(
            f"""
            SELECT job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                   {torrent_json_expr},
                   {target_path_expr}, {file_timestamp_expr}, {file_time_kind_expr}, {file_size_expr},
                   last_error, created_at, updated_at, started_at, finished_at
            FROM mikan_extract_jobs
            {where_sql}
            ORDER BY
                COALESCE(NULLIF(finished_at, 0), updated_at, created_at, 0) DESC,
                updated_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
    ]

    return _ttl_cache_set(_MIKAN_SQLITE_DOWNLOADS_CACHE, cache_key, {
        **base_result,
        "exists": True,
        "source": "sqlite",
        "database": str(db_path),
        "updated_at": stat.st_mtime,
        "total": filtered,
        "filtered": filtered,
        "counts": counts,
        "extracted_total": extracted_total,
        "extracted_unknown_completed": extracted_unknown_completed,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "recent": rows,
    })


def _mikan_extract_job_download_row(
    *,
    job_key: str,
    status: str,
    attempts: int,
    torrent_hash: str,
    torrent_name: str,
    episodes: list[Any],
    torrent_json: Any,
    current_file_path: str,
    current_file_timestamp: float,
    current_file_time_kind: str,
    current_file_size: int,
    last_error: str,
    created_at: float,
    updated_at: float,
    started_at: float,
    finished_at: float,
) -> dict[str, Any]:
    numeric_episodes = [int(value) for value in episodes if isinstance(value, int) or str(value).isdigit()]
    first_episode = numeric_episodes[0] if numeric_episodes else None
    timestamp = finished_at or updated_at or started_at or created_at
    subtitle_state = "official_ready" if status == "success" else "official_extract_failed_replace"
    next_action = "done" if status == "success" else "find_replacement"
    return {
        "key": job_key,
        "group_key": job_key,
        "job_key": job_key,
        "status": status,
        "title": torrent_name or job_key,
        "source": "qbit-recovered" if torrent_hash else "",
        "source_page": "",
        "bangumi_id": None,
        "episode": first_episode,
        "episodes": numeric_episodes,
        "episode_count": len(numeric_episodes),
        "torrent_url": f"qbit://{torrent_hash}" if torrent_hash else "",
        "torrent_hash": torrent_hash,
        "torrent_name": torrent_name,
        **_mikan_torrent_time_fields(torrent_json),
        "extract_file_path": current_file_path,
        "extract_file_timestamp": current_file_timestamp,
        "extract_file_time_kind": current_file_time_kind,
        "extract_file_size": current_file_size,
        "queued_at": created_at,
        "deferred_at": 0.0,
        "completed_at": finished_at if status == "success" else 0.0,
        "last_extracted_at": finished_at if status == "success" else 0.0,
        "last_extracted_count": 1 if status == "success" else 0,
        "total_extracted_count": 1 if status == "success" else 0,
        "last_progress_at": 0.0,
        "last_qbit_sync_at": 0.0,
        "last_qbit_state": None,
        "last_qbit_hash": torrent_hash,
        "last_qbit_name": torrent_name,
        "last_extract_failed_at": finished_at if status != "success" else 0.0,
        "last_extract_deferred_at": 0.0,
        "last_failure_reason": last_error,
        "last_extract_failure_reason": status if status != "success" else "",
        "last_extract_failure_detail": last_error,
        "last_extract_context": {},
        "last_subtitle_diagnostics": [],
        "subtitle_state": subtitle_state,
        "failed_count": attempts if status != "success" else 0,
        "progress": 1.0 if status == "success" else 0.0,
        "downloaded": None,
        "dlspeed": 0,
        "next_action": next_action,
        "updated_at": updated_at or timestamp,
        "age_seconds": max(0, int(time.time() - timestamp)) if timestamp else 0,
        "extract_job_status": status,
        "extract_job_attempts": attempts,
        "children": [],
    }


def _mikan_active_extract_jobs_from_conn(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _sqlite_table_exists(conn, "mikan_extract_jobs"):
        return []
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(mikan_extract_jobs)").fetchall()}
    torrent_json_expr = "torrent_json" if "torrent_json" in columns else "'{}' AS torrent_json"
    target_path_expr = "target_path" if "target_path" in columns else "'' AS target_path"
    file_timestamp_expr = (
        "current_file_timestamp" if "current_file_timestamp" in columns else "0 AS current_file_timestamp"
    )
    file_time_kind_expr = (
        "current_file_time_kind" if "current_file_time_kind" in columns else "'' AS current_file_time_kind"
    )
    file_size_expr = "current_file_size" if "current_file_size" in columns else "0 AS current_file_size"
    return [
        {
            "job_key": str(job_key),
            "status": str(status),
            "attempts": int(attempts or 0),
            "torrent_hash": str(torrent_hash or ""),
            "torrent_name": str(torrent_name or ""),
            "episodes": _json_list(episodes_json),
            **_mikan_torrent_time_fields(torrent_json),
            "current_file_path": str(target_path or ""),
            "current_file_timestamp": float(current_file_timestamp or 0),
            "current_file_time_kind": str(current_file_time_kind or ""),
            "current_file_size": int(current_file_size or 0),
            "last_error": str(last_error or ""),
            "created_at": float(created_at or 0),
            "updated_at": float(updated_at or 0),
            "started_at": float(started_at or 0),
            "finished_at": float(finished_at or 0),
        }
        for (
            job_key,
            status,
            attempts,
            torrent_hash,
            torrent_name,
            episodes_json,
            torrent_json,
            target_path,
            current_file_timestamp,
            current_file_time_kind,
            current_file_size,
            last_error,
            created_at,
            updated_at,
            started_at,
            finished_at,
        ) in conn.execute(
            f"""
            SELECT job_key, status, attempts, torrent_hash, torrent_name, episodes_json,
                   {torrent_json_expr},
                   {target_path_expr}, {file_timestamp_expr}, {file_time_kind_expr}, {file_size_expr},
                   last_error, created_at, updated_at, started_at, finished_at
            FROM mikan_extract_jobs
            WHERE status IN ('queued', 'running')
            ORDER BY
                CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 9 END,
                updated_at DESC,
                created_at DESC
            LIMIT 200
            """
        ).fetchall()
    ]


def _expand_config_env(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        env_value = os.environ.get(name)
        if env_value is not None:
            return env_value
        if name == "ANIME_WORK_PATH":
            return str(WORK_PATH)
        if default is not None:
            return default
        return match.group(0)

    return ENV_PATTERN.sub(replace, value)


def _mikan_subtitle_state(entry: dict[str, Any], status: str) -> str:
    extracted = (_coerce_int(entry.get("total_extracted_count")) or 0) + (_coerce_int(entry.get("last_extracted_count")) or 0)
    if status == "completed":
        return "official_ready" if extracted > 0 else "official_completed_unknown"
    if status in {"extracting_subtitles", "completed_waiting_extract"}:
        return "official_extracting" if status == "extracting_subtitles" else "official_waiting_extract"
    if status == "downloading":
        return "official_downloading"
    if status == "extract_failed":
        return "official_extract_failed_replace"
    if status == "target_missing":
        return "official_target_missing"
    if status in {"no_candidate_retry", "failed_candidate"}:
        return "no_candidate_retry"
    if status == "deferred":
        return "official_deferred"
    return "unknown"


def _mikan_download_entry(key: str, entry: dict[str, Any], now: float) -> dict[str, Any]:
    status = _mikan_download_status(entry, now)
    failed_urls = entry.get("failed_urls")
    first_failed_url = str(failed_urls[0]) if isinstance(failed_urls, list) and failed_urls else ""
    title = str(
        entry.get("title")
        or entry.get("deferred_title")
        or entry.get("last_completed_title")
        or entry.get("last_failed_title")
        or ""
    )
    source = str(
        entry.get("source")
        or entry.get("deferred_source")
        or entry.get("last_completed_source")
        or entry.get("last_failed_source")
        or ""
    )
    source_page = str(
        entry.get("source_page")
        or entry.get("deferred_source_page")
        or entry.get("last_completed_source_page")
        or entry.get("last_failed_source_page")
        or ""
    )
    torrent_url = str(
        entry.get("torrent_url")
        or entry.get("deferred_torrent_url")
        or entry.get("last_completed_torrent_url")
        or entry.get("last_failed_torrent_url")
        or first_failed_url
        or ""
    )
    episodes = _mikan_entry_episodes(entry)
    progress = _coerce_float(entry.get("last_progress"))
    downloaded = _coerce_int(entry.get("last_downloaded"))
    updated_at = _mikan_entry_updated_at(entry)
    started_at = _mikan_entry_started_at(entry)
    failed_count = len(failed_urls) if isinstance(failed_urls, list) else 0
    group_key = torrent_url or f"{entry.get('bangumi_id')}:{','.join(str(ep) for ep in episodes) or key}:{status}"
    age_seconds = max(0, int(now - started_at)) if started_at else 0
    return {
        "group_key": group_key,
        "sort_priority": _mikan_download_status_priority(status),
        "key": key,
        "status": status,
        "title": title,
        "source": source,
        "source_page": source_page,
        "bangumi_id": entry.get("bangumi_id"),
        "episode": entry.get("episode"),
        "episodes": episodes,
        "episode_count": len(episodes),
        "source_published_at": _mikan_entry_source_publication_timestamp(entry),
        "source_published_precision": _review_source_publication_precision([entry]),
        "torrent_created_at": _parse_timestamp(entry.get("last_qbit_creation_date")),
        "torrent_added_at": _parse_timestamp(entry.get("last_qbit_added_on")),
        "torrent_completed_at": _parse_timestamp(entry.get("last_qbit_completion_on")),
        "torrent_url": torrent_url,
        "last_failed_title": entry.get("last_failed_title"),
        "last_failed_torrent_url": entry.get("last_failed_torrent_url") or first_failed_url,
        "queued_at": _parse_timestamp(entry.get("queued_at")),
        "deferred_at": _parse_timestamp(entry.get("deferred_at")),
        "completed_at": _parse_timestamp(entry.get("completed_at")),
        "last_extracted_at": _parse_timestamp(entry.get("last_extracted_at") or entry.get("completed_at")),
        "last_extracted_count": _coerce_int(entry.get("last_extracted_count")) or 0,
        "total_extracted_count": _coerce_int(entry.get("total_extracted_count")) or 0,
        "last_progress_at": _parse_timestamp(entry.get("last_progress_at")),
        "last_qbit_sync_at": _parse_timestamp(entry.get("last_qbit_sync_at")),
        "last_qbit_state": entry.get("last_qbit_state"),
        "last_qbit_hash": entry.get("last_qbit_hash"),
        "last_qbit_name": entry.get("last_qbit_name"),
        "last_extract_failed_at": _parse_timestamp(entry.get("last_extract_failed_at")),
        "last_extract_deferred_at": _parse_timestamp(entry.get("last_extract_deferred_at")),
        "no_candidate_until": _coerce_float(entry.get("no_candidate_until")),
        "deferred_reason": entry.get("deferred_reason"),
        "last_failure_reason": entry.get("last_failure_reason"),
        "last_extract_failure_reason": entry.get("last_extract_failure_reason"),
        "last_extract_failure_detail": entry.get("last_extract_failure_detail"),
        "last_extract_deferred_reason": entry.get("last_extract_deferred_reason"),
        "last_extract_deferred_detail": entry.get("last_extract_deferred_detail"),
        "last_extract_context": _public_mikan_extract_context(entry.get("last_extract_context")),
        "last_subtitle_diagnostics": _public_subtitle_diagnostics(entry.get("last_subtitle_diagnostics")),
        "subtitle_state": _mikan_subtitle_state(entry, status),
        "failed_count": failed_count,
        "progress": max(0.0, min(1.0, progress)) if progress is not None else None,
        "downloaded": downloaded,
        "dlspeed": _coerce_int(entry.get("last_dlspeed")) or 0,
        "next_action": _mikan_download_next_action(entry, status, now),
        "updated_at": updated_at,
        "age_seconds": age_seconds,
    }


def _mikan_download_child_entry(entry: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "key",
        "status",
        "title",
        "source",
        "source_page",
        "bangumi_id",
        "episode",
        "episodes",
        "episode_count",
        "torrent_url",
        "queued_at",
        "deferred_at",
        "completed_at",
        "last_extracted_at",
        "last_extracted_count",
        "total_extracted_count",
        "last_progress_at",
        "last_qbit_sync_at",
        "last_qbit_state",
        "last_qbit_hash",
        "last_qbit_name",
        "last_extract_failed_at",
        "last_extract_deferred_at",
        "last_failure_reason",
        "last_extract_failure_reason",
        "last_extract_failure_detail",
        "last_extract_deferred_reason",
        "last_extract_deferred_detail",
        "subtitle_state",
        "failed_count",
        "progress",
        "downloaded",
        "dlspeed",
        "next_action",
        "updated_at",
        "age_seconds",
    )
    return {field: entry.get(field) for field in fields if entry.get(field) is not None}


def _merge_mikan_download_entry(existing: dict[str, Any], entry: dict[str, Any]) -> None:
    existing.setdefault("children", []).append(_mikan_download_child_entry(entry))
    existing["episodes"] = sorted({*existing.get("episodes", []), *entry.get("episodes", [])})
    existing["episode_count"] = len(existing["episodes"])
    existing["updated_at"] = max(float(existing.get("updated_at") or 0), float(entry.get("updated_at") or 0))
    existing["age_seconds"] = min(existing.get("age_seconds", entry["age_seconds"]), entry["age_seconds"])
    existing["failed_count"] = max(int(existing.get("failed_count") or 0), int(entry.get("failed_count") or 0))
    existing["last_extracted_count"] = max(
        int(existing.get("last_extracted_count") or 0),
        int(entry.get("last_extracted_count") or 0),
    )
    existing["total_extracted_count"] = max(
        int(existing.get("total_extracted_count") or 0),
        int(entry.get("total_extracted_count") or 0),
    )
    existing["last_extracted_at"] = max(
        float(existing.get("last_extracted_at") or 0),
        float(entry.get("last_extracted_at") or 0),
    )
    if float(entry.get("source_published_at") or 0) >= float(existing.get("source_published_at") or 0):
        if entry.get("source_published_precision"):
            existing["source_published_precision"] = entry["source_published_precision"]
    for key in ("source_published_at", "torrent_created_at", "torrent_added_at", "torrent_completed_at"):
        existing[key] = max(float(existing.get(key) or 0), float(entry.get(key) or 0))
    if _mikan_download_status_priority(str(entry.get("status") or "")) < _mikan_download_status_priority(
        str(existing.get("status") or "")
    ):
        existing["status"] = entry["status"]
        existing["sort_priority"] = entry["sort_priority"]
    if float(entry.get("updated_at") or 0) >= float(existing.get("updated_at") or 0):
        for key in (
            "last_extract_failure_reason",
            "last_extract_failure_detail",
            "last_extract_deferred_reason",
            "last_extract_deferred_detail",
            "last_subtitle_diagnostics",
            "last_failure_reason",
            "last_failed_title",
            "last_failed_torrent_url",
            "last_qbit_sync_at",
            "last_qbit_state",
            "last_qbit_hash",
            "last_qbit_name",
            "last_dlspeed",
            "source",
            "source_page",
        ):
            if entry.get(key):
                existing[key] = entry.get(key)
    if float(entry.get("progress") or 0) > float(existing.get("progress") or 0):
        existing["progress"] = entry["progress"]
        existing["downloaded"] = entry.get("downloaded")
        existing["dlspeed"] = entry.get("dlspeed")
        existing["last_qbit_sync_at"] = entry.get("last_qbit_sync_at")
        existing["last_qbit_state"] = entry.get("last_qbit_state")


def _public_mikan_extract_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "qbit_hash",
        "qbit_name",
        "qbit_content_path",
        "qbit_save_path",
        "qbit_raw_path",
        "mapped_root",
        "mapped_root_exists",
        "source_video",
        "source_video_exists",
        "target_video",
    ):
        if key in value and value[key] not in (None, ""):
            result[key] = value[key]
    mappings = value.get("qbit_path_mappings")
    if isinstance(mappings, list):
        result["qbit_path_mappings"] = [item for item in mappings[:10] if isinstance(item, dict)]
    files = value.get("qbit_files")
    if isinstance(files, list):
        result["qbit_files"] = [item for item in files[:12] if isinstance(item, dict)]
    candidates = value.get("target_candidates")
    if isinstance(candidates, list):
        result["target_candidates"] = [
            {
                key: item.get(key)
                for key in ("path", "score", "reasons")
                if item.get(key) not in (None, "")
            }
            for item in candidates[:3]
            if isinstance(item, dict)
        ]
    return result


def _public_subtitle_diagnostics(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value[:10]:
        if not isinstance(raw, dict):
            continue
        row: dict[str, Any] = {}
        for key in ("source", "status", "kind", "codec", "stream_index", "path", "title", "detail"):
            if key in raw and raw.get(key) not in (None, ""):
                text = str(raw.get(key))
                row[key] = text[:500] if key in {"path", "title", "detail"} else raw.get(key)
        classification = raw.get("classification")
        if isinstance(classification, dict):
            row["classification"] = {
                key: classification.get(key)
                for key in (
                    "language",
                    "reason",
                    "metadata_language",
                    "traditional_score",
                    "simplified_score",
                    "japanese_score",
                    "cjk_chars",
                    "text_chars",
                    "quality_score",
                )
                if key in classification
            }
        if row:
            rows.append(row)
    return rows


def _mikan_download_status(entry: dict[str, Any], now: float) -> str:
    if entry.get("last_failure_reason") == "extract_failed" or entry.get("last_extract_failed_at"):
        return "extract_failed"
    if (
        entry.get("completed_at")
        or (_coerce_int(entry.get("last_extracted_count")) or 0) > 0
        or (_coerce_int(entry.get("total_extracted_count")) or 0) > 0
    ):
        return "completed"
    if entry.get("torrent_url") and entry.get("queued_at"):
        progress = _coerce_float(entry.get("last_progress")) or 0.0
        if progress >= 1.0:
            if entry.get("last_extract_deferred_reason") == "target_video_not_found" or entry.get("last_extract_deferred_at"):
                return "target_missing"
            return "completed_waiting_extract"
        if progress > 0 or (_coerce_int(entry.get("last_downloaded")) or 0) > 0:
            return "downloading"
        return "queued"
    if entry.get("deferred_torrent_url") and entry.get("deferred_at"):
        return "deferred"
    retry_until = _coerce_float(entry.get("no_candidate_until"))
    if retry_until is not None and retry_until > now:
        return "no_candidate_retry"
    failed_urls = entry.get("failed_urls")
    if isinstance(failed_urls, list) and failed_urls:
        return "failed_candidate"
    return "unknown"


def _mikan_download_next_action(entry: dict[str, Any], status: str, now: float) -> str:
    if status == "extracting_subtitles":
        return "extracting_subtitles"
    if status == "downloading":
        if str(entry.get("last_qbit_state") or "") == "stalledDL" and (_coerce_int(entry.get("last_dlspeed")) or 0) <= 0:
            return "replace_when_stall_timeout"
        return "wait_qbit_progress"
    if status == "queued":
        return "wait_qbit_start"
    if status == "completed_waiting_extract":
        return "extract_subtitles"
    if status == "target_missing":
        return "wait_target_video"
    if status in {"extract_failed", "failed_candidate"}:
        return "find_replacement"
    if status == "no_candidate_retry":
        retry_until = _coerce_float(entry.get("no_candidate_until")) or 0.0
        return "retry_candidate_search" if retry_until <= now else "wait_retry_window"
    if status == "deferred":
        return "queue_when_qbit_available"
    if status == "completed":
        return "done"
    return "inspect_state"


def _mikan_entry_episodes(entry: dict[str, Any]) -> list[int]:
    result: set[int] = set()
    episodes = entry.get("episodes")
    if isinstance(episodes, (list, tuple, set)):
        for value in episodes:
            coerced = _coerce_int(value)
            if coerced is not None:
                result.add(coerced)
    episode = _coerce_int(entry.get("episode"))
    if episode is not None:
        result.add(episode)
    return sorted(result)


def _mikan_entry_updated_at(entry: dict[str, Any]) -> float:
    return max(
        _parse_timestamp(entry.get("last_progress_at")),
        _parse_timestamp(entry.get("last_qbit_sync_at")),
        _parse_timestamp(entry.get("last_extracted_at")),
        _parse_timestamp(entry.get("last_extract_failed_at")),
        _parse_timestamp(entry.get("last_extract_deferred_at")),
        _parse_timestamp(entry.get("completed_at")),
        _parse_timestamp(entry.get("queued_at")),
        _parse_timestamp(entry.get("last_qbit_sync_at")),
        _parse_timestamp(entry.get("deferred_at")),
        _parse_timestamp(entry.get("no_candidate_at")),
        _coerce_float(entry.get("no_candidate_until")) or 0.0,
    )


def _mikan_entry_started_at(entry: dict[str, Any]) -> float:
    return max(
        _parse_timestamp(entry.get("queued_at")),
        _parse_timestamp(entry.get("deferred_at")),
        _parse_timestamp(entry.get("no_candidate_at")),
        _parse_timestamp(entry.get("last_extracted_at")),
        _parse_timestamp(entry.get("last_extract_failed_at")),
        _parse_timestamp(entry.get("last_extract_deferred_at")),
        _parse_timestamp(entry.get("completed_at")),
    )


def _mikan_download_status_priority(status: str) -> int:
    return {
        "extracting_subtitles": 0,
        "completed_waiting_extract": 1,
        "target_missing": 2,
        "downloading": 3,
        "queued": 4,
        "deferred": 5,
        "extract_failed": 6,
        "failed_candidate": 7,
        "no_candidate_retry": 8,
        "unknown": 9,
        "completed": 10,
    }.get(status, 10)


def _mikan_download_sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    return (
        int(row.get("sort_priority") or 9),
        -float(row.get("updated_at") or 0),
        str(row.get("title") or row.get("key") or ""),
    )


def _mikan_download_child_sort_key(row: dict[str, Any]) -> tuple[int, int, float, str]:
    episode = _coerce_int(row.get("episode"))
    return (
        episode if episode is not None else 999999,
        _mikan_download_status_priority(str(row.get("status") or "")),
        -float(row.get("updated_at") or 0),
        str(row.get("key") or ""),
    )


def _parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return 0.0
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return _datetime_fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _datetime_fromisoformat(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _lock_file_summary(path: Path) -> dict[str, Any]:
    lock: dict[str, Any] = {"path": str(path), "exists": False}
    if not path.exists():
        return lock
    try:
        stat = path.stat()
        info = _read_simple_lock_info(path)
    except OSError as exc:
        return {**lock, "exists": True, "error": str(exc)}
    return {
        **lock,
        "exists": True,
        "mtime": stat.st_mtime,
        "age_seconds": max(0, int(time.time() - stat.st_mtime)),
        **info,
    }


def _mark_reused_pid_stale_locks(locks: list[dict[str, Any]]) -> None:
    latest_start_by_pid: dict[Any, float] = {}
    for lock in locks:
        if not lock.get("exists"):
            continue
        pid = lock.get("pid")
        process_start = _coerce_float(lock.get("process_start"))
        if pid is None or process_start is None:
            continue
        latest_start_by_pid[pid] = max(process_start, latest_start_by_pid.get(pid, process_start))

    for lock in locks:
        if not lock.get("exists"):
            lock["active"] = False
            continue
        pid = lock.get("pid")
        process_start = _coerce_float(lock.get("process_start"))
        latest_start = latest_start_by_pid.get(pid)
        if process_start is not None and latest_start is not None and process_start + 1.0 < latest_start:
            lock["stale"] = True
            lock["stale_reason"] = "process_reused"
        lock["active"] = _lock_is_active(lock)


def _lock_is_active(lock: dict[str, Any]) -> bool:
    return bool(lock.get("exists") and not lock.get("stale"))


def _request_file_summary(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": False}
    if not path.exists():
        return result
    try:
        stat = path.stat()
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**result, "exists": True, "error": str(exc)}
    if not isinstance(payload, dict):
        payload = {}
    return {
        **result,
        "exists": True,
        "mtime": stat.st_mtime,
        "age_seconds": max(0, int(time.time() - stat.st_mtime)),
        "action": payload.get("action"),
        "request_count": payload.get("request_count"),
        "requested_at": payload.get("requested_at"),
        "started_at": payload.get("started_at"),
        "updated_at": payload.get("updated_at"),
        "delete_files": payload.get("delete_files"),
        "reason": payload.get("reason"),
        "qbit_deleted_at": payload.get("qbit_deleted_at"),
        "state_reset_at": payload.get("state_reset_at"),
        "enqueue_completed_at": payload.get("enqueue_completed_at"),
        "stage": payload.get("stage"),
        "stage_label": payload.get("stage_label"),
        "current": payload.get("current"),
        "total": payload.get("total"),
        "queued": payload.get("queued"),
        "deferred": payload.get("deferred"),
        "deleted_torrents": payload.get("deleted_torrents"),
        "bangumi_id": payload.get("bangumi_id"),
        "scan_current": payload.get("scan_current"),
        "scan_total": payload.get("scan_total"),
        "scan_path": payload.get("scan_path"),
    }


def _read_simple_lock_info(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if not sep:
                continue
            key = key.strip()
            value = value.strip()
            if key == "pid":
                try:
                    info["pid"] = int(value)
                except ValueError:
                    info["pid"] = value
            elif key in {"created", "process_start"}:
                info[key] = value
    except OSError as exc:
        info["error"] = str(exc)
    return info


def _tail_file(path: Path, lines: int) -> str:
    if not path.exists():
        return ""
    lines = max(1, int(lines))
    chunk_size = 8192
    max_bytes = 2 * 1024 * 1024
    data = bytearray()
    newline_count = 0
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        while position > 0 and newline_count <= lines and len(data) < max_bytes:
            read_size = min(chunk_size, position, max_bytes - len(data))
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            newline_count += chunk.count(b"\n")
            data[:0] = chunk
    text = bytes(data).decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def _docker_exec(container_name: str, command: list[str], *, timeout_seconds: float | None = None) -> str:
    created = _docker_request(
        "POST",
        f"/containers/{container_name}/exec",
        {
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": True,
            "Cmd": command,
        },
    )
    exec_id = created.get("Id")
    if not exec_id:
        raise HTTPException(status_code=500, detail="Docker exec did not return an id")
    output = _docker_request(
        "POST",
        f"/exec/{exec_id}/start",
        {"Detach": False, "Tty": True},
        parse_json=False,
        timeout_seconds=timeout_seconds or DOCKER_EXEC_TIMEOUT_SECONDS,
    )
    inspected = _docker_request("GET", f"/exec/{exec_id}/json")
    exit_code = inspected.get("ExitCode")
    if exit_code not in {0, None}:
        raise HTTPException(
            status_code=500,
            detail=f"Docker exec failed exit_code={exit_code}: {str(output)[-4000:]}",
        )
    return str(output)


def _format_background_action_error(action: str, detail: str) -> str:
    if action.startswith("mikan-") and _is_mikan_lock_busy_error(detail):
        return (
            "Mikan 正在執行另一個操作，這次背景操作沒有拿到鎖。\n"
            "新版 worker 會先等待 mikan_operation_lock_wait_seconds 秒；若仍看到這個訊息，"
            "代表背景掃描或抽字幕超過等待時間，稍後重試即可。\n"
            "\n"
            f"{_last_nonempty_line(detail)}"
        )
    return detail


def _is_mikan_lock_busy_error(text: str) -> bool:
    return (
        "Mikan operation already running" in text
        or "Mikan operation still running after waiting" in text
    )


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return text.strip()


def _docker_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    parse_json: bool = True,
    return_bytes: bool = False,
    timeout_seconds: float | None = None,
) -> Any:
    if not DOCKER_SOCKET.exists():
        raise HTTPException(status_code=503, detail=f"Docker socket not mounted: {DOCKER_SOCKET}")
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is None:
        raise HTTPException(status_code=503, detail="Unix Docker socket is not supported by this Python runtime")

    payload = b""
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
    headers = [
        f"{method} {path} HTTP/1.1",
        "Host: docker",
        "Connection: close",
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
        "",
        "",
    ]
    request_bytes = "\r\n".join(headers).encode("utf-8") + payload
    timeout = DOCKER_API_TIMEOUT_SECONDS if timeout_seconds is None else max(1.0, float(timeout_seconds))

    try:
        with socket.socket(af_unix, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(DOCKER_SOCKET))
            client.sendall(request_bytes)
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=504, detail=f"Docker API request failed or timed out: {exc}") from exc

    raw = b"".join(chunks)
    header_bytes, _, response_body = raw.partition(b"\r\n\r\n")
    header_text = header_bytes.decode("iso-8859-1", errors="replace")
    if "transfer-encoding: chunked" in header_text.casefold():
        response_body = _decode_chunked(response_body)
    first_line = header_text.splitlines()[0] if header_text else "HTTP/1.1 500"
    try:
        status_code = int(first_line.split()[1])
    except (IndexError, ValueError):
        status_code = 500

    text = response_body.decode("utf-8", errors="replace")
    if status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Docker API error {status_code}: {text[:500]}")
    if return_bytes:
        return response_body
    if not parse_json:
        return text
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _decode_chunked(body: bytes) -> bytes:
    decoded = bytearray()
    index = 0
    while index < len(body):
        line_end = body.find(b"\r\n", index)
        if line_end < 0:
            break
        size_line = body[index:line_end].split(b";", 1)[0].strip()
        try:
            size = int(size_line, 16)
        except ValueError:
            return body
        index = line_end + 2
        if size == 0:
            break
        decoded.extend(body[index : index + size])
        index += size + 2
    return bytes(decoded)
