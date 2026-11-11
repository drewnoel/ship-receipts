#!/usr/bin/env python3
"""SessionStart / Stop hook: one line when the weekly posting floor is at risk or the idea queue runs low.

  --event session-start   plain stdout (Claude Code adds it to the session context)
  --event stop            JSON {"systemMessage": ...} (shown to you; never blocks the stop)

Honors quiet hours and a throttle (nudges.throttle_hours) from your config. Optional desktop notification
(nudges.desktop: true) via osascript on macOS or notify-send on Linux. No network. Always exits 0.
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    try:
        ap = argparse.ArgumentParser()
        ap.add_argument("--event", choices=["session-start", "stop"], default="session-start")
        ns, _ = ap.parse_known_args()
        try:
            sys.stdin.read()  # hook payload; not needed, but drain it
        except Exception:
            pass
        import _common  # noqa: F401
        import receipts
        ctx = receipts.Ctx()
        msg = receipts.nudge_message(ctx)
        if not msg:
            return 0
        if (ctx.cfg.get("nudges") or {}).get("desktop"):
            receipts.desktop_notify(msg)
        if ns.event == "stop":
            print(json.dumps({"systemMessage": msg}))
        else:
            print(msg)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
