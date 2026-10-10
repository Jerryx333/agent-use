#!/usr/bin/env python3
# JSON discovery, validation, and execution interface for local agents.

import argparse
import json
from pathlib import Path
import signal
import sys
import tempfile

from dispatcher_config import ConfigError, ProviderDisabled, disabled_status, catalog, fields, load_config, resolve, resolve_config_path
from run_agent import RouteError, interrupted, run

REQUEST_FIELDS = {"version", "task", "output", "profile", "providers", "models", "timeout", "expect_exact", "require_no_tools"}


def request_options(request, config_path):
    fields(request, REQUEST_FIELDS, "request")
    if type(request.get("version")) is not int or request["version"] != 1:
        raise ConfigError("request version must be 1")
    if not isinstance(request.get("task"), str) or not request["task"].strip():
        raise ConfigError("task must be nonempty text")
    if "output" in request and (not isinstance(request["output"], str) or not request["output"].strip()):
        raise ConfigError("output must be a path string")
    if "expect_exact" in request and not isinstance(request["expect_exact"], str):
        raise ConfigError("expect_exact must be a string")
    if not isinstance(request.get("require_no_tools", False), bool):
        raise ConfigError("require_no_tools must be boolean")
    if ("profile" in request and request["profile"] is None) or ("providers" in request and request["providers"] is None):
        raise ConfigError("profile/providers must not be null")
    models = request.get("models", {})
    if not isinstance(models, dict):
        raise ConfigError("models must map provider names to models")
    routes = resolve(load_config(config_path), profile=request.get("profile"), requested=request.get("providers"),
        models=models, timeout=request.get("timeout"), require_no_tools=request.get("require_no_tools", False))
    options = argparse.Namespace(task=None, output=request.get("output"), config=config_path, order=None,
        providers=request.get("providers"), profile=request.get("profile"), models=models, timeout=request.get("timeout"),
        expect_exact=request.get("expect_exact"), require_no_tools=request.get("require_no_tools", False))
    return options, routes


def main():
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description="Agent Use JSON dispatch interface")
    parser.add_argument("action", choices=["list", "validate", "run"])
    parser.add_argument("--config")
    parser.add_argument("--request", default="-", help="JSON file path, or - for stdin")
    args = parser.parse_args()
    try:
        if args.action == "list":
            config_path = resolve_config_path(args.config)
            print(json.dumps({**catalog(load_config(config_path)), "config_path": str(config_path)}, ensure_ascii=False))
            return 0
        if args.request == "-":
            raw = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        else:
            with Path(args.request).open("rb") as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ConfigError("request exceeds the 4 MiB limit")
        request = json.loads(raw)
        try:
            options, routes = request_options(request, args.config)
        except ProviderDisabled:
            result = {"protocol_version": 1, "state": "failed", "accepted": False,
                      "reason": "provider_disabled", "fatal": False, "attempts": []}
            if args.action == "run":
                output = request.get("output") or str(Path(tempfile.mkdtemp(prefix="agent-use-job-")) / "result")
                result = {**disabled_status(output), "run_directory": str(Path(output).expanduser().resolve())}
            print(json.dumps(result, ensure_ascii=False))
            return 1
        if args.action == "validate":
            print(json.dumps({"protocol_version": 1, "state": "valid", "routes": [
                {name: route[name] for name in ("provider", "model", "timeout", "options", "tool_policy")}
                for route in routes]}, ensure_ascii=False))
            return 0
        if options.output is None:
            options.output = str(Path(tempfile.mkdtemp(prefix="agent-use-job-")) / "result")
        with tempfile.TemporaryDirectory(prefix="agent-use-input-") as directory:
            task = Path(directory) / "task.txt"
            task.write_text(request["task"], encoding="utf-8")
            options.task = str(task)
            status = run(options)
        output = Path(options.output).expanduser().resolve()
        reply = {**status, "run_directory": str(output)}
        if status["state"] == "staged_for_review":
            reply["response"] = (output / "response.txt").read_text(encoding="utf-8")
        print(json.dumps(reply, ensure_ascii=False))
        return 0 if status["state"] == "staged_for_review" else 1
    except (ConfigError, RouteError, OSError, ValueError, TypeError, OverflowError, KeyboardInterrupt) as error:
        category = "interrupted" if isinstance(error, KeyboardInterrupt) else "invalid_request_or_run"
        result = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": category}
        if isinstance(error, (ConfigError, RouteError)):
            result["message"] = str(error)
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
