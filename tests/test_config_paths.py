# Offline registry discovery and private adapter tests.

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dispatcher_config as config


class ConfigPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = mock.patch.dict(os.environ, HOME=str(self.home), AGENT_USE_CONFIG="", XDG_CONFIG_HOME="")
        environment.start()
        self.addCleanup(environment.stop)
        self.user_config = self.home / ".config/agent-use/dispatcher.json"
        self.user_config.parent.mkdir(parents=True)
        self.private = self.home / "my-adapters"
        self.private.mkdir()
        self.registry = self.private / "dispatcher.json"
        self.registry.write_bytes(config.DEFAULT_CONFIG.read_bytes())

    def cli(self, script, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / script), *map(str, args)],
                              capture_output=True, text=True)

    def test_four_level_precedence(self):
        self.assertEqual(config.resolve_config_path(), config.DEFAULT_CONFIG.resolve())
        self.user_config.write_bytes(self.registry.read_bytes())
        self.assertEqual(config.resolve_config_path(), self.user_config.resolve())
        os.environ["AGENT_USE_CONFIG"] = "~/my-adapters/dispatcher.json"
        self.assertEqual(config.resolve_config_path(), self.registry.resolve())
        self.assertEqual(config.resolve_config_path(config.DEFAULT_CONFIG), config.DEFAULT_CONFIG.resolve())
        os.environ["AGENT_USE_CONFIG"] = str(self.home / "missing.json")
        self.assertEqual(config.resolve_config_path(config.DEFAULT_CONFIG), config.DEFAULT_CONFIG.resolve())
        with self.assertRaises(config.ConfigError):
            config.load_config(self.home / "explicit-missing.json")

    def test_xdg_lookup_precedence_and_invalid_values(self):
        xdg = self.home / "xdg"
        candidate = xdg / "agent-use/dispatcher.json"
        candidate.parent.mkdir(parents=True)
        self.user_config.write_bytes(self.registry.read_bytes())
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        # An absent XDG registry skips the legacy home location.
        self.assertEqual(config.resolve_config_path(), config.DEFAULT_CONFIG.resolve())
        candidate.write_bytes(self.registry.read_bytes())
        self.assertEqual(config.resolve_config_path(), candidate.resolve())
        reply = self.cli("agent_use.py", "list")
        self.assertEqual(json.loads(reply.stdout)["config_path"], str(candidate.resolve()))
        os.environ["AGENT_USE_CONFIG"] = str(self.registry)
        self.assertEqual(config.resolve_config_path(), self.registry.resolve())
        self.assertEqual(config.resolve_config_path(config.DEFAULT_CONFIG), config.DEFAULT_CONFIG.resolve())
        os.environ["AGENT_USE_CONFIG"] = ""
        for value in ("", "relative/path", "~/xdg"):
            os.environ["XDG_CONFIG_HOME"] = value
            self.assertEqual(config.resolve_config_path(), self.user_config.resolve())

    def test_broken_xdg_registry_symlink_is_error(self):
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "xdg")
        candidate = self.home / "xdg/agent-use/dispatcher.json"
        candidate.parent.mkdir(parents=True)
        candidate.symlink_to(self.home / "missing.json")
        with self.assertRaises(config.ConfigError):
            config.resolve_config_path()

    def test_missing_environment_config_never_falls_back(self):
        self.user_config.write_bytes(self.registry.read_bytes())
        os.environ["AGENT_USE_CONFIG"] = str(self.home / "missing.json")
        with self.assertRaises(config.ConfigError):
            config.load_config()
        result = self.cli("agent_use.py", "list")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["state"], "failed")

    def test_unreadable_environment_config_never_falls_back(self):
        os.environ["AGENT_USE_CONFIG"] = str(self.registry)
        with mock.patch.object(Path, "open", side_effect=PermissionError):
            with self.assertRaises(config.ConfigError):
                config.load_config()

    def test_broken_user_symlink_never_falls_back(self):
        self.user_config.symlink_to(self.home / "missing.json")
        with self.assertRaises(config.ConfigError):
            config.load_config()
        result = self.cli("agent_use.py", "list")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["state"], "failed")
        self.assertEqual(config.resolve_config_path(self.registry), self.registry.resolve())
        os.environ["AGENT_USE_CONFIG"] = str(self.registry)
        self.assertEqual(config.resolve_config_path(), self.registry.resolve())

    def test_symlink_config_dir_and_both_entry_points(self):
        registry = json.loads(self.registry.read_text())
        registry["providers"]["echo"]["command"] = ["{python}", "-B", "{config_dir}/adapter.py"]
        self.registry.write_text(json.dumps(registry))
        (self.private / "adapter.py").write_bytes((ROOT / "examples/echo_adapter.py").read_bytes())
        self.user_config.symlink_to(self.registry)
        command = config.resolve(config.load_config(), requested=["echo"])[0]["command"]
        self.assertEqual(command, [sys.executable, "-B", str(self.private.resolve() / "adapter.py")])
        for args, expected in [((), self.registry), (("--config", config.DEFAULT_CONFIG), config.DEFAULT_CONFIG)]:
            result = self.cli("agent_use.py", "list", *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["config_path"], str(expected.resolve()))
        task = self.home / "task.txt"
        task.write_text("hello")
        result = self.cli("run_agent.py", task, self.home / "result", "--expect-exact", "echo: hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual(status["state"], "staged_for_review")
        self.assertIn("config:adapter.py", status["attempts"][0]["adapter_versions"])
        self.user_config.unlink()
        os.environ["AGENT_USE_CONFIG"] = "~/my-adapters/dispatcher.json"
        result = self.cli("run_agent.py", task, self.home / "env-result", "--expect-exact", "echo: hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.cli("agent_use.py", "list")
        self.assertEqual(json.loads(result.stdout)["config_path"], str(self.registry.resolve()))


if __name__ == "__main__":
    unittest.main()
