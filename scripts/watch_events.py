"""Print new important journal events of a bot as they happen (for monitoring a session).

Usage:
    uv run python scripts/watch_events.py [state/demo/bot.db]
    uv run python scripts/watch_events.py state/demo/bot.db --until-event --max-minutes 120

With --until-event the script exits after the first important event (or after
--max-minutes), so a caller waiting on it wakes up only when something happened.
"""

import argparse
import time
from datetime import UTC, datetime, timedelta
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
# Expected on Demo Trading: it lacks some new symbols, and the risk rules reject entries.
NOISE = ("not a perpetual on this venue", "KeyError(", "'status': 'rejected'")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", nargs="?", default="state/demo/bot.db")
    parser.add_argument("--until-event", action="store_true")
    parser.add_argument("--max-minutes", type=float, default=0)
    args = parser.parse_args()

    store = Store(Path(args.db))
    last = max((event.id for event in store.last_events(1)), default=0)
    deadline = (
        datetime.now(UTC) + timedelta(minutes=args.max_minutes) if args.max_minutes else None
    )
    while deadline is None or datetime.now(UTC) < deadline:
        found = False
        for event in store.events(after_id=last, limit=200):
            last = event.id
            text = str(event.payload)
            if event.kind in IMPORTANT and not any(noise in text for noise in NOISE):
                print(f"{event.ts:%H:%M:%S} {event.kind} {text[:200]}", flush=True)
                found = True
        if found and args.until_event:
            return
        time.sleep(15)
    print(f"no important events until {datetime.now(UTC):%H:%M} UTC", flush=True)


if __name__ == "__main__":
    main()
