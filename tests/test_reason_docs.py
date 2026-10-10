"""Keep literal failure categories in the offline sources documented."""

import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def literals(node):
    return {part.value for part in ast.walk(node)
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", part.value)}


def assigned_literals(node):
    if isinstance(node, ast.Constant):
        return literals(node)
    if isinstance(node, ast.IfExp):
        return assigned_literals(node.body) | assigned_literals(node.orelse)
    return set()


def reason_literals(source):
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "reason":
                    found.update(assigned_literals(value))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "ProcessError" and node.args:
                found.update(literals(node.args[0]))
            for keyword in node.keywords:
                if keyword.arg == "reason":
                    found.update(assigned_literals(keyword.value))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (isinstance(target, ast.Name) and target.id in {"reason", "category"}
                    or isinstance(target, ast.Attribute) and target.attr in {"reason", "category"}
                    or isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "reason"):
                    if node.value is not None:
                        found.update(assigned_literals(node.value))
        elif isinstance(node, ast.FunctionDef) and node.name == "classify_failure":
            # Includes recognized categories and literal (reason, fatal) returns.
            found.update(literals(node) - {"category"})
    return found


class ReasonDocumentationTests(unittest.TestCase):
    def test_all_source_reasons_have_table_rows(self):
        document = (ROOT / "docs/DISPATCHER.md").read_text()
        vocabulary = document.split("## Reason codes\n", 1)[1].split("## Artifacts and reuse", 1)[0]
        rows = set(re.findall(r"^\| `([a-z][a-z0-9_]{0,63})` \|", vocabulary, re.M))
        found = set()
        for directory in ("scripts", "examples"):
            for path in (ROOT / directory).rglob("*.py"):
                reasons = reason_literals(path.read_text())
                self.assertFalse(reasons - rows, f"{path.relative_to(ROOT)}: undocumented {sorted(reasons - rows)}")
                found.update(reasons)
        self.assertTrue({"router_failed", "invalid_request_or_run", "adapter_io_failed", "cli_not_found"} <= found)

    def test_scanner_covers_reason_production_forms(self):
        source = '''
reason = "assigned_reason"
record["reason"] = "record_reason"
category = "one_reason" if flag else "other_reason"
reply = {"reason": "dict_reason"}
status.update(reason="keyword_reason")
raise ProcessError("process_reason")
def classify_failure(error):
    if category in ("recognized_reason",):
        return category, True
    return "returned_reason", False
'''
        self.assertEqual(reason_literals(source), {"assigned_reason", "record_reason", "one_reason", "other_reason",
                                                  "dict_reason", "keyword_reason", "process_reason",
                                                  "recognized_reason", "returned_reason"})
