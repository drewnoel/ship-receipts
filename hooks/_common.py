"""Shared bits for the hook scripts: locate tools/receipts.py and never fail the session."""
from __future__ import annotations

import os
import sys
from pathlib import Path

TOOLS = Path(os.environ.get("SHIP_RECEIPTS_TOOLS") or Path(__file__).resolve().parent.parent / "tools")
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
