#!/usr/bin/env python3
"""A deterministic, standard-library-only adapter for the local protocol."""

import json
from pathlib import Path
import sys


def main():
    request = json.load(sys.stdin)
    task = Path(request["task_file"]).read_text(encoding="utf-8")
    prefix = request.get("options", {}).get("prefix", "echo: ")
    print(json.dumps({"protocol_version": 1, "state": "staged_for_review",
                      "accepted": False, "response": str(prefix) + task}))


if __name__ == "__main__":
    main()
