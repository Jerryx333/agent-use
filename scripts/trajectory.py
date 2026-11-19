"""Bound optional tool telemetry and persist it without following symlinks."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import uuid

MAX_BYTES = 1024 * 1024


def timestamp(value):
    try:
        if isinstance(value, str):
            if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", value):
                return None
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif type(value) in (int, float) and 0 <= value <= 253402300799:
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    except (ValueError, OverflowError, OSError):
        return None


class Trajectory(list):
    def __init__(self, values=()):
        super().__init__()
        self.bytes = 2
        self.truncated = False
        for value in values:
            self.append(value)
        self.truncated |= getattr(values, "truncated", False)

    def append(self, item):
        if not isinstance(item, dict):
            return
        ts = timestamp(item.get("ts"))
        tool, value = item.get("tool"), item.get("input")
        if ts is None or not isinstance(tool, str) or not re.fullmatch(r"[^\s\x00-\x1f]{1,128}", tool):
            return
        if not isinstance(value, str) or any(type(item[k]) is not bool for k in ("edit", "rejected") if k in item):
            return
        row = {"ts": ts, "tool": tool, "input": value[:2000]}
        row.update({k: item[k] for k in ("edit", "rejected") if k in item})
        # Conservative ASCII JSON budget also bounds UTF-8 and JSONL encodings.
        size = len(json.dumps(row).encode("utf-8")) + (2 if self else 0)
        if self.truncated or len(self) >= 2000 or self.bytes + size > MAX_BYTES:
            self.truncated = True
            return
        self.bytes += size
        super().append(row)

    def metadata(self):
        return {"trajectory": self, **({"trajectory_truncated": True} if self.truncated else {})}


def write_trajectory(directory, metadata, record):
    if not isinstance(metadata, dict) or not isinstance(metadata.get("trajectory"), list):
        return
    rows = Trajectory(metadata["trajectory"])
    if rows.truncated or metadata.get("trajectory_truncated") is True:
        record["trajectory_truncated"] = True
    if not rows:
        return
    directory = Path(os.path.abspath(directory))
    fd = None
    temporary = ".trajectory-" + uuid.uuid4().hex + ".tmp"
    try:
        if not stat.S_ISDIR(directory.lstat().st_mode):
            return
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        # Replaces the link itself, never its destination. dir_fd pins the directory.
        os.replace(temporary, "trajectory.jsonl", src_dir_fd=fd, dst_dir_fd=fd)
        record["transcript_path"] = str(directory / "trajectory.jsonl")
    except (OSError, UnicodeError):
        pass  # Optional telemetry must not change dispatch results.
    finally:
        if fd is not None:
            try:
                os.unlink(temporary, dir_fd=fd)
            except OSError:
                pass
            os.close(fd)
