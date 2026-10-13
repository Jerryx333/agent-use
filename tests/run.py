#!/usr/bin/env python3
"""Run isolated offline test modules and installation checks at low priority."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.durations = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.durations.append((test.id(), time.monotonic() - self.started))
        super().stopTest(test)


def lower_priority():
    try:
        # Set nice to at least 10 without increasing an already lower priority.
        os.nice(max(0, 10 - os.nice(0)))
    except (AttributeError, OSError):
        pass


def run_module(module):
    # Separate processes protect module globals, signal handlers and env patches.
    with tempfile.TemporaryDirectory(prefix='agent-use-suite-') as directory:
        report = Path(directory) / 'report.json'
        environment = dict(os.environ, HOME=directory, PYTHONDONTWRITEBYTECODE='1')
        for key in ('AGENT_USE_CONFIG', 'XDG_CONFIG_HOME'):
            environment.pop(key, None)
        process = subprocess.run(
            [sys.executable, '-B', str(Path(__file__).resolve()),
             '--worker', module, '--report', str(report)],
            cwd=ROOT, env=environment, capture_output=True, text=True)
        data = json.loads(report.read_text()) if report.exists() else {
            'count': 0, 'skipped': 0, 'durations': []}
        return module, process.returncode, process.stdout + process.stderr, data


def main():
    lower_priority()
    cpus = os.cpu_count() or 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-j', type=int, default=max(1, cpus // 4),
                        help='parallel modules (default: CPUs // 4; cap: CPUs // 2)')
    parser.add_argument('--durations', action='store_true', help='show slowest 10 test cases')
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    parser.add_argument('--report', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.j < 1:
        parser.error('-j must be at least 1')
    limit = max(1, cpus // 2)
    if args.j > limit:
        print(f'Concurrency clamped: -j {args.j} -> {limit} (half the CPU count)',
              file=sys.stderr)
    jobs = min(args.j, limit)
    if args.worker:
        suite = unittest.defaultTestLoader.loadTestsFromName(args.worker)
        result = unittest.TextTestRunner(resultclass=TimedResult).run(suite)
        Path(args.report).write_text(json.dumps({
            'count': result.testsRun, 'skipped': len(result.skipped),
            'durations': result.durations}))
        return 0 if result.wasSuccessful() else 1

    modules = sorted(path.stem for path in TESTS.glob('test_*.py'))
    print(f'CPUs: {cpus}; parallel modules: {jobs}; nice target: 10', flush=True)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run_module, modules))
    count = skipped = failed = 0
    durations = []
    for module, status, output, data in results:
        count += data['count']
        skipped += data['skipped']
        durations.extend(data['durations'])
        failed += bool(status)
        print(f'{"FAIL" if status else "PASS"} {module} ({data["count"]} cases)')
        if status:
            print(output, end='')
    # Shell checks are additional checks, not new unittest cases.
    with tempfile.TemporaryDirectory(prefix='agent-use-shell-') as directory:
        environment = dict(os.environ, HOME=directory)
        for key in ('AGENT_USE_CONFIG', 'XDG_CONFIG_HOME'):
            environment.pop(key, None)
        for command in (['bash', '-n', 'scripts/install.sh', 'scripts/test.sh',
                          'tests/test_install.sh'], ['bash', 'tests/test_install.sh']):
            process = subprocess.run(command, cwd=ROOT, env=environment,
                                     capture_output=True, text=True)
            failed += bool(process.returncode)
            print(f'{"FAIL" if process.returncode else "PASS"} {" ".join(command)}')
            if process.returncode:
                print(process.stdout + process.stderr, end='')
    print(f'{count} tests; {skipped} skipped; {failed} failed modules/checks')
    if args.durations:
        print('Slowest 10 cases (seconds):')
        for name, seconds in sorted(durations, key=lambda row: (-row[1], row[0]))[:10]:
            print(f'{seconds:.3f}s {name}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
