#!/usr/bin/env python3
# Check canonical registry JSON formatting.

import json
from pathlib import Path
import unittest

CONFIG = Path(__file__).resolve().parents[1] / "config" / "dispatcher.json"


class ConfigFormatTest(unittest.TestCase):
    def test_dispatcher_json_is_canonical(self):
        text = CONFIG.read_text(encoding="utf-8")
        canonical = json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n"
        self.assertTrue(text == canonical,
                        "config/dispatcher.json is not canonically formatted; rewrite it using json.dumps(json.loads(s), ensure_ascii=False, indent=2) + '\\n' before committing")



if __name__ == "__main__":
    unittest.main()
