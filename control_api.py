from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Callable


class CommandConflictError(ValueError):
    pass


_REVIEW_READ_RETRY_DELAYS_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8)


def stable_id(prefix: str, *values: object) -> str:
    digest = hashlib.sha256("\x1f".join(str(value) for value in values).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def configured_path(
    config: dict[str, Any],
    work_path: Path,
    key: str,
    default: str,
    expand: Callable[[str], str],
) -> Path:
    raw = expand(str(config.get(key) or default)).strip() or default
    path = Path(raw)
    return path if path.is_absolute() else work_path / path


def enqueue_atomic_command(
    *,
    config: dict[str, Any],
    work_path: Path,
    expand: Callable[[str], str],
    action: str,
    target: str,
    parameters: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    command_id = stable_id("cmd", idempotency_key)
    database = configured_path(config, work_path, "control_state_path", "control_state.sqlite3", expand)
    existing = read_command(database, command_id)
    if existing is not None:
        if (
            str(existing.get("action") or "") != str(action)
            or str(existing.get("target") or "") != str(target)
            or existing.get("parameters") != parameters
        ):
            raise CommandConflictError(
                "Idempotency-Key was already used with a different command payload"
            )
        return existing
    inbox = configured_path(config, work_path, "control_inbox_path", "control_inbox", expand)
    inbox.mkdir(parents=True, exist_ok=True)
    destination = inbox / f"{command_id}.json"
    payload = {
        "command_id": command_id,
        "action": str(action),
        "target": str(target),
        "parameters": parameters,
        "idempotency_key": str(idempotency_key),
        "requested_at": time.time(),
    }
    if destination.exists():
        try:
            queued = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CommandConflictError(
                "Existing idempotent command payload cannot be verified"
            ) from exc
        if (
            not isinstance(queued, dict)
            or str(queued.get("action") or "") != str(action)
            or str(queued.get("target") or "") != str(target)
            or queued.get("parameters") != parameters
        ):
            raise CommandConflictError(
                "Idempotency-Key was already used with a different command payload"
            )
    else:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=inbox,
                prefix=f".{command_id}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            try:
                temporary.replace(destination)
            except OSError:
                if not destination.exists():
                    raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {
        "command_id": command_id,
        "action": str(action),
        "target": str(target),
        "parameters": parameters,
        "status": "accepted",
        "requested_at": payload["requested_at"],
    }


def read_command(database: Path, command_id: str) -> dict[str, Any] | None:
    if not database.exists():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        row = connection.execute(
            "SELECT * FROM control_commands WHERE command_id=?", (str(command_id),)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        return None
    payload = dict(row)
    payload["parameters"] = _json_object(payload.pop("parameters_json", "{}"))
    payload["result"] = _json_object(payload.pop("result_json", "{}"))
    payload.pop("idempotency_key", None)
    return payload


def read_auto_remediation_status(database: Path) -> dict[str, Any]:
    idle = {
        "available": True,
        "state": "idle",
        "campaign_id": "",
        "counters": {},
        "current_item": None,
    }
    if not database.exists():
        return idle
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        row = connection.execute(
            """
            SELECT *
            FROM auto_remediation_campaigns
            ORDER BY created_at DESC, campaign_id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return idle
        payload = dict(row)
        payload["parameters"] = _json_object(payload.pop("parameters_json", "{}"))
        payload["counters"] = _json_object(payload.pop("counters_json", "{}"))
        current_item = None
        current_item_id = str(payload.get("current_item_id") or "")
        if current_item_id:
            item_row = connection.execute(
                "SELECT * FROM auto_remediation_items WHERE item_id=?",
                (current_item_id,),
            ).fetchone()
            if item_row is not None:
                current_item = dict(item_row)
                current_item["before"] = _json_object(current_item.pop("before_json", "{}"))
                current_item["result"] = _json_object(current_item.pop("result_json", "{}"))
        return {"available": True, **payload, "current_item": current_item}
    except sqlite3.Error:
        return {"available": False, "state": "unavailable", "campaign_id": "", "counters": {}, "current_item": None}
    finally:
        if connection is not None:
            connection.close()


def read_review(database: Path, review_id: str) -> dict[str, Any] | None:
    """Read one review item without opening a writable SQLite connection."""

    if not database.exists():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        row = connection.execute(
            "SELECT * FROM review_items WHERE review_id=?",
            (str(review_id),),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if connection is not None:
            connection.close()
    return _review_payload(row) if row is not None else None


def list_reviews(
    database: Path,
    *,
    status: str,
    kind: str,
    limit: int,
    offset: int,
    state: str = "",
    search: str = "",
    sort: str = "priority",
    active_queue_targets: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Read a review page, retrying a bounded set of transient SQLite failures.

    A successful empty query is authoritative and returns immediately.  Only
    ``OperationalError`` retries the complete count-and-page read and discards
    every partial attempt.  Exhausted or permanent SQLite failures retain the
    existing fail-closed result.
    """

    if not database.exists():
        return [], 0
    for attempt in range(len(_REVIEW_READ_RETRY_DELAYS_SECONDS) + 1):
        try:
            return _list_reviews_once(
                database,
                status=status,
                kind=kind,
                limit=limit,
                offset=offset,
                state=state,
                search=search,
                sort=sort,
                active_queue_targets=active_queue_targets,
            )
        except sqlite3.OperationalError:
            if attempt >= len(_REVIEW_READ_RETRY_DELAYS_SECONDS):
                return [], 0
            time.sleep(_REVIEW_READ_RETRY_DELAYS_SECONDS[attempt])
        except sqlite3.Error:
            return [], 0
    return [], 0


def _list_reviews_once(
    database: Path,
    *,
    status: str,
    kind: str,
    limit: int,
    offset: int,
    state: str = "",
    search: str = "",
    sort: str = "priority",
    active_queue_targets: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    normalized_state = str(state or "").strip().casefold()
    normalized_status = str(status or "").strip().casefold()
    if normalized_state == "resolved":
        normalized_status = "resolved"
    elif normalized_state in {"needs_action", "processing"}:
        normalized_status = "open"
    clauses = ["1=1"]
    parameters: list[Any] = []
    if normalized_status:
        clauses.append("r.status=?")
        parameters.append(normalized_status)
    if kind:
        clauses.append("r.kind=?")
        parameters.append(kind)
    if search.strip():
        token = f"%{search.strip()}%"
        clauses.append("(r.summary LIKE ? OR r.target_key LIKE ? OR r.diagnosis_json LIKE ?)")
        parameters.extend([token, token, token])
    where = " AND ".join(clauses)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        command_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(control_commands)").fetchall()
        }
        review_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(review_items)").fetchall()
        }
        if normalized_state == "processing" and "review_id" not in command_columns:
            return [], 0
        if normalized_state in {"processing", "needs_action"} and "review_id" in command_columns:
            active_states = ["c.status IN ('queued','running')"]
            normalized_active_targets = sorted(
                str(target) for target in (active_queue_targets or set()) if str(target)
            )
            if (
                normalized_active_targets
                and len(normalized_active_targets) <= 500
                and {"target", "result_json", "finished_at", "requested_at"}.issubset(command_columns)
            ):
                active_target_placeholders = ",".join("?" for _target in normalized_active_targets)
                active_states.append(
                    "(c.status='completed' "
                    "AND LOWER(REPLACE(COALESCE(c.result_json, ''), ' ', '')) LIKE '%\"queued\":true%' "
                    f"AND c.target IN ({active_target_placeholders}) "
                    "AND COALESCE(NULLIF(c.finished_at, 0), c.requested_at, 0) >= COALESCE(r.updated_at, 0))"
                )
                parameters.extend(normalized_active_targets)
            newest_only = ""
            if {"command_id", "requested_at"}.issubset(command_columns):
                newest_only = (
                    " AND NOT EXISTS (SELECT 1 FROM control_commands newer "
                    "WHERE newer.review_id=c.review_id AND "
                    "(newer.requested_at>c.requested_at OR "
                    "(newer.requested_at=c.requested_at AND newer.command_id>c.command_id)))"
                )
            active_exists = (
                "EXISTS (SELECT 1 FROM control_commands c "
                f"WHERE c.review_id=r.review_id AND ({' OR '.join(active_states)}){newest_only})"
            )
            where += f" AND {active_exists}" if normalized_state == "processing" else f" AND NOT {active_exists}"
        if "canonical_key" not in review_columns:
            legacy_rows = connection.execute(
                f"SELECT r.* FROM review_items r WHERE {where} ORDER BY r.updated_at DESC, r.review_id",
                parameters,
            ).fetchall()
            legacy_items = _collapse_open_review_duplicates([_review_payload(row) for row in legacy_rows])
            normalized_sort = str(sort or "").strip().casefold()
            if normalized_sort == "oldest":
                legacy_items.sort(key=lambda item: (float(item.get("updated_at") or 0), str(item.get("review_id") or "")))
            elif normalized_sort == "latest":
                legacy_items.sort(key=lambda item: (float(item.get("updated_at") or 0), str(item.get("review_id") or "")), reverse=True)
            else:
                severity_order = {"error": 0, "warning": 1}
                legacy_items.sort(
                    key=lambda item: (
                        severity_order.get(str(item.get("severity") or "").casefold(), 2),
                        float(item.get("updated_at") or 0),
                        str(item.get("review_id") or ""),
                    )
                )
            start = max(0, int(offset))
            page_size = max(1, min(200, int(limit)))
            return legacy_items[start : start + page_size], len(legacy_items)
        group_expression = (
            "CASE WHEN r.status='open' AND r.kind='target_ambiguity' "
            "AND COALESCE(r.canonical_key, '')<>'' "
            "THEN r.kind || ':' || r.canonical_key ELSE r.review_id END"
        )
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM (SELECT 1 FROM review_items r WHERE {where} GROUP BY {group_expression})",
                parameters,
            ).fetchone()[0]
        )
        order = {
            "latest": "updated_at DESC, review_id",
            "oldest": "updated_at ASC, review_id",
        }.get(
            str(sort or "").strip().casefold(),
            "CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, updated_at ASC, review_id",
        )
        page_size = max(1, min(200, int(limit)))
        rows = connection.execute(
            f"""
            WITH ranked AS (
                SELECT r.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY {group_expression}
                           ORDER BY LENGTH(COALESCE(r.candidates_json, '')) DESC,
                                    r.updated_at DESC,
                                    r.review_id
                       ) AS duplicate_rank,
                       COUNT(*) OVER (PARTITION BY {group_expression}) AS duplicate_count
                FROM review_items r
                WHERE {where}
            )
            SELECT * FROM ranked
            WHERE duplicate_rank=1
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            [*parameters, page_size, max(0, int(offset))],
        ).fetchall()
    finally:
        if connection is not None:
            connection.close()
    return [_review_payload(row) for row in rows], total


def review_command_states(
    database: Path,
    review_ids: list[str],
    *,
    inbox: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the newest durable or accepted command for each review item."""

    wanted = {str(value) for value in review_ids if str(value)}
    if not wanted:
        return {}
    states: dict[str, dict[str, Any]] = {}
    if database.exists():
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(control_commands)").fetchall()
            }
            if "review_id" in columns:
                placeholders = ",".join("?" for _value in wanted)
                rows = connection.execute(
                    f"""
                    SELECT * FROM control_commands
                    WHERE review_id IN ({placeholders})
                    ORDER BY requested_at DESC, command_id DESC
                    """,
                    sorted(wanted),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM control_commands WHERE action LIKE 'review.%' "
                    "ORDER BY requested_at DESC, command_id DESC LIMIT 1000"
                ).fetchall()
            for row in rows:
                command = _command_payload(row)
                review_id = str(command.get("review_id") or command.get("parameters", {}).get("review_id") or "")
                if review_id in wanted and review_id not in states:
                    states[review_id] = command
        except sqlite3.Error:
            pass
        finally:
            if connection is not None:
                connection.close()
    if inbox is not None and inbox.is_dir():
        for path in sorted(inbox.glob("cmd_*.json"), key=lambda item: item.name)[:500]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
            review_id = str(parameters.get("review_id") or "")
            if review_id not in wanted:
                continue
            accepted = {
                "command_id": str(payload.get("command_id") or path.stem),
                "action": str(payload.get("action") or ""),
                "review_id": review_id,
                "status": "accepted",
                "parameters": parameters,
                "requested_at": float(payload.get("requested_at") or 0),
                "started_at": 0.0,
                "finished_at": 0.0,
                "error": "",
                "result": {},
            }
            previous = states.get(review_id)
            if previous is None or accepted["requested_at"] > float(previous.get("requested_at") or 0):
                states[review_id] = accepted
    return states


def review_queue_states(database: Path, targets: list[str]) -> dict[str, dict[str, Any]]:
    """Read current AI queue state for review command targets without writes."""

    wanted = {str(value) for value in targets if str(value)}
    if not wanted or not database.exists():
        return {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(ai_candidate_queue)").fetchall()
        }
        if not {"path", "status"}.issubset(columns):
            return {}
        last_error = "last_error" if "last_error" in columns else "'' AS last_error"
        updated_at = "updated_at" if "updated_at" in columns else "0 AS updated_at"
        states: dict[str, dict[str, Any]] = {}
        ordered = sorted(wanted)
        for start in range(0, len(ordered), 500):
            chunk = ordered[start : start + 500]
            placeholders = ",".join("?" for _target in chunk)
            rows = connection.execute(
                f"SELECT path, status, {last_error}, {updated_at} "
                f"FROM ai_candidate_queue WHERE path IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                payload = dict(row)
                states[str(payload.get("path") or "")] = payload
        return states
    except sqlite3.Error:
        return {}
    finally:
        if connection is not None:
            connection.close()


def review_active_queue_targets(database: Path) -> set[str]:
    """Return bounded active queue targets created by review remediation."""

    if not database.exists():
        return set()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(ai_candidate_queue)").fetchall()
        }
        if not {"path", "status", "source"}.issubset(columns):
            return set()
        rows = connection.execute(
            """
            SELECT path
            FROM ai_candidate_queue
            WHERE status IN ('queued', 'running')
              AND source IN ('auto_review_remediation', 'manual_force')
            LIMIT 501
            """
        ).fetchall()
        if len(rows) > 500:
            return set()
        return {str(row[0]) for row in rows if str(row[0] or "")}
    except sqlite3.Error:
        return set()
    finally:
        if connection is not None:
            connection.close()


def review_state_counts(database: Path) -> dict[str, int]:
    if not database.exists():
        return {"needs_action": 0, "processing": 0, "resolved": 0, "open": 0}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        review_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(review_items)")}
        command_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(control_commands)")}
        if "canonical_key" in review_columns:
            key = (
                "CASE WHEN status='open' AND kind='target_ambiguity' AND COALESCE(canonical_key,'')<>'' "
                "THEN kind || ':' || canonical_key ELSE review_id END"
            )
            open_count = int(connection.execute(f"SELECT COUNT(DISTINCT {key}) FROM review_items WHERE status='open'").fetchone()[0])
            resolved_count = int(connection.execute(f"SELECT COUNT(DISTINCT {key}) FROM review_items WHERE status='resolved'").fetchone()[0])
        else:
            rows = connection.execute("SELECT * FROM review_items").fetchall()
            payloads = [_review_payload(row) for row in rows]
            open_count = len(_collapse_open_review_duplicates([item for item in payloads if item.get("status") == "open"]))
            resolved_count = sum(1 for item in payloads if item.get("status") == "resolved")
        processing_count = 0
        if "review_id" in command_columns:
            processing_count = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT r.review_id)
                    FROM review_items r
                    WHERE r.status='open' AND EXISTS (
                        SELECT 1 FROM control_commands c
                        WHERE c.review_id=r.review_id AND c.status IN ('queued','running')
                    )
                    """
                ).fetchone()[0]
            )
        processing_count = min(open_count, processing_count)
        return {
            "needs_action": max(0, open_count - processing_count),
            "processing": processing_count,
            "resolved": resolved_count,
            "open": open_count,
        }
    except sqlite3.Error:
        return {"needs_action": 0, "processing": 0, "resolved": 0, "open": 0}
    finally:
        if connection is not None:
            connection.close()


def _collapse_open_review_duplicates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Defensively collapse legacy duplicate ambiguity cards without writing SQLite.

    Worker schema v3 enforces this invariant.  This read-side guard keeps an old
    database understandable during startup or rollback: only an exact 40-byte
    torrent hash is safe enough to identify two cards as the same source.
    """

    groups: dict[str, list[dict[str, Any]]] = {}
    ordered_keys: list[str] = []
    for item in items:
        diagnosis = item.get("diagnosis") if isinstance(item.get("diagnosis"), dict) else {}
        torrent_hash = str(diagnosis.get("torrent_hash") or "").strip().casefold()
        canonical = str(item.get("canonical_key") or "").strip().casefold()
        exact_hash = torrent_hash if len(torrent_hash) == 40 and all(char in "0123456789abcdef" for char in torrent_hash) else ""
        if (
            str(item.get("status") or "") == "open"
            and str(item.get("kind") or "") == "target_ambiguity"
            and exact_hash
        ):
            key = canonical if canonical == f"torrent:{exact_hash}" else f"torrent:{exact_hash}"
        else:
            key = f"review:{item.get('review_id') or len(ordered_keys)}"
        if key not in groups:
            groups[key] = []
            ordered_keys.append(key)
        groups[key].append(item)

    collapsed: list[dict[str, Any]] = []
    for key in ordered_keys:
        entries = groups[key]
        representative = max(
            entries,
            key=lambda item: (
                len(item.get("candidates") or []),
                len((item.get("diagnosis") or {}).get("bangumi_ids") or []),
                float(item.get("updated_at") or 0),
            ),
        )
        merged = dict(representative)
        merged_candidates: list[Any] = []
        seen_candidates: set[str] = set()
        for entry in [representative, *[value for value in entries if value is not representative]]:
            for candidate in entry.get("candidates") or []:
                identity = json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str)
                if identity in seen_candidates:
                    continue
                seen_candidates.add(identity)
                merged_candidates.append(candidate)
        merged["candidates"] = merged_candidates
        merged["duplicate_count"] = len(entries)
        if len(entries) > 1:
            merged["duplicate_review_ids"] = [str(entry.get("review_id") or "") for entry in entries]
        collapsed.append(merged)
    return collapsed


def _review_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload.pop("duplicate_rank", None)
    payload["diagnosis"] = _json_object(payload.pop("diagnosis_json", "{}"))
    payload["candidates"] = _json_list(payload.pop("candidates_json", "[]"))
    payload["resolution"] = _json_object(payload.pop("resolution_json", "{}"))
    return payload


def _command_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["parameters"] = _json_object(payload.pop("parameters_json", "{}"))
    payload["result"] = _json_object(payload.pop("result_json", "{}"))
    payload.pop("idempotency_key", None)
    return payload


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
