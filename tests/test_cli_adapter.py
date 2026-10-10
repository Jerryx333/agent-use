# Offline CLI protocol, bounded output, and owned process-group tests.

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 4 * 1024 * 1024


class CliAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = self.root / 'task with spaces.txt'
        self.task.write_text('hello café', encoding='utf-8')

    def invoke(self, command, **overrides):
        request = {'task_file': str(self.task), 'model': 'test-model', 'timeout': 5, **overrides}
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'examples/cli_adapter.py'), '--', *command],
            input=json.dumps(request).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b'')
        reply = json.loads(result.stdout)
        self.assertEqual(reply['protocol_version'], 1)
        self.assertIs(reply['accepted'], False)
        return reply

    def python(self, source, **overrides):
        return self.invoke([sys.executable, '-B', '-c', source], **overrides)

    def test_stdin_and_utf8_response(self):
        reply = self.python('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().upper())')
        self.assertEqual(reply['state'], 'staged_for_review')
        self.assertEqual(reply['response'], 'HELLO CAFé')

    def test_placeholders_remain_literal_argv(self):
        model = 'literal {task_file} $(false); *'
        reply = self.invoke([sys.executable, '-B', '-c',
                             'import json,sys; print(json.dumps(sys.argv[1:]))',
                             'prefix={model}/{model}', '{task_file}', ''], model=model)
        self.assertEqual(json.loads(reply['response']), ['prefix=' + model + '/' + model, str(self.task), ''])

    def test_failure_reasons(self):
        cases = [('import sys; print("partial"); sys.exit(3)', 'cli_exit_nonzero'),
                 ('import sys; sys.exit(3)', 'cli_exit_nonzero'),
                 ('pass', 'empty_output'),
                 ('print("   ")', 'empty_output'),
                 ('import time; time.sleep(30)', 'timeout'),
                 ('import os,time; os.close(1); time.sleep(30)', 'timeout'),
                 (f'import sys; sys.stdout.buffer.write(b"x" * {LIMIT + 1})', 'output_too_large')]
        for source, reason in cases:
            with self.subTest(reason=reason, source=source):
                reply = self.python(source, timeout=.3 if reason == 'timeout' else 5)
                self.assertEqual(reply['state'], 'failed')
                self.assertEqual(reply['reason'], reason)

    def test_missing_program_is_fatal(self):
        reply = self.invoke([str(self.root / 'absent-command')])
        self.assertEqual(reply['reason'], 'cli_not_found')
        self.assertIs(reply['fatal'], True)

    def test_output_limit_boundary(self):
        reply = self.python(f'import sys; sys.stdout.buffer.write(b"x" * {LIMIT})')
        self.assertEqual(reply['state'], 'staged_for_review')
        self.assertEqual(len(reply['response']), LIMIT)

    def test_stderr_is_discarded_even_when_large(self):
        reply = self.python('import sys; sys.stderr.buffer.write(b"private\\x00\\x1b\\n" * 600000); sys.exit(2)')
        self.assertEqual(reply, {'protocol_version': 1, 'accepted': False,
                                 'state': 'failed', 'reason': 'cli_exit_nonzero'})

    def test_early_stdin_close(self):
        self.task.write_bytes(b'x' * LIMIT)
        reply = self.python('import os; os.close(0); print("done")')
        self.assertEqual(reply['response'].strip(), 'done')

    def test_timeout_stops_descendant(self):
        marker = self.root / 'late-output'
        pid_file = self.root / 'child-pid'
        child = ('import signal,time; from pathlib import Path; '
                 'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                 f'time.sleep(3); Path({str(marker)!r}).write_text("alive")')
        source = ('import subprocess,sys,time; from pathlib import Path; '
                  f'p=subprocess.Popen([sys.executable,"-B","-c",{child!r}]); '
                  f'Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(30)')
        try:
            reply = self.python(source, timeout=.5)
            self.assertEqual(reply['reason'], 'timeout')
            self.assertTrue(pid_file.exists())
            time.sleep(1)
            self.assertFalse(marker.exists())
        finally:
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    unittest.main()
