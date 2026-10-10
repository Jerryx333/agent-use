# Offline registry, protocol, environment, and concurrency tests.

import argparse
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import agent_use
import dispatcher_config as config
import provider_process
import run_agent as router


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, HOME=str(self.root), AGENT_USE_CONFIG="")
        environment.start()
        self.addCleanup(environment.stop)
        self.config = config.load_config()
        self.config["providers"]["second"] = copy.deepcopy(self.config["providers"]["echo"])
        self.config["providers"]["disabled-demo"] = {**copy.deepcopy(self.config["providers"]["echo"]), "enabled": False}
        self.config["providers"]["tool-demo"] = {**copy.deepcopy(self.config["providers"]["echo"]), "tool_policy": "guarded"}
        self.config["profiles"]["default"] = ["disabled-demo", "echo", "second"]
        self.config["profiles"]["disabled-demo-only"] = ["disabled-demo"]
        self.config["profiles"]["text-safe"] = ["echo", "second"]
        self.path = self.root / "config.json"
        self.save()
        self.task = self.root / "task.txt"
        self.task.write_text("draft")

    def save(self):
        self.path.write_text(json.dumps(self.config))

    def args(self, **values):
        return argparse.Namespace(task=str(self.task), output=str(self.root / "result"), config=str(self.path),
            order=None, profile=None, providers=None, models={}, timeout=None, expect_exact=None, require_no_tools=False, **values)

    def fake(self, source):
        adapter = self.root / "adapter.py"
        adapter.write_text(source)
        return [sys.executable, "-B", str(adapter)]

    def register_fake(self, source=None):
        source = source or 'import json,sys\nrequest=json.load(sys.stdin)\nprint(json.dumps({"protocol_version":1,"state":"staged_for_review","accepted":False,"response":request["model"]}))\n'
        self.config["providers"] = {"newagent": {"command": self.fake(source), "default_model": "custom-model", "tool_policy": "disabled", "timeout": 2}}
        self.config["profiles"] = {"default": ["newagent"]}
        self.save()

    def test_dangerous_environment_prefixes_rejected(self):
        for prefix in ("LD_", "DYLD_", "NODE_", "PYTHON", "BASH_", "L", "DY", "LD_EXTRA_"):
            with self.subTest(prefix=prefix):
                self.assertTrue(config.dangerous_environment_prefix(prefix))
                self.config["providers"]["echo"]["env_prefixes"] = [prefix]
                self.save()
                with self.assertRaises(config.ConfigError):
                    config.load_config(self.path)

    def test_runtime_environment_removes_dangerous_names(self):
        blocked = dict.fromkeys([*config.DANGEROUS_ENV_NAMES, "LD_PRELOAD", "DYLD_TEST"], "blocked")
        with mock.patch.dict(os.environ, {**blocked, "PYTHONUTF8": "1", "DEMO_VALUE": "ok"}, clear=True):
            actual = router.provider_environment({"env_prefixes": [""], "env_names": list(blocked)})
        self.assertEqual(actual, {"PYTHONUTF8": "1", "DEMO_VALUE": "ok"})

    def test_working_directory_changes_reuse_fingerprint(self):
        args = self.args()
        first = router.run(args)
        with mock.patch.object(router.Path, "cwd", return_value=self.root):
            with self.assertRaisesRegex(router.RouteError, "does not match"):
                router.run(args)
            args.output = str(self.root / "other-result")
            second = router.run(args)
        self.assertNotEqual(first["request_sha256"], second["request_sha256"])

    def test_huge_integer_timeout_is_config_error(self):
        with self.assertRaisesRegex(config.ConfigError, "timeout"):
            config.positive_timeout(10 ** 400)
        request = {"version": 1, "task": "hello", "timeout": 10 ** 400}
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/agent_use.py"), "run"],
                                input=json.dumps(request), capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["state"], "failed")
        self.assertNotIn("Traceback", result.stderr)
        self.config["providers"]["echo"]["timeout"] = 10 ** 400
        self.save()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/run_agent.py"),
                                 str(self.task), str(self.root / "result"), "--config", str(self.path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("timeout", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_top_level_overflow_is_reported_without_traceback(self):
        for module, argv in ((agent_use, ["agent_use.py", "list"]),
                             (router, ["run_agent.py", str(self.task), str(self.root / "result")])):
            with self.subTest(module=module.__name__):
                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch.object(sys, "argv", argv), mock.patch.object(module, "load_config", side_effect=OverflowError), \
                     mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
                    self.assertEqual(module.main(), 1)
                self.assertNotIn("Traceback", stderr.getvalue())
                if module is agent_use:
                    self.assertEqual(json.loads(stdout.getvalue())["state"], "failed")
                else:
                    self.assertIn("invalid routing input", stderr.getvalue())

    def test_request_file_read_is_bounded(self):
        limit = 4 * 1024 * 1024
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.read.return_value = b"x" * (limit + 1)
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["agent_use.py", "run", "--request", "request.json"]), \
             mock.patch.object(Path, "open", return_value=stream), mock.patch.object(sys, "stdout", stdout):
            self.assertEqual(agent_use.main(), 1)
        stream.read.assert_called_once_with(limit + 1)
        self.assertEqual(json.loads(stdout.getvalue())["message"], "request exceeds the 4 MiB limit")
        request = self.root / "request.json"
        request.write_bytes(b"x" * (limit + 1))
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/agent_use.py"), "run",
                                 "--request", str(request)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("4 MiB", json.loads(result.stdout)["message"])

    def test_environment_prefix_validation(self):
        for value in ("DEMO_", None, [""], ["demo_"], ["1DEMO"], ["A-B"], ["A" * 64 + "_"], ["A_"], ["PYTHON"], ["AB_CD_"], [True], ["DEMO_\n"]):
            with self.subTest(value=value):
                self.config["providers"]["echo"]["env_prefixes"] = value
                self.save()
                with self.assertRaises(config.ConfigError):
                    config.load_config(self.path)
        self.config["providers"]["echo"]["env_prefixes"] = ["AB_", "DEMO_", "A" * 63 + "_"]
        self.save()
        loaded = config.load_config(self.path)
        self.assertEqual(config.resolve(loaded, requested=["echo"])[0]["env_prefixes"], ["AB_", "DEMO_", "A" * 63 + "_"])

    def test_environment_name_validation(self):
        invalid = (None, "DEMO", [True], [""], ["lower"], ["1ABC"], ["A-B"],
                   ["A" * 65], ["ABC\n"], ["LD_PRELOAD"], ["DYLD_TEST"],
                   ["PYTHONPATH"], ["PYTHONSTARTUP"], ["NODE_OPTIONS"], ["BASH_ENV"], ["ENV"])
        for value in invalid:
            with self.subTest(value=value):
                self.config["providers"]["echo"]["env_names"] = value
                self.save()
                with self.assertRaises(config.ConfigError):
                    config.load_config(self.path)
        names = ["_", "A", "A" * 64, "EXACT_VALUE", "PYTHONUNBUFFERED"]
        self.config["providers"]["echo"]["env_names"] = names
        self.save()
        loaded = config.load_config(self.path)
        self.assertEqual(config.resolve(loaded, requested=["echo"])[0]["env_names"], names)
        entry = next(item for item in config.catalog(loaded)["providers"] if item["provider"] == "echo")
        self.assertEqual(entry["env_names"], names)

    def test_environment_prefixes_are_isolated_per_route(self):
        self.config["providers"]["echo"]["env_prefixes"] = ["DEMO_"]
        self.config["providers"]["echo"]["env_names"] = ["EXACT_VALUE"]
        routes = config.resolve(self.config, requested=["echo", "second"])
        environment = {"PATH": "test", "HOME": "test", "LC_ALL": "C", "PYTHONUNBUFFERED": "1",
                       "NODE_OPTIONS": "test", "AGENT_USE_TEST": "test", "DEMO_VALUE": "allowed",
                       "DEMOOTHER": "blocked", "OTHER_SECRET": "blocked",
                       "EXACT_VALUE": "allowed", "EXACT_VALUE_EXTRA": "blocked",
                       "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                       "PYTHONPATH": "blocked", "PYTHONSTARTUP": "blocked",
                       "BASH_ENV": "blocked", "ENV": "blocked", "LD_PRELOAD": "blocked",
                       "DYLD_INSERT_LIBRARIES": "blocked"}
        reply = {"protocol_version": 1, "accepted": False, "state": "failed", "reason": "example_failure"}
        with mock.patch.dict(os.environ, environment, clear=True):
            for route in routes:
                with mock.patch.object(router, "invoke", return_value=reply) as invoke:
                    router.attempt(route["provider"], self.task, self.root / "attempt", 2, None, route)
                expected = {key: value for key, value in environment.items()
                            if key in {"PATH", "HOME", "LC_ALL", "AGENT_USE_TEST",
                                       "PYTHONIOENCODING", "PYTHONUTF8"}}
                if route["provider"] == "echo":
                    expected["DEMO_VALUE"] = "allowed"
                    expected["EXACT_VALUE"] = "allowed"
                self.assertEqual(invoke.call_args.args[3], expected)

    def test_example_fallback_and_exact_validation(self):
        self.save()
        args = self.args()
        args.profile = "fallback-demo"
        args.expect_exact = "echo: draft"
        status = router.run(args)
        self.assertEqual([record["provider"] for record in status["attempts"]], ["fail-demo", "echo"])
        self.assertEqual(status["attempts"][0]["reason"], "example_failure")
        self.assertEqual((self.root / "result/response.txt").read_text(), "echo: draft")
        args.output = str(self.root / "mismatch")
        args.expect_exact = "different"
        status = router.run(args)
        self.assertEqual(status["reason"], "all_providers_failed")
        self.assertEqual(status["attempts"][-1]["reason"], "validation_failed")

    def test_only_declared_options_can_be_overridden(self):
        for entry in ({"provider": "echo", "options": {"unknown": "x"}},
                      {"provider": "fail-demo", "options": {"prefix": "x"}}):
            with self.assertRaises(config.ConfigError):
                config.resolve(self.config, requested=[entry])
        routes = config.resolve(self.config, requested=["echo"])
        router.apply_options(routes, ["echo.prefix="])
        self.assertEqual(routes[0]["options"], {"prefix": ""})
        self.assertEqual(self.config["providers"]["echo"]["options"], {"prefix": "echo: "})
        self.assertEqual(config.resolve(self.config, requested=["echo"])[0]["options"], {"prefix": "echo: "})

    def test_example_cli_option_is_used_by_adapter(self):
        self.save()
        command = [sys.executable, "-B", str(ROOT / "scripts/run_agent.py"), str(self.task),
                   str(self.root / "cli-example"), "--config", str(self.path), "--order", "echo",
                   "--option", "echo.prefix=custom: "]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "cli-example/response.txt").read_text(), "custom: draft")

    def test_credential_labels_are_filtered(self):
        for value in ("example-label", "unsafe\nlabel", 42, "x" * 129):
            error = RuntimeError("private value")
            error.credential = value
            expected = value if value == "example-label" else "adapter credential (safe name unavailable)"
            self.assertEqual(router.missing_credential(error), expected)
            reply = {"protocol_version": 1, "accepted": False, "state": "failed",
                     "reason": "credential_missing", "missing_credential": value}
            with mock.patch.object(router, "invoke", return_value=reply):
                record, _, fatal = router.attempt("echo", self.task, self.root / "attempt", 2, None)
            self.assertEqual(record["missing_credential"], expected)
            self.assertTrue(fatal)

    def test_one_two_or_three_providers_and_independent_models(self):
        self.config["providers"]["disabled-demo"]["enabled"] = True
        for selected in [["echo"], ["second", "disabled-demo"], ["echo", "second", "disabled-demo"]]:
            routes = config.resolve(self.config, requested=selected, models={name: name + "-chosen" for name in selected})
            self.assertEqual([route["provider"] for route in routes], selected)
            self.assertEqual([route["model"] for route in routes], [name + "-chosen" for name in selected])

    def test_disabled_provider_skipped_in_profile_and_explicit_request_rejected(self):
        self.config["providers"]["disabled-demo"]["enabled"] = False
        self.assertEqual([route["provider"] for route in config.resolve(self.config)], ["echo", "second"])
        with self.assertRaises(config.ConfigError):
            config.resolve(self.config, requested=["disabled-demo"])

    def test_disabled_demo_explicit_cli_records_status_without_adapter(self):
        self.save()
        for option in ("--order", "--profile"):
            output = self.root / option[2:]
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/run_agent.py"),
                str(self.task), str(output), "--config", str(self.path), option,
                "disabled-demo" if option == "--order" else "disabled-demo-only"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            status = json.loads((output / "status.json").read_text())
            self.assertEqual(status["reason"], "provider_disabled")
            self.assertFalse(status["fatal"])
            self.assertEqual(status["attempts"], [])
            self.assertFalse((output / "attempts").exists())
        args = self.args()
        args.order = "disabled-demo"
        with mock.patch.object(router, "invoke", side_effect=AssertionError("adapter must not start")):
            self.assertEqual(router.run(args)["reason"], "provider_disabled")

    def test_disabled_demo_json_run_and_validate(self):
        for action in ("validate", "run"):
            output = self.root / action
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/agent_use.py"), action, "--config", str(self.path)],
                input=json.dumps({"version": 1, "task": "offline", "profile": "disabled-demo-only", "output": str(output)}),
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            reply = json.loads(result.stdout)
            self.assertEqual(reply["reason"], "provider_disabled")
            self.assertFalse(reply["fatal"])
            self.assertEqual(output.exists(), action == "run")
            if action == "run":
                self.assertEqual(json.loads((output / "status.json").read_text())["attempts"], [])

    def test_disabled_rejection_preserves_existing_output(self):
        output = self.root / "existing"
        output.mkdir()
        status = output / "status.json"
        status.write_text("existing result")
        with self.assertRaises(FileExistsError):
            config.disabled_status(output)
        self.assertEqual(status.read_text(), "existing result")


    def test_new_provider_needs_no_router_patch(self):
        self.register_fake()
        args = self.args()
        status = router.run(args)
        self.assertEqual(status["selected_provider"], "newagent")
        self.assertEqual((Path(args.output) / "response.txt").read_text(), "custom-model")
        self.assertFalse(status["accepted"])
        with mock.patch.object(router, "invoke", side_effect=AssertionError("must not resend")):
            router.run(args)
        args.models = {"newagent": "another-model"}
        with self.assertRaises(router.RouteError):
            router.run(args)

    def test_failure_detail_reaches_record_and_stderr(self):
        self.register_fake()
        for index, detail in enumerate(("workspace_prepare\n\x00" + "x" * 300, 123, None)):
            with self.subTest(detail=detail):
                reply = {"protocol_version": 1, "state": "failed", "accepted": False,
                         "reason": "extension_command_failed", "detail": detail}
                args = self.args()
                args.output = str(self.root / f"detail-{index}")
                stderr = io.StringIO()
                with mock.patch.object(router, "invoke", return_value=reply), mock.patch.object(router.sys, "stderr", stderr):
                    status = router.run(args)
                record = status["attempts"][0]
                message = "agent-use: newagent failed (extension_command_failed)"
                if isinstance(detail, str):
                    expected = ("workspace_prepare " + "x" * 300)[:240]
                    self.assertEqual(record["detail"], expected)
                    message += ": " + expected
                else:
                    self.assertNotIn("detail", record)
                self.assertIn(message + "\n", stderr.getvalue())
                self.assertEqual(json.loads((Path(args.output) / "status.json").read_text())["attempts"][0], record)

    def test_adapter_code_change_invalidates_replay(self):
        self.register_fake()
        router.run(self.args())
        adapter = self.root / "adapter.py"
        adapter.write_text(adapter.read_text() + '\n')
        with self.assertRaises(router.RouteError):
            router.run(self.args())

    def test_custom_failure_then_example_success_and_single_route_credential_stops(self):
        self.register_fake()
        self.config["providers"]["echo"] = config.load_config()["providers"]["echo"]
        self.config["profiles"]["default"].append("echo")
        self.save()
        failed = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": "timeout", "fatal": False}
        success = {"protocol_version": 1, "state": "staged_for_review", "accepted": False, "response": "done"}
        with mock.patch.object(router, "invoke", side_effect=[failed, success]):
            self.assertEqual(router.run(self.args())["selected_provider"], "echo")
        # A single-route credential failure preserves its reason for the caller.
        args = self.args()
        args.providers = ["newagent"]
        args.output = str(self.root / "single")
        failed.update(reason="credential_missing", fatal=False)
        with mock.patch.object(router, "invoke", return_value=failed) as invoke:
            self.assertEqual(router.run(args)["reason"], "credential_missing")
            invoke.assert_called_once()

    def test_credential_missing_skips_in_multi_route_but_interrupt_aborts(self):
        self.register_fake()
        self.config["providers"]["echo"] = config.load_config()["providers"]["echo"]
        self.config["profiles"]["default"].append("echo")
        self.save()
        missing = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": "credential_missing", "fatal": False}
        success = {"protocol_version": 1, "state": "staged_for_review", "accepted": False, "response": "done"}
        # A credential failure advances to the next configured route.
        with mock.patch.object(router, "invoke", side_effect=[missing, success]) as invoke:
            status = router.run(self.args())
            self.assertEqual(status["selected_provider"], "echo")
            self.assertEqual(invoke.call_count, 2)
        # Credential failures throughout the chain fail on exhaustion.
        args = self.args()
        args.output = str(self.root / "allmissing")
        with mock.patch.object(router, "invoke", return_value=missing):
            self.assertEqual(router.run(args)["reason"], "all_providers_failed")
        # Interruption stops without trying later providers.
        args = self.args()
        args.output = str(self.root / "interrupted")
        interrupted = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": "interrupted", "fatal": True}
        with mock.patch.object(router, "invoke", side_effect=[interrupted, success]) as invoke:
            self.assertEqual(router.run(args)["reason"], "interrupted")
            invoke.assert_called_once()

    def test_same_output_lock_rejects_concurrent_request(self):
        self.register_fake()
        lock = os.open(self.root / ".result.agent-use.lock", os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with mock.patch.object(router, "invoke") as invoke:
            with self.assertRaises(router.RouteError):
                router.run(self.args())
            invoke.assert_not_called()

    def test_config_mistakes_fail_before_generation(self):
        for mutation in [lambda value: value.update(version=True),
                         lambda value: value["providers"]["second"].update(options={"api_key": "never-store"}),
                         lambda value: value["providers"]["second"].update(command="shell command"),
                         lambda value: value["providers"].pop("second"),
                         lambda value: value["providers"]["second"].update(timeout=float("nan"))]:
            original = copy.deepcopy(self.config)
            mutation(self.config)
            self.save()
            with self.assertRaises(config.ConfigError):
                config.load_config(self.path)
            self.config = original

    def test_no_tools_is_explicit_not_silent_filter(self):
        self.assertEqual([route["provider"] for route in config.resolve(self.config, require_no_tools=True)],
                         ["echo", "second"])
        with self.assertRaises(config.ConfigError):
            config.resolve(self.config, requested=["tool-demo"], require_no_tools=True)
        self.assertEqual(len(config.resolve(self.config, profile="text-safe", require_no_tools=True)), 2)


    def test_entry_options_model_and_timeout_override(self):
        route = config.resolve(self.config, requested=[{"provider": "echo", "model": "first", "timeout": 9, "options": {"prefix": "custom: "}}], models={"echo": "second"})[0]
        self.assertEqual((route["model"], route["timeout"], route["options"]),
                         ("second", 9, {"prefix": "custom: "}))




    def test_json_cli_discovery_validation_generation_and_replay(self):
        self.register_fake()
        command = [sys.executable, '-B', str(ROOT / 'scripts/agent_use.py')]
        catalog = subprocess.run(command + ['list', '--config', str(self.path)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(catalog.stdout)['providers'][0]['provider'], 'newagent')
        request = {"version": 1, "task": "draft", "output": str(self.root / "result"), "providers": [{"provider": "newagent", "model": "my-choice"}]}
        for action in ['validate', 'run', 'run']:
            reply = subprocess.run(command + [action, '--config', str(self.path)], input=json.dumps(request), capture_output=True, text=True, check=True)
            data = json.loads(reply.stdout)
            self.assertEqual(data['state'], 'valid' if action == 'validate' else 'staged_for_review')
            if action == 'run':
                self.assertEqual(data['response'], 'my-choice')
        self.assertFalse((self.root / 'result').joinpath('accepted').exists())

    def test_bad_request_is_structured_and_cannot_inject_command(self):
        for request in [{"version": 1, "task": "draft", "command": ["bad"]},
                        {"version": 1, "task": "draft", "models": []},
                        {"version": 1, "task": "draft", "require_no_tools": "yes"},
                        {"version": 1, "task": "draft", "providers": []}]:
            with self.assertRaises(config.ConfigError):
                agent_use.request_options(request, None)



    def test_echo_model_attempt_metadata_is_preserved(self):
        reply = {'protocol_version': 1, 'state': 'staged_for_review', 'accepted': False, 'response': 'draft',
                 'metadata': {'selected_model': 'model-2', 'model_switch_count': 1,
                              'model_attempts': [{'requested_model': 'model-1', 'state': 'failed', 'reason': 'quota_exhausted'},
                                                 {'requested_model': 'model-2', 'state': 'staged_for_review'}]}}
        with mock.patch.object(router, 'invoke', return_value=reply):
            record, content, fatal = router.attempt('echo', self.task, self.root / 'attempt', 2, None)
        self.assertEqual(record['selected_model'], 'model-2')
        self.assertEqual(record['model_switch_count'], 1)
        self.assertEqual(len(record['model_attempts']), 2)
        self.assertEqual(content, 'draft')
        self.assertFalse(fatal)


    def test_option_overrides_and_empty_value_reach_request_and_status(self):
        self.save()
        for index, value in enumerate(['model-3', '']):
            args = self.args(option=['echo.prefix=' + value])
            args.providers = ['echo']
            args.output = str(self.root / f'override-{index}')
            reply = {'protocol_version': 1, 'state': 'staged_for_review', 'accepted': False, 'response': 'draft',
                     'metadata': {'credits': 0, 'model_attempts': [{'state': 'staged_for_review', 'credits': 0}]}}
            with mock.patch.object(router, 'invoke', return_value=reply) as invoke:
                status = router.run(args)
            expected = {'prefix': value}
            self.assertEqual(invoke.call_args.args[1]['options'], expected)
            record = status['attempts'][0]
            self.assertEqual(record['options'], expected)
            self.assertEqual(record['credits'], 0)
            self.assertEqual(record['model_attempts'][0]['credits'], 0)

    def test_option_cli_parsing(self):
        self.save()
        argv = ['run_agent.py', str(self.task), str(self.root / 'cli'), '--config', str(self.path),
                '--order', 'echo', '--option', 'echo.prefix=']
        reply = {'protocol_version': 1, 'state': 'staged_for_review', 'accepted': False, 'response': 'draft'}
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(router, 'invoke', return_value=reply) as invoke, \
                mock.patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(router.main(), 0)
        self.assertEqual(invoke.call_args.args[1]['options'], {'prefix': ''})

    def test_invalid_options_fail_before_request(self):
        self.save()
        for values in [['echo.bad=x'], ['second.effort=low'], ['echo=low'],
                       ['echo.effort'], ['.effort=x'], ['echo.=x'],
                       ['echo.effort=low', 'echo.effort=high']]:
            args = self.args(option=values)
            args.providers = ['echo']
            with mock.patch.object(router, 'invoke') as invoke:
                with self.assertRaises(router.RouteError):
                    router.run(args)
                invoke.assert_not_called()
        command = [sys.executable, '-B', str(ROOT / 'scripts/run_agent.py'),
                   str(self.task), str(self.root / 'invalid'), '--order', 'echo', '--option', 'echo.bad=x']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / 'invalid').exists())


    def test_models_do_not_leak_between_requests(self):
        first = config.resolve(self.config, requested=["second"], models={"second": "first"})
        second = config.resolve(self.config, requested=["second"])
        self.assertEqual(first[0]["model"], "first")
        self.assertEqual(second[0]["model"], "echo-1")

    def test_nonzero_success_and_oversized_unread_stdin_are_rejected(self):
        success = 'import json,sys\nprint(json.dumps({"state":"staged_for_review"}))\nsys.exit(1)'
        with self.assertRaises(provider_process.ProcessError) as raised:
            provider_process.invoke(self.fake(success), {}, 3)
        self.assertEqual(raised.exception.category, 'adapter_failed')
        with self.assertRaises(provider_process.ProcessError) as raised:
            provider_process.invoke(self.fake('import time\ntime.sleep(10)'), {'task': 'x' * 200000}, .3)
        self.assertEqual(raised.exception.category, 'timeout')


    def test_process_timeout_invalid_json_and_output_limit(self):
        for source, category in [('import time\ntime.sleep(10)', 'timeout'), ('print("private-secret")', 'invalid_protocol'),
                                 ('print("x"*5000000)', 'output_limit')]:
            with self.assertRaises(provider_process.ProcessError) as raised:
                provider_process.invoke(self.fake(source), {}, .4)
            self.assertEqual(raised.exception.category, category)
            self.assertNotIn('private-secret', str(raised.exception))

    def test_bad_protocol_and_raw_failure_reason_rejected(self):
        for reply in [{"protocol_version": 2}, {"protocol_version": 1, "accepted": True},
                      {"protocol_version": 1, "accepted": False, "state": "failed", "reason": "Bearer private-secret"}]:
            with mock.patch.object(router, 'invoke', return_value=reply):
                record, content, fatal = router.attempt('echo', self.task, self.root / 'attempt', 2, None)
            self.assertEqual(record['reason'], 'invalid_protocol')
            self.assertIsNone(content)
            self.assertNotIn('private-secret', json.dumps(record))

    def test_outer_interrupt_reaps_term_ignoring_cli_target(self):
        marker = self.root / "target.pid"
        source = ('import os,signal,time; from pathlib import Path; '
                  'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                  f'p=Path({str(marker)!r}); t=p.with_suffix(".tmp"); '
                  't.write_text(str(os.getpid())); t.replace(p); time.sleep(30)')
        self.config["providers"]["echo"]["command"] = [
            "{python}", "-B", "{repo}/examples/cli_adapter.py", "--",
            sys.executable, "-B", "-c", source]
        self.save()
        request = {"version": 1, "task": "hello", "providers": ["echo"],
                   "output": str(self.root / "result")}
        process = subprocess.Popen([sys.executable, "-B", str(ROOT / "scripts/agent_use.py"),
                                    "run", "--config", str(self.path)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            process.stdin.write(json.dumps(request))
            process.stdin.close()
            process.stdin = None
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists())
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(json.loads(stdout)["reason"], "interrupted")
            self.assertNotIn("Traceback", stderr)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
            if marker.exists():
                try:
                    os.kill(int(marker.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_sigterm_leaves_failed_state_and_cleans_adapter(self):
        self.register_fake('import json,os,sys,time\nfrom pathlib import Path\nrequest=json.load(sys.stdin)\nmarker=Path(request["task_file"]).parent.joinpath("adapter.pid")\ntemporary=marker.with_suffix(".tmp")\ntemporary.write_text(str(os.getpid()))\ntemporary.replace(marker)\ntime.sleep(30)\n')
        request = {'version': 1, 'task': 'draft', 'output': str(self.root / 'result')}
        process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/agent_use.py'), 'run', '--config', str(self.path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            process.stdin.write(json.dumps(request))
            process.stdin.close()
            process.stdin = None
            marker = self.root / 'result/adapter.pid'
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists())
            pid = int(marker.read_text())
            process.send_signal(signal.SIGTERM)
            stdout, _stderr = process.communicate(timeout=8)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(json.loads(stdout)['reason'], 'interrupted')
            self.assertEqual(json.loads((self.root / 'result/status.json').read_text())['state'], 'failed')
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()


if __name__ == '__main__':
    unittest.main()
