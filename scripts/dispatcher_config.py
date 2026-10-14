# Validate registries and resolve model, profile, timeout, and tool requirements.

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config/dispatcher.json"
NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
DANGEROUS_ENV_PREFIXES = ("LD_", "DYLD_")
DANGEROUS_ENV_NAMES = frozenset({"PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS", "BASH_ENV", "ENV"})
SECRET_NAME = re.compile(r"key|token|password|secret|credential|authorization|cookie", re.I)


class ConfigError(Exception):
    category = "invalid_config"


class ProviderDisabled(ConfigError):
    category = "provider_disabled"


def fail(message):
    raise ConfigError(message)


def fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        fail(label + " has invalid fields")


def dangerous_environment_name(name):
    return name.startswith(DANGEROUS_ENV_PREFIXES) or name in DANGEROUS_ENV_NAMES


def dangerous_environment_prefix(prefix):
    return (any(prefix.startswith(blocked) or blocked.startswith(prefix)
                for blocked in DANGEROUS_ENV_PREFIXES)
            or any(name.startswith(prefix) for name in DANGEROUS_ENV_NAMES))


def positive_timeout(value):
    try:
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value) and 0 < value <= 86400)
    except OverflowError:
        valid = False
    if not valid:
        fail("timeout must be in (0, 86400] seconds")
    return value


def workspace_options(workdir=None, access="none", require_no_tools=False):
    if not isinstance(access, str) or access not in {"none", "ro", "rw"}:
        fail("access must be none, ro, or rw")
    if access == "none":
        if workdir is not None:
            fail("workdir requires access ro or rw")
        return None, access
    if require_no_tools:
        fail("workspace access conflicts with require_no_tools")
    if not isinstance(workdir, str) or not workdir.strip():
        fail("workdir is required for workspace access")
    try:
        directory = Path(workdir).expanduser().resolve(strict=True)
        if not directory.is_dir():
            fail("workdir must be an existing directory")
    except (OSError, ValueError, RuntimeError):
        fail("workdir must be an existing directory")
    return str(directory), access


def model_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(character) < 32 for character in value):
        fail("model must be a nonempty single-line string")
    return value


def options(value):
    if not isinstance(value, dict):
        fail("options must be an object")
    for name, item in value.items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or SECRET_NAME.search(name):
            fail("invalid or credential-like option name; obtain credentials from a secure runtime")
        if not isinstance(item, (str, int, float, bool)) or isinstance(item, float) and not math.isfinite(item):
            fail("options only support non-sensitive scalars")
    return value


def entries(value):
    if not isinstance(value, list) or not value:
        fail("providers must be a nonempty array")
    result = []
    for entry in value:
        entry = {"provider": entry} if isinstance(entry, str) else copy.deepcopy(entry)
        fields(entry, {"provider", "model", "timeout", "options"}, "provider request")
        name = entry.get("provider")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            fail("invalid provider name")
        if "model" in entry:
            model_name(entry["model"])
        if "timeout" in entry:
            positive_timeout(entry["timeout"])
        options(entry.get("options", {}))
        result.append(entry)
    if len({entry["provider"] for entry in result}) != len(result):
        fail("providers must not repeat within a request")
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fail("unreadable or invalid JSON file")


def resolve_config_path(path=None):
    """Select one registry, without falling back when the selected file fails."""
    try:
        if path is None:
            path = os.environ.get("AGENT_USE_CONFIG")
            if not path:
                xdg = os.environ.get("XDG_CONFIG_HOME", "")
                base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path("~/.config").expanduser()
                candidate = base / "agent-use/dispatcher.json"
                try:
                    candidate.lstat()
                except FileNotFoundError:
                    path = DEFAULT_CONFIG
                else:
                    path = candidate
        resolved = Path(path).expanduser().resolve(strict=True)
        with resolved.open("rb"):
            pass
        return resolved
    except (OSError, ValueError, RuntimeError):
        fail("unreadable registry file")


def load_config(path=None):
    config_path = resolve_config_path(path)
    config = read_json(config_path)
    fields(config, {"version", "default_profile", "providers", "profiles"}, "config")
    if type(config.get("version")) is not int or config["version"] != 1:
        fail("config version must be 1")
    providers = config.get("providers")
    profiles = config.get("profiles")
    if not isinstance(providers, dict) or not providers or not isinstance(profiles, dict):
        fail("registry requires providers and profiles objects")
    for name, specification in providers.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            fail("invalid registry provider name")
        fields(specification, {"command", "enabled", "default_model", "models", "timeout", "options", "tool_policy", "env_prefixes", "env_names"}, "provider registration")
        prefixes = specification.get("env_prefixes", [])
        if not isinstance(prefixes, list) or any(not isinstance(prefix, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{1,62}_", prefix) or dangerous_environment_prefix(prefix) for prefix in prefixes):
            fail("env_prefixes must be an array of uppercase environment prefixes")
        names = specification.get("env_names", [])
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", name)
            or dangerous_environment_name(name)
            for name in names
        ):
            fail("env_names must contain safe exact environment variable names")
        command = specification.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part or "\0" in part for part in command):
            fail("command must be a nonempty argv array; shell commands are not supported")
        if not isinstance(specification.get("enabled", True), bool):
            fail("enabled must be boolean")
        model_name(specification.get("default_model"))
        positive_timeout(specification.get("timeout", 60))
        options(specification.get("options", {}))
        if specification.get("tool_policy", "unknown") not in {"disabled", "guarded", "unknown", "workspace"}:
            fail("invalid tool_policy")
        if not isinstance(specification.get("models", []), list):
            fail("models must be an array")
        for model in specification.get("models", []):
            model_name(model)
        specification["command"] = [part.replace("{config_dir}", str(config_path.parent))
                                    for part in command]
    for name, profile in profiles.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            fail("invalid profile name")
        for entry in entries(profile):
            if entry["provider"] not in providers:
                fail("profile references an unregistered provider; update profiles when removing providers")
    if not isinstance(config.get("default_profile"), str) or config["default_profile"] not in profiles:
        fail("default_profile is not registered")
    return config


def resolve(config, profile=None, requested=None, models=None, timeout=None, require_no_tools=False, access="none"):
    if profile is not None and (not isinstance(profile, str) or not NAME.fullmatch(profile)):
        fail("invalid profile name")
    if not isinstance(require_no_tools, bool):
        fail("require_no_tools must be boolean")
    if not isinstance(access, str) or access not in {"none", "ro", "rw"}:
        fail("access must be none, ro, or rw")
    if access != "none" and require_no_tools:
        fail("workspace access conflicts with require_no_tools")
    if profile is not None and requested is not None:
        fail("profile and providers/order are mutually exclusive")
    selected_profile = profile or config["default_profile"]
    if requested is None and selected_profile not in config["profiles"]:
        fail("profile is not registered")
    selected = entries(requested if requested is not None else config["profiles"][selected_profile])
    overrides = models or {}
    if not isinstance(overrides, dict):
        fail("models must be an object")
    if timeout is not None:
        positive_timeout(timeout)
    if set(overrides) - {entry["provider"] for entry in selected}:
        fail("model override references an unselected provider")
    routes = []
    for entry in selected:
        name = entry["provider"]
        if name not in config["providers"]:
            fail("provider is not registered")
        specification = config["providers"][name]
        if not specification.get("enabled", True):
            if requested is not None:
                raise ProviderDisabled("requested provider is disabled")
            continue
        policy = specification.get("tool_policy", "unknown")
        if access != "none" and policy != "workspace":
            continue
        if require_no_tools and policy not in {"disabled", "workspace"}:
            fail("selected provider does not guarantee disabled tools; use text-safe or explicitly select a tool-free provider")
        model = model_name(overrides.get(name, entry.get("model", specification["default_model"])))
        budget = positive_timeout(timeout if timeout is not None else entry.get("timeout", specification.get("timeout", 60)))
        settings = {**specification.get("options", {}), **entry.get("options", {})}
        options(settings)
        if set(entry.get("options", {})) - set(specification.get("options", {})):
            fail("options must be declared in the provider registry")
        command = [part.replace("{python}", sys.executable).replace("{repo}", str(REPO_ROOT)) for part in specification["command"]]
        routes.append({"provider": name, "model": model, "timeout": budget, "options": settings,
                       "command": command, "tool_policy": policy,
                       "env_prefixes": specification.get("env_prefixes", []),
                       "env_names": specification.get("env_names", [])})
    if not routes:
        if access != "none":
            fail("no enabled workspace providers")
        raise ProviderDisabled("no enabled providers")
    return routes


def catalog(config):
    return {"protocol_version": 1, "default_profile": config["default_profile"],
            "profiles": config["profiles"], "providers": [
                {"provider": name, "enabled": specification.get("enabled", True),
                 "default_model": specification["default_model"], "models": specification.get("models", []),
                 "model_list_is_exhaustive": False, "timeout": specification.get("timeout", 60),
                 "env_prefixes": specification.get("env_prefixes", []),
                 "env_names": specification.get("env_names", []),
                 "options": specification.get("options", {}), "tool_policy": specification.get("tool_policy", "unknown")}
                for name, specification in config["providers"].items()]}


def scheduler_versions():
    """Hash the same core sources used by the request fingerprint."""
    return {name: hashlib.sha256((REPO_ROOT / "scripts" / name).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            for name in ("run_agent.py", "dispatcher_config.py", "provider_process.py")}


def disabled_status(output, workdir=None, access="none"):
    """Persist a rejected request without overwriting an existing run."""
    status = {"protocol_version": 1, "state": "failed", "accepted": False,
              "reason": "provider_disabled", "fatal": False, "attempts": [],
              "scheduler_versions": scheduler_versions(), "workdir": workdir, "access": access}
    directory = Path(output).expanduser().resolve()
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    (directory / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status
