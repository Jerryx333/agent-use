import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_agent
import trajectory

class TrajectoryTests(unittest.TestCase):
    def test_validation_limits_and_write_failure(self):
        good = {"ts": "2026-11-20T00:00:00+07:00", "tool": "read", "input": "文" * 2001, "edit": False}
        bad = [None, {}, dict(good, ts=True), dict(good, ts=float("nan")), dict(good, ts=-1),
               dict(good, ts="yesterday"), dict(good, tool="x " ), dict(good, input={}), dict(good, edit=1)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = {}
            run_agent.write_trajectory(root, {"trajectory": bad + [good] * 2001}, record)
            path = Path(record["transcript_path"])
            self.assertTrue(path.is_absolute())
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertLess(len(rows), 2000)
            self.assertLessEqual(len(json.dumps(rows).encode()), 1024 * 1024)
            self.assertTrue(record["trajectory_truncated"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(rows[0]["input"]), 2000)
            self.assertFalse(rows[0]["edit"])
            for value in (None, {}, [], bad):
                record = {}
                run_agent.write_trajectory(root, {"trajectory": value}, record)
                self.assertNotIn("transcript_path", record)
            with mock.patch.object(trajectory.os, "replace", side_effect=OSError("full")):
                record = {}
                run_agent.write_trajectory(root, {"trajectory": [good]}, record)
                self.assertNotIn("transcript_path", record)

    def test_success_failure_and_write_error_preserve_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            route = {"model": "test", "options": {}, "command": ["unused"]}
            for state in ("staged_for_review", "failed"):
                reply = {"protocol_version": 1, "accepted": False, "state": state,
                         "response": "unchanged", "reason": "timeout",
                         "metadata": {"trajectory": [{"ts": 0, "tool": "read", "input": "a"}]}}
                with mock.patch.object(run_agent, "invoke", return_value=reply):
                    record, content, fatal = run_agent.attempt("test", root / "task", root, 1, None, route)
                    self.assertEqual(record["state"], state)
                    self.assertIn("transcript_path", record)
                    self.assertEqual(content, "unchanged" if state == "staged_for_review" else None)
                    with mock.patch.object(trajectory.os, "replace", side_effect=OSError("full")):
                        record, content, fatal = run_agent.attempt("test", root / "task", root, 1, None, route)
                        self.assertEqual(record["state"], state)
                        self.assertNotIn("transcript_path", record)

    def test_symlink_directory_target_and_old_temporary(self):
        good = {"ts": 0, "tool": "read", "input": "a"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim"
            victim.write_text("untouched")
            attempt = root / "attempt"
            attempt.mkdir()
            (attempt / "trajectory.jsonl.tmp").symlink_to(victim)
            (attempt / "trajectory.jsonl").symlink_to(victim)
            record = {}
            run_agent.write_trajectory(attempt, {"trajectory": [good]}, record)
            self.assertEqual(victim.read_text(), "untouched")
            self.assertFalse((attempt / "trajectory.jsonl").is_symlink())
            linked = root / "linked"
            linked.symlink_to(attempt, target_is_directory=True)
            record = {}
            run_agent.write_trajectory(linked, {"trajectory": [good]}, record)
            self.assertNotIn("transcript_path", record)

    def test_utc_and_invalid_times_and_rejected(self):
        times = ["2026-11-20T07:00:00.123+07:00", "2026-11-20T00:00:00.123Z", 0,
                 "2026-11-20T00:00:00", "2026-11-20T00:00Z", 1e300, True, float("inf")]
        rows = trajectory.Trajectory({"ts": t, "tool": "read", "input": "a", "rejected": True} for t in times)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["ts"], "2026-11-20T00:00:00.123+00:00")
        self.assertEqual(rows[0], rows[1])
        self.assertTrue(rows[0]["rejected"])

    def test_large_protocol_reply_remains_success(self):
        from provider_process import invoke
        import os
        payload = trajectory.Trajectory({"ts": 0, "tool": "read", "input": "文" * 2000} for _ in range(2000))
        reply = {"protocol_version": 1, "accepted": False, "state": "staged_for_review",
                 "response": "unchanged", "metadata": payload.metadata()}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "adapter.py"
            script.write_text("import json,sys\njson.load(sys.stdin)\nprint(" + repr(json.dumps(reply)) + ")\n")
            route = {"model": "test", "options": {}, "command": [sys.executable, str(script)]}
            record, text, fatal = run_agent.attempt("test", root / "task", root, 5, None, route)
            self.assertEqual(record["state"], "staged_for_review")
            self.assertEqual(text, "unchanged")
            self.assertTrue(record["trajectory_truncated"])
            self.assertIn("transcript_path", record)
