from __future__ import annotations

import gc
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import control_api


class ReviewReadRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "control_state.sqlite3"
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE control_commands (command_id TEXT PRIMARY KEY)")
            connection.execute(
                """
                CREATE TABLE review_items (
                    review_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    canonical_key TEXT NOT NULL DEFAULT '',
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

    def tearDown(self) -> None:
        gc.collect()
        self.temporary.cleanup()

    def _insert_reviews(self, count: int) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.executemany(
                """
                INSERT INTO review_items(
                    review_id, kind, target_key, severity, summary,
                    diagnosis_json, candidates_json, status, created_at, updated_at
                ) VALUES (?, 'subtitle_quality', ?, 'error', ?, '{}', '[]', 'open', ?, ?)
                """,
                [
                    (f"review_{index:024d}", f"/anime/{index}.mkv", f"review {index}", index, index)
                    for index in range(count)
                ],
            )

    def _list(self, *, limit: int = 2, offset: int = 0) -> tuple[list[dict], int]:
        return control_api.list_reviews(
            self.database,
            status="open",
            kind="",
            limit=limit,
            offset=offset,
        )

    def test_third_page_retries_five_open_failures_then_returns_all_655_total(self) -> None:
        self._insert_reviews(655)
        real_connect = sqlite3.connect
        attempts = 0

        def flaky_connect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts <= 5:
                raise sqlite3.OperationalError("unable to open database file")
            return real_connect(*args, **kwargs)

        with (
            patch.object(control_api.sqlite3, "connect", side_effect=flaky_connect),
            patch.object(control_api.time, "sleep") as sleep,
        ):
            items, total = self._list(limit=200, offset=400)

        self.assertEqual(total, 655)
        self.assertEqual(len(items), 200)
        self.assertEqual(items[0]["review_id"], "review_000000000000000000000400")
        self.assertEqual(items[-1]["review_id"], "review_000000000000000000000599")
        self.assertEqual(attempts, 6)
        self.assertEqual(sleep.call_count, 5)

    def test_successful_empty_page_is_not_retried(self) -> None:
        real_connect = sqlite3.connect
        with (
            patch.object(control_api.sqlite3, "connect", wraps=real_connect) as connect,
            patch.object(control_api.time, "sleep") as sleep,
        ):
            items, total = self._list()

        self.assertEqual((items, total), ([], 0))
        self.assertEqual(connect.call_count, 1)
        sleep.assert_not_called()

    def test_exhausted_transient_failures_remain_fail_closed(self) -> None:
        with (
            patch.object(
                control_api.sqlite3,
                "connect",
                side_effect=sqlite3.OperationalError("database is locked"),
            ) as connect,
            patch.object(control_api.time, "sleep") as sleep,
        ):
            items, total = self._list()

        self.assertEqual((items, total), ([], 0))
        self.assertEqual(connect.call_count, 6)
        self.assertEqual(sleep.call_count, 5)


if __name__ == "__main__":
    unittest.main()
