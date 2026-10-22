"""Print new important journal events of a bot as they happen (for monitoring a session).

Usage: uv run python scripts/watch_events.py [state/demo/bot.db]
"""

import sys
import time
from pathlib import Path

from marketbot.state.store import Store

IMPORTANT = {
    "strategy_entry",
    "entry_filled",
    "position_closed",
    "position_gone",
    "stop_moved",
    "stop_failed",
    "stop_missing",
    "mode_changed",
    "strategy_error",
    "reconcile_unknown_position",
    "session_lost",
    "engine_stopped",
    "project_paused",
    "delisting_announced",
    "flatten_error",
}


def main() -> None:
    store = Store(Path(sys.argv[1] if len(sys.argv) > 1 else "state/demo/bot.db"))
    last = max((event.id for event in store.last_events(1)), default=0)
    while True:
        for event in store.events(after_id=last, limit=200):
            last = event.id
            if event.kind in IMPORTANT:
                print(f"{event.ts:%H:%M:%S} {event.kind} {str(event.payload)[:200]}", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
