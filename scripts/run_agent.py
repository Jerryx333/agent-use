#!/usr/bin/env python3
# Dispatch local adapters, try fallback routes, and persist reviewable results.

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dispatcher_config import ConfigError, ProviderDisabled, dangerous_environment_name, disabled_status, load_config, resolve, resolve_config_path, scheduler_versions as core_versions, workspace_options
from provider_process import ProcessError, invoke

REPO_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_NAMES = frozenset({"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "TERM", "PYTHONIOENCODING", "PYTHONUTF8"})
ENVIRONMENT_PREFIXES = ("LC_", "AGENT_USE_")


class RouteError(Exception):
    pass


def sanitize_detail(detail):
    return " ".join("".join(char if char.isprintable() else " " for char in detail).split())[:240]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_failure(error):
    category = getattr(error, "category", None)
    if category in ("credential_missing", "keychain_authorization_pending"):
        return category, True
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return "interrupted", True
    if isinstance(category, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category):
        return category, False
    return "provider_failed", False


def missing_credential(error):
    item = getattr(error, "credential", None)
    return safe_credential(item) if item is not None else None


def safe_credential(item):
    return item if isinstance(item, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_. /-]{0,127}", item) else "adapter credential (safe name unavailable)"


def provider_environment(route):
    prefixes = ENVIRONMENT_PREFIXES + tuple(route.get("env_prefixes", []))
    names = ENVIRONMENT_NAMES | set(route.get("env_names", []))
    return {name: value for name, value in os.environ.items()
            if (name in names or name.startswith(prefixes)) and not dangerous_environment_name(name)}


def attempt(provider, task, directory, timeout, expected, route=None, workdir=None, access="none"):
    started = time.monotonic()
    record = {"provider": provider, "state": "failed"}
    try:
        route = route or resolve(load_config(), requested=[provider], timeout=timeout, access=access)[0]
        record["requested_model"] = route["model"]
        record["options"] = route["options"]
        request = {"protocol_version": 1, "task_file": str(task.resolve()), "output": str(directory.resolve()),
                   "model": route["model"], "timeout": timeout, "options": route["options"], "expect_exact": expected,
                   "workdir": workdir, "access": access}
        environment = provider_environment(route)
        reply = invoke(route["command"], request, timeout + 8, environment)
        if not isinstance(reply, dict) or type(reply.get("protocol_version")) is not int or reply["protocol_version"] != 1 or reply.get("accepted") is not False:
            raise ProcessError("invalid_protocol")
        if reply.get("state") == "failed":
            reason = reply.get("reason")
            if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
                raise ProcessError("invalid_protocol")
            if "fatal" in reply and not isinstance(reply["fatal"], bool):
                raise ProcessError("invalid_protocol")
            record["reason"] = reason
            detail = reply.get("detail")
            if isinstance(detail, str):
                record["detail"] = sanitize_detail(detail)
            if reason == "credential_missing":
                item = reply.get("missing_credential")
                record["missing_credential"] = safe_credential(item)
            return record, None, reason in {"credential_missing", "keychain_authorization_pending", "interrupted"} or reply.get("fatal", False)
        if reply.get("state") != "staged_for_review":
            raise ProcessError("invalid_protocol")
        content = reply.get("response")
        if not isinstance(content, str) or not content.strip() or (expected is not None and content != expected):
            record["reason"] = "validation_failed"
            return record, None, False
        record["state"] = "staged_for_review"
        metadata = reply.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ProcessError("invalid_protocol")
        for field in ("actual_model", "selected_model", "transport"):
            if isinstance(metadata.get(field), str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", metadata[field]):
                record[field] = metadata[field]
        for field in ("credits", "switch_count", "model_switch_count"):
            value = metadata.get(field)
            if type(value) in {int, float} and 0 <= value < float("inf"):
                record[field] = value
        usage = metadata.get("usage")
        if isinstance(usage, dict):
            record["usage"] = {name: value for name, value in usage.items() if
                name in {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}
                and type(value) is int and value >= 0}
        model_attempts = metadata.get("model_attempts")
        if isinstance(model_attempts, list):
            record["model_attempts"] = []
            for step in model_attempts[:12]:
                if not isinstance(step, dict):
                    continue
                safe = {}
                for name in ("requested_model", "state", "reason"):
                    value = step.get(name)
                    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", value):
                        safe[name] = value
                if type(step.get("elapsed_s")) in {int, float} and 0 <= step["elapsed_s"] < float("inf"):
                    safe["elapsed_s"] = step["elapsed_s"]
                credits = step.get("credits")
                if type(credits) in {int, float} and 0 <= credits < float("inf"):
                    safe["credits"] = credits
                record["model_attempts"].append(safe)
        return record, content, False
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        reason, fatal = (error.category, False) if isinstance(error, ProcessError) else classify_failure(error)
        record["reason"] = reason
        if reason == "credential_missing":
            record["missing_credential"] = missing_credential(error)
        return record, None, fatal
    finally:
        record["elapsed_s"] = round(time.monotonic() - started, 2)


def write_status(output, status):
    temporary = output / "status.json.tmp"
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output / "status.json")


def apply_options(routes, values):
    selected = {route["provider"]: route for route in routes}
    seen = set()
    for value in values:
        target, separator, content = value.partition("=")
        provider, dot, key = target.partition(".")
        if not separator or not dot or not provider or not key or target in seen:
            raise RouteError("option must be a unique PROVIDER.KEY=VALUE")
        if provider not in selected:
            raise RouteError("option override references an unselected provider")
        if key not in selected[provider]["options"]:
            raise RouteError("provider does not support the specified option")
        seen.add(target)
        selected[provider]["options"][key] = content


def version_key(source, config_dir):
    """Describe command files without publishing machine-specific absolute paths."""
    source = Path(source)
    resolved = source.resolve()
    if resolved.is_relative_to(REPO_ROOT):
        return resolved.relative_to(REPO_ROOT).as_posix()
    if source.absolute().is_relative_to(config_dir) or resolved.is_relative_to(config_dir):
        return "config:" + Path(os.path.relpath(resolved, config_dir)).as_posix()
    return resolved.name


def run(args):
    order = getattr(args, "order", None)
    requested = getattr(args, "providers", None)
    if order is not None:
        if requested is not None:
            raise RouteError("order and providers are mutually exclusive")
        requested = order.split(",")
    try:
        args.workdir, args.access = workspace_options(getattr(args, "workdir", None),
            getattr(args, "access", "none"), getattr(args, "require_no_tools", False))
        config_path = resolve_config_path(getattr(args, "config", None))
        routes = resolve(load_config(config_path), profile=getattr(args, "profile", None),
            requested=requested, models=getattr(args, "models", None), timeout=args.timeout,
            require_no_tools=getattr(args, "require_no_tools", False), access=args.access)
    except ProviderDisabled:
        return disabled_status(args.output, args.workdir, args.access)
    except ConfigError as error:
        raise RouteError(str(error)) from None
    apply_options(routes, getattr(args, "option", []))
    providers = tuple(route["provider"] for route in routes)
    prompt = Path(args.task).read_text(encoding="utf-8")
    if not prompt.strip():
        raise RouteError("task must not be empty")
    adapter_versions = {}
    for route in routes:
        provider = route["provider"]
        sources = [Path(part) for part in route["command"] if Path(part).is_file()]
        adapter_versions[provider] = {
            str(source): hashlib.sha256(source.read_bytes()).hexdigest()
            for source in sorted(set(sources)) if source.is_file()}
    scheduler_versions = core_versions()
    fingerprint = digest(json.dumps({"prompt": prompt, "routes": routes, "cwd": str(Path.cwd().resolve()),
                                     "expected": args.expect_exact,
                                     "workdir": args.workdir, "access": args.access,
                                     "adapter_versions": adapter_versions, "scheduler_versions": scheduler_versions}, sort_keys=True))
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(output.parent / ("." + output.name + ".agent-use.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RouteError("output directory is in use by another invocation") from None
        status_versions = {provider: {version_key(source, config_path.parent): sha
                                      for source, sha in versions.items()}
                           for provider, versions in adapter_versions.items()}
        return execute(args, prompt, routes, providers, fingerprint, output, scheduler_versions, status_versions)
    finally:
        os.close(descriptor)


def execute(args, prompt, routes, providers, fingerprint, output, scheduler_versions, adapter_versions):
    if output.exists():
        status = json.loads((output / "status.json").read_text(encoding="utf-8"))
        if not isinstance(status, dict) or status.get("state") != "staged_for_review" or status.get("accepted") is not False or status.get("request_sha256") != fingerprint:
            raise RouteError("existing directory does not match or was unsuccessful; use a new directory, no automatic resend")
        content = (output / "response.txt").read_text(encoding="utf-8")
        if status.get("response_sha256") != digest(content):
            raise RouteError("existing artifact has changed; use a new directory")
        return status
    output.mkdir(parents=True, mode=0o700)
    status = {"protocol_version": 1, "state": "running", "accepted": False, "request_sha256": fingerprint,
              "order": providers, "attempts": [], "scheduler_versions": scheduler_versions,
              "workdir": args.workdir, "access": args.access}
    write_status(output, status)
    task = output / "task.txt"
    try:
        task.write_text(prompt, encoding="utf-8")
        attempts = output / "attempts"
        attempts.mkdir(mode=0o700)
        for route in routes:
            provider = route["provider"]
            status["current_provider"] = provider
            write_status(output, status)
            print(f"agent-use: invoking {provider}", file=sys.stderr)
            record, content, fatal = attempt(provider, task, attempts / provider, route["timeout"], args.expect_exact,
                                             route, args.workdir, args.access)
            record["adapter_versions"] = adapter_versions[provider]
            status["attempts"].append(record)
            if content is not None:
                (output / "response.txt").write_text(content, encoding="utf-8")
                status.update(state="staged_for_review", selected_provider=provider,
                              response_sha256=digest(content))
                break
            if record["reason"] == "credential_missing":
                print(f"Missing credential entry or capability: {record['missing_credential']}", file=sys.stderr)
            else:
                detail = f": {record['detail']}" if isinstance(record.get("detail"), str) else ""
                print(f"agent-use: {provider} failed ({record['reason']}){detail}", file=sys.stderr)
            # Interruptions stop the chain. Other fatal failures stop a single
            # route, but an explicitly configured fallback chain continues.
            if record["reason"] == "interrupted" or (fatal and len(routes) == 1):
                status.update(state="failed", reason=record["reason"])
                break
        else:
            status.update(state="failed", reason="all_providers_failed")
    except (Exception, KeyboardInterrupt, SystemExit):
        status.update(state="failed", reason="router_failed")
        write_status(output, status)
        raise RouteError("routing failed; status recorded, raw exception omitted") from None
    status.pop("current_provider", None)
    write_status(output, status)
    return status


def main():
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description="Configurable local agent dispatcher with results pending review")
    parser.add_argument("task")
    parser.add_argument("output")
    parser.add_argument("--order")
    parser.add_argument("--profile")
    parser.add_argument("--config")
    parser.add_argument("--option", action="append", default=[], help="PROVIDER.KEY=VALUE, repeatable; VALUE may be empty")
    parser.add_argument("--model", action="append", default=[], help="provider=model, repeatable")
    parser.add_argument("--require-no-tools", action="store_true")
    parser.add_argument("--workdir", help="Existing workspace directory; requires access ro or rw")
    parser.add_argument("--access", choices=["none", "ro", "rw"], default="none")
    parser.add_argument("--timeout", type=float, help="Override timeout in seconds for each selected route")
    parser.add_argument("--expect-exact", help="Optional exact response check; mismatch tries the next route")
    args = parser.parse_args()
    try:
        args.models = {}
        for value in args.model:
            provider, separator, model = value.partition("=")
            if not separator or provider in args.models:
                raise RouteError("model must be a unique provider=model")
            args.models[provider] = model
        status = run(args)
    except (RouteError, OSError, ValueError, TypeError, OverflowError) as error:
        print(str(error) if isinstance(error, RouteError) else "invalid routing input or state; raw exception omitted", file=sys.stderr)
        return 1
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["state"] == "staged_for_review" else 1


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    sys.exit(main())
