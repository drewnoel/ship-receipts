#!/usr/bin/env python3
"""PostToolUse hook (matcher: Bash): when a `git commit` runs, log the new commit as a receipt candidate.

Reads the hook JSON on stdin, uses its `cwd`, runs `git -C <cwd> log -1`, appends to receipts.jsonl.
No network. Always exits 0 and prints nothing, so it can never block or clutter a session.
"""
from __future__ import annotations

import json
import re
import sys

# `git commit` only in command position: start of the line or after ; && || | ( $( , with optional VAR=value
# prefixes and -C/-c options. "echo git commit" or "grep 'git commit'" do not count.
COMMIT_RX = re.compile(r"(?:^|[;&|(]|\$\()\s*(?:\w+=\S*\s+)*git(?:\s+-C\s+\S+|\s+-c\s+\S+)*\s+commit\b",
                       re.M)


def is_commit(payload: dict) -> bool:
    if payload.get("tool_name") != "Bash":
        return False
    cmd = str((payload.get("tool_input") or {}).get("command", ""))
    return bool(COMMIT_RX.search(cmd)) and "--dry-run" not in cmd


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not is_commit(payload):
            return 0
        import _common  # noqa: F401  (puts tools/ on sys.path)
        import receipts
        cwd = str(payload.get("cwd") or ".")
        m = re.search(r"git\s+-C\s+(\S+)", str(payload["tool_input"].get("command", "")))
        if m:
            cwd = m.group(1).strip("\"'")
        receipts.receipt_from_commit(receipts.Ctx(), cwd)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
