# Offline early-stdin-close and process-group cleanup tests.

import errno
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import provider_process


class ProviderProcessTests(unittest.TestCase):
    def test_stop_reaps_leader_when_exit_is_not_yet_visible_after_eperm(self):
        process = mock.Mock(pid=123, poll=mock.Mock(return_value=None))
        with mock.patch.object(provider_process.os, "killpg", side_effect=PermissionError(errno.EPERM, "Operation not permitted")):
            provider_process.stop(process)
        self.assertEqual(process.wait.call_args_list, [mock.call(timeout=.1), mock.call()])

    def test_stop_accepts_eperm_only_after_leader_exits(self):
        for phase in range(3):
            for exited in (False, True):
                with self.subTest(phase=phase, exited=exited):
                    # TERM, probe, KILL: the leader exits just before EPERM.
                    process = mock.Mock(pid=123)
                    process.poll.side_effect = [None] * (1 if phase == 0 else 2) + [0 if exited else None]
                    if not exited:
                        process.wait.side_effect = subprocess.TimeoutExpired("adapter", .1)
                    error = PermissionError(errno.EPERM, "Operation not permitted")
                    with mock.patch.object(provider_process.os, "killpg", side_effect=[None] * phase + [error]) as killpg:
                        with mock.patch.object(provider_process.time, "monotonic", side_effect=[10, 12]):
                            if exited:
                                provider_process.stop(process)
                                process.wait.assert_called_once_with()
                            else:
                                with self.assertRaises(PermissionError) as raised:
                                    provider_process.stop(process)
                                self.assertIs(raised.exception, error)
                                process.wait.assert_called_once_with(timeout=.1)
                    self.assertEqual(killpg.call_count, phase + 1)

    def test_stop_ignores_repeated_interrupts_and_restores_handlers(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        process = subprocess.Popen([sys.executable, "-B", "-c",
            'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            'print("ready", flush=True); time.sleep(30)'],
            stdout=subprocess.PIPE, start_new_session=True)
        sleep = time.sleep

        def repeat_interrupts(delay):
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGINT)
            sleep(delay)

        try:
            self.assertEqual(process.stdout.readline(), b"ready\n")
            with mock.patch.object(provider_process.time, "sleep", side_effect=repeat_interrupts) as repeated:
                provider_process.stop(process, grace=.1)
            self.assertGreater(repeated.call_count, 0)
            self.assertEqual(process.returncode, -signal.SIGKILL)
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
            self.assertEqual({sig: signal.getsignal(sig) for sig in previous}, previous)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()

    def test_stop_reaps_child(self):
        process = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"], start_new_session=True)
        try:
            provider_process.stop(process)
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def test_early_failure_preserved_after_broken_pipe(self):
        reply = {"protocol_version": 1, "accepted": False, "state": "failed",
                 "reason": "credential_missing", "fatal": True}
        with tempfile.TemporaryDirectory() as directory:
            adapter = Path(directory) / "adapter.py"
            adapter.write_text("import sys\nprint(" + repr(json.dumps(reply)) + ")\nsys.exit(1)\n", encoding="utf-8")
            result = provider_process.invoke([sys.executable, str(adapter)], {"task": "x" * (2 * 1024 * 1024)}, 5)
        self.assertEqual(result, reply)

    def test_stop_waits_for_group_after_direct_child_exits(self):
        process = mock.Mock(pid=123, poll=mock.Mock(return_value=0))
        with mock.patch.object(provider_process.os, "killpg", side_effect=[None, None, ProcessLookupError()]) as killpg:
            with mock.patch.object(provider_process.time, "sleep") as sleep:
                provider_process.stop(process)
        self.assertEqual(killpg.call_args_list, [mock.call(123, signal.SIGTERM), mock.call(123, 0), mock.call(123, 0)])
        sleep.assert_called_once()
        process.wait.assert_called_once_with()

    def test_stop_kills_group_after_grace_period(self):
        process = mock.Mock(pid=123)
        with mock.patch.object(provider_process.os, "killpg") as killpg:
            with mock.patch.object(provider_process.time, "monotonic", side_effect=[10, 12]):
                provider_process.stop(process)
        self.assertEqual(killpg.call_args_list, [mock.call(123, signal.SIGTERM), mock.call(123, 0), mock.call(123, signal.SIGKILL)])
        process.wait.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
