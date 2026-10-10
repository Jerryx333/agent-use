# Offline fallback, credential, validation, and reuse tests.

import argparse
import contextlib
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_agent.py"
SPEC = importlib.util.spec_from_file_location("run_agent", SCRIPT)
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        environment = mock.patch.dict(os.environ, HOME=str(self.root), AGENT_USE_CONFIG="")
        environment.start()
        self.addCleanup(environment.stop)
        self.task = self.root / "task.txt"
        self.task.write_text("Small draft", encoding="utf-8")
        self.output = self.root / "run"
        config = router.load_config()
        specification = config["providers"]["echo"]
        config["providers"] = {name: dict(specification) for name in ("first", "second", "third")}
        config["profiles"] = {"default": ["first", "second", "third"]}
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps(config))
        self.routes = {route["provider"]: route for route in router.resolve(config)}
        self.args = argparse.Namespace(task=str(self.task), output=str(self.output),
                                       config=str(self.config), order="first,second,third", timeout=20, expect_exact=None)

    def execute(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return router.run(self.args)


    def test_status_versions_cover_failed_and_successful_attempts(self):
        def attempt(provider, *arguments):
            if provider == "first":
                return {"provider": provider, "state": "failed", "reason": "timeout"}, None, False
            return {"provider": provider, "state": "staged_for_review"}, "draft", False
        with mock.patch.object(router, "attempt", side_effect=attempt):
            status = self.execute()
        for name, sha in status["scheduler_versions"].items():
            self.assertNotIn("/", name)
            self.assertEqual(sha, hashlib.sha256((SCRIPT.parent / name).read_bytes()).hexdigest())
        adapter = SCRIPT.parents[1] / "examples/echo_adapter.py"
        for record in status["attempts"]:
            versions = record["adapter_versions"]
            self.assertEqual(versions["examples/echo_adapter.py"], hashlib.sha256(adapter.read_bytes()).hexdigest())
            self.assertTrue(all(not Path(key).is_absolute() for key in versions))
        self.assertEqual(status, json.loads((self.output / "status.json").read_text()) | {"order": status["order"]})
        # Simulate an older successful status: reuse must not backfill fields.
        del status["scheduler_versions"]
        for record in status["attempts"]:
            del record["adapter_versions"]
        router.write_status(self.output, status)
        with mock.patch.object(router, "attempt") as called:
            replay = self.execute()
        called.assert_not_called()
        self.assertNotIn("scheduler_versions", replay)
        self.assertTrue(all("adapter_versions" not in record for record in replay["attempts"]))

    def test_version_keys_are_portable(self):
        config_dir = self.root.resolve() / "registry"
        config_dir.mkdir()
        self.assertEqual(router.version_key(SCRIPT, config_dir), "scripts/run_agent.py")
        self.assertEqual(router.version_key(config_dir / "adapter.py", config_dir), "config:adapter.py")
        self.assertEqual(router.version_key(config_dir / "../scripts/x.py", config_dir), "config:../scripts/x.py")
        self.assertEqual(router.version_key(self.root / "external.py", config_dir), "external.py")
        # Core ownership wins even if the registry also contains the source.
        self.assertEqual(router.version_key(SCRIPT, SCRIPT.parent), "scripts/run_agent.py")
        link = config_dir / "linked.py"
        link.symlink_to(SCRIPT)
        self.assertEqual(router.version_key(link, config_dir), "scripts/run_agent.py")

    def test_non_object_existing_status_is_rejected(self):
        self.output.mkdir()
        for status in ([], None, "status", 1, True):
            with self.subTest(status=status):
                (self.output / "status.json").write_text(json.dumps(status), encoding="utf-8")
                with mock.patch.object(router, "attempt") as attempt:
                    with self.assertRaisesRegex(router.RouteError, "^existing directory does not match or was unsuccessful; use a new directory, no automatic resend$"):
                        self.execute()
                attempt.assert_not_called()

    def test_success_stops_fallback_and_replay_never_calls_again(self):
        record = {"provider": "first", "state": "staged_for_review"}
        with mock.patch.object(router, "attempt", return_value=(record, "draft", False)) as attempt:
            status = self.execute()
            self.execute()
            attempt.assert_called_once()
        self.assertEqual(status["selected_provider"], "first")
        self.assertFalse(status["accepted"])

    def test_each_provider_can_be_the_successful_backup(self):
        for winner in ("first", "second", "third"):
            with self.subTest(winner=winner):
                self.args.output = str(self.root / winner)
                calls = []
                def attempt(provider, *arguments):
                    calls.append(provider)
                    if provider == winner:
                        return {"provider": provider, "state": "staged_for_review"}, "draft", False
                    return {"provider": provider, "state": "failed", "reason": "timeout"}, None, False
                with mock.patch.object(router, "attempt", side_effect=attempt):
                    status = self.execute()
                self.assertEqual(status["selected_provider"], winner)
                self.assertEqual(calls, list(("first", "second", "third")[:("first", "second", "third").index(winner) + 1]))

    def test_order_can_start_with_any_provider(self):
        self.args.order = "third,first,second"
        with mock.patch.object(router, "attempt", return_value=(
                {"provider": "third", "state": "staged_for_review"}, "draft", False)) as attempt:
            self.execute()
        self.assertEqual(attempt.call_args.args[0], "third")

    def test_credential_missing_continues_in_multi_route_but_stops_single(self):
        missing = ({"provider": "first", "state": "failed", "reason": "credential_missing",
                    "missing_credential": "first-tools-api-key"}, None, True)
        # Multi-route credential failures continue; exhaustion reports all_providers_failed.
        with mock.patch.object(router, "attempt", return_value=missing) as attempt:
            status = self.execute()
        self.assertEqual(attempt.call_count, 3)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["reason"], "all_providers_failed")
        # A single-route credential failure preserves its reason for the caller.
        self.args.order = "first"
        self.args.output = str(self.root / "single")
        with mock.patch.object(router, "attempt", return_value=missing) as attempt:
            status = self.execute()
        attempt.assert_called_once()
        self.assertEqual(status["reason"], "credential_missing")

    def test_all_fail_not_retried_or_returned_as_success(self):
        with mock.patch.object(router, "attempt", return_value=(
                {"state": "failed", "reason": "provider_failed"}, None, False)) as attempt:
            status = self.execute()
            self.assertEqual(status["reason"], "all_providers_failed")
            with self.assertRaises(router.RouteError):
                self.execute()
        self.assertEqual(attempt.call_count, 3)

    def test_tampering_or_changed_order_rejected(self):
        with mock.patch.object(router, "attempt", return_value=(
                {"state": "staged_for_review"}, "draft", False)):
            self.execute()
        self.args.order = "second,third"
        with self.assertRaises(router.RouteError):
            self.execute()
        self.args.order = "first,second,third"
        (self.output / "response.txt").write_text("changed")
        with self.assertRaises(router.RouteError):
            self.execute()

    def test_invalid_order_and_timeout_fail_before_output(self):
        for order, timeout in [("unknown", 20), ("third,third", 20), ("third", 0)]:
            self.args.order, self.args.timeout = order, timeout
            with self.assertRaises(router.RouteError):
                self.execute()
            self.assertFalse(self.output.exists())

    def test_adapter_exception_is_redacted_and_fatal_credentials_are_classified(self):
        for message, reason, fatal in [("upstream secret-value", "provider_failed", False),
                                       ("missing credential entry: test-key", "credential_missing", True),
                                       ("invocation timeout", "timeout", False)]:
            error = RuntimeError(message)
            error.category = reason
            with mock.patch.object(router, "invoke", side_effect=error):
                record, content, stop = router.attempt("first", self.task, self.output, 20, None, self.routes["first"])
            self.assertEqual(record["reason"], reason)
            self.assertEqual(stop, fatal)
            self.assertIsNone(content)
            self.assertNotIn("secret-value", json.dumps(record))

    def test_invalid_state_or_wrong_content_cannot_be_selected(self):
        for state, content, expected in [("failed", "draft", None),
                                         ("staged_for_review", "", None),
                                         ("staged_for_review", "wrong", "right")]:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "attempt"
                reply = {"protocol_version": 1, "state": state, "accepted": False, "response": content,
                         "reason": "provider_failed"}
                with mock.patch.object(router, "invoke", return_value=reply):
                    record, content, fatal = router.attempt("first", self.task, output, 20, expected, self.routes["first"])
                self.assertEqual(record["state"], "failed")
                self.assertIsNone(content)
                self.assertFalse(fatal)

    def test_third_uses_text_contract_and_per_provider_timeout(self):
        output = self.root / "third"
        reply = {"protocol_version": 1, "state": "staged_for_review", "accepted": False, "response": "draft", "metadata": {"credits": 0}}
        with mock.patch.object(router, "invoke", return_value=reply) as invoke:
            record, content, fatal = router.attempt("third", self.task, output, 13, "draft", self.routes["third"])
        request = invoke.call_args.args[1]
        self.assertEqual(request["timeout"], 13)
        self.assertEqual(request["expect_exact"], "draft")
        self.assertEqual(Path(request["task_file"]).read_text(), "Small draft")
        self.assertEqual(content, "draft")
        self.assertEqual(record["requested_model"], "echo-1")
        self.assertEqual(record["credits"], 0)
        self.assertFalse(fatal)

    def test_connection_failure_is_not_falsely_reported_as_timeout(self):
        error = RuntimeError("Connection failed")
        error.category = "connection_or_timeout"
        reason, fatal = router.classify_failure(error)
        self.assertEqual(reason, "connection_or_timeout")
        self.assertFalse(fatal)

    def test_interruption_never_falls_back(self):
        with mock.patch.object(router, "invoke", side_effect=KeyboardInterrupt):
            record, content, fatal = router.attempt("second", self.task, self.output, 20, None, self.routes["first"])
        self.assertEqual(record["reason"], "interrupted")
        self.assertIsNone(content)
        self.assertTrue(fatal)


    def test_categories_are_validated_and_preserved(self):
        categories = ("timeout", "authentication_failed", "admin_authentication_failed",
                      "native_cli_missing", "quota_exhausted", "no_eligible_account",
                      "model_unavailable", "external_session_active",
                      "switch_verification_failed", "unexpected_tool_use",
                      "connection_or_json_failed", "connection_or_timeout",
                      "new_provider_category", "a", "a" * 64)
        for category in categories:
            with self.subTest(category=category):
                error = RuntimeError("credential unavailable")
                error.category = category
                self.assertEqual(router.classify_failure(error), (category, False))
        for category in (None, "", "Timeout", "a-b", "a\n", "1abc", "a" * 65, 3, [], {}):
            with self.subTest(category=category):
                error = RuntimeError("missing credential entry: first-tools-admin; authentication failed; timeout")
                error.category = category
                self.assertEqual(router.classify_failure(error), ("provider_failed", False))
                self.assertIsNone(router.missing_credential(error))

    def test_message_alone_does_not_classify_failure(self):
        for message in ("missing credential entry", "valid credential entry required", "missing credential capability",
                        "authentication failed", "connection, timeout", "timeout", "first-tools-admin"):
            error = RuntimeError(message)
            self.assertEqual(router.classify_failure(error), ("provider_failed", False))
            self.assertIsNone(router.missing_credential(error))

    def test_both_interrupt_types_are_fatal_even_with_nonfatal_category(self):
        for kind in (KeyboardInterrupt, SystemExit):
            for category in (None, "timeout", "new_category"):
                error = kind("Stopped")
                error.category = category
                self.assertEqual(router.classify_failure(error), ("interrupted", True))


    def test_native_categories_preserved_and_keychain_pending_stops(self):
        for category, fatal in [("keychain_authorization_pending", True), ("quota_exhausted", False),
                                ("native_cli_missing", False), ("admin_authentication_failed", False)]:
            error = RuntimeError("redacted")
            error.category = category
            self.assertEqual(router.classify_failure(error), (category, fatal))


if __name__ == "__main__":
    unittest.main()
