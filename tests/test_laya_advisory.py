import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class LayaAdvisoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(app, "WORK_PATH", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.directory = self.root / "laya-diagnostics"
        self.directory.mkdir()

    def test_absence_does_not_create_admission_block(self):
        self.assertEqual(app._laya_advisory_summary(), {"status": "NOT_AVAILABLE"})
        self.assertFalse(app._ai_scheduler_summary()["admission_blocked"])

    def test_invalid_or_oversize_record_is_only_diagnostic_unavailable(self):
        for raw in ("{invalid", "x" * 65537, '{"advisory_only":false}',
                    '{"advisory_only":true,"model":{"choice":"CLEAR_BREAKER"}}'):
            (self.directory / "latest.json").write_text(raw)
            self.assertEqual(app._laya_advisory_summary()["status"], "UNAVAILABLE")
            self.assertFalse(app._ai_scheduler_summary()["admission_blocked"])

    def test_advice_cannot_release_existing_pause(self):
        (self.root / app.AI_SCHEDULER_STATE_NAME).write_text(json.dumps({"state": "paused", "reason_code": "safety_fault"}))
        (self.directory / "latest.json").write_text(json.dumps({"advisory_only": True, "status": "ADVISORY",
            "model": {"choice": "TRANSIENT"}, "raw_reason": "original failure", "created_at": 100,
            "evidence_ids": ["sha256:evidence"], "record_id": "record"}))
        result = app._ai_scheduler_summary()
        self.assertTrue(result["admission_blocked"])
        self.assertEqual(result["blocking_reason_code"], "safety_fault")
        self.assertEqual(result["laya_advisory"]["category"], "TRANSIENT")
        self.assertFalse(result["laya_advisory"]["calibrated"])

    def test_display_excerpt_is_explicit_and_record_unchanged(self):
        path = self.directory / "latest.json"
        path.write_text(json.dumps({"advisory_only": True, "status": "UNAVAILABLE", "raw_reason": "r" * 2000}))
        before = path.read_bytes()
        self.assertTrue(app._laya_advisory_summary()["raw_reason_truncated"])
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
