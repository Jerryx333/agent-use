#!/usr/bin/env python3
"""Wrap a trusted local argv command in the dispatcher protocol."""

import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dispatcher_config import positive_timeout
from provider_process import MAX_BYTES, ProcessError, stop


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


def execute(command, request):
    with Path(request["task_file"]).open("rb") as stream:
        task = stream.read(MAX_BYTES + 1)
    if len(task) > MAX_BYTES:
        raise ProcessError("input_limit")
    task.decode("utf-8")
    # Substitute once so values containing placeholder text remain literal.
    command = [re.sub(r"\{(model|task_file)\}", lambda match: request[match[1]], part)
               for part in command]
    deadline = time.monotonic() + positive_timeout(request["timeout"])
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        return {"state": "failed", "reason": "cli_not_found", "fatal": True}
    except OSError:
        raise ProcessError("cli_unavailable") from None
    output = bytearray()
    try:
        pending = memoryview(task)
        os.set_blocking(process.stdin.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProcessError("timeout")
                for key, _mask in selector.select(min(.1, remaining)):
                    if key.data == "stdin":
                        try:
                            pending = pending[os.write(key.fd, pending[:65536]):]
                        except BrokenPipeError:
                            pending = pending[:0]
                        if not pending:
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if len(output) + len(chunk) > MAX_BYTES:
                        raise ProcessError("output_too_large")
                    output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProcessError("timeout")
        process.wait(timeout=remaining)
        if process.returncode:
            return {"state": "failed", "reason": "cli_exit_nonzero"}
        response = output.decode("utf-8")
        if not response.strip():
            return {"state": "failed", "reason": "empty_output"}
        return {"state": "staged_for_review", "response": response}
    except subprocess.TimeoutExpired:
        raise ProcessError("timeout") from None
    finally:
        # Ignore repeated termination signals while cleaning the owned group.
        previous = {sig: signal.signal(sig, signal.SIG_IGN)
                    for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            # Finish the nested group before the outer two-second deadline.
            stop(process, grace=1)
        finally:
            process.stdin.close()
            process.stdout.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main():
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if len(sys.argv) < 3 or sys.argv[1] != "--":
            raise ProcessError("invalid_command")
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ProcessError("input_limit")
        request = json.loads(raw)
        reply = execute(sys.argv[2:], request)
    except ProcessError as error:
        reply = {"state": "failed", "reason": error.category}
    except KeyboardInterrupt:
        reply = {"state": "failed", "reason": "interrupted", "fatal": True}
    except UnicodeError:
        reply = {"state": "failed", "reason": "invalid_utf8"}
    except Exception:
        reply = {"state": "failed", "reason": "cli_adapter_failed"}
    print(json.dumps({"protocol_version": 1, "accepted": False, **reply}, ensure_ascii=False))


if __name__ == "__main__":
    main()
