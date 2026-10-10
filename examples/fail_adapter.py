#!/usr/bin/env python3
"""Always fail to demonstrate a fallback chain."""

import json
import sys


def main():
    json.load(sys.stdin)
    print(json.dumps({"protocol_version": 1, "state": "failed",
                      "accepted": False, "reason": "example_failure"}))


if __name__ == "__main__":
    main()
