# Run registered argv adapters with bounded JSON pipes and POSIX group cleanup.

import errno
import json
import os
import selectors
import signal
import subprocess
import time

MAX_BYTES = 4 * 1024 * 1024


class ProcessError(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def _signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        # macOS may return EPERM for a zombie-only group. Recheck/reap the
        # leader after killpg: it can exit between the preceding poll and killpg.
        if error.errno != errno.EPERM:
            raise
        if process.poll() is None:
            # The kernel can report the dying group before waitpid sees exit.
            try:
                process.wait(timeout=.1)
            except subprocess.TimeoutExpired:
                raise error from None
        return False
    return True


def stop(process, grace=2):
    # Cleanup must finish even if the caller receives another interruption.
    previous = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, signal.SIG_IGN)
        deadline = time.monotonic() + grace
        process.poll()
        if _signal_group(process, signal.SIGTERM):
            while True:
                process.poll()
                if not _signal_group(process, 0):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _signal_group(process, signal.SIGKILL)
                    break
                time.sleep(min(.05, remaining))
        process.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def invoke(command, request, timeout, environment=None):
    payload = (json.dumps(request, ensure_ascii=False) + "\n").encode()
    if len(payload) > MAX_BYTES:
        raise ProcessError("input_limit")
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=environment, start_new_session=True)
    except OSError:
        raise ProcessError("adapter_unavailable") from None
    output = bytearray()
    received = 0
    try:
        pending = memoryview(payload)
        os.set_blocking(process.stdin.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProcessError("timeout")
                for key, _mask in selector.select(min(.1, remaining)):
                    if key.data == "stdin":
                        try:
                            count = os.write(key.fd, pending[:65536])
                            pending = pending[count:]
                        except OSError as error:
                            if not isinstance(error, BrokenPipeError) and error.errno != errno.EPIPE:
                                raise
                            pending = pending[:0]
                        if not pending:
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                            process.stdin = None
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    received += len(chunk)
                    if received > MAX_BYTES:
                        raise ProcessError("output_limit")
                    if key.data == "stdout":
                        output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProcessError("timeout")
        process.wait(timeout=remaining)
        try:
            reply = json.loads(output)
        except (ValueError, UnicodeError):
            raise ProcessError("invalid_protocol") from None
        if process.returncode and isinstance(reply, dict) and reply.get("state") != "failed":
            raise ProcessError("adapter_failed")
        return reply
    except subprocess.TimeoutExpired:
        raise ProcessError("timeout") from None
    except OSError:
        raise ProcessError("adapter_io_failed") from None
    finally:
        try:
            stop(process)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
