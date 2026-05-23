from __future__ import annotations

import sys
import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ORIGINAL_GET_RUNNING_LOOP = asyncio.get_running_loop
_FALLBACK_LOOP = asyncio.new_event_loop()


def _get_running_loop_for_discord_view_tests():
    try:
        return _ORIGINAL_GET_RUNNING_LOOP()
    except RuntimeError:
        return _FALLBACK_LOOP


asyncio.get_running_loop = _get_running_loop_for_discord_view_tests
