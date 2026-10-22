"""Print new important journal events of the bots as they happen (for monitoring).

Usage:
    uv run python scripts/watch_events.py                      # live and demo
    uv run python scripts/watch_events.py state/demo/bot.db --until-event --max-minutes 120

With --until-event the script exits after the first important event, a bot whose status
has not been updated for STALE (stopped or hung), or after --max-minutes, so a caller
waiting on it wakes up only when something happened.
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
    "entry_rejected",
    "announcements_down",
    "announcements_restored",
    "account_access_lost",
    "account_access_restored",
    "engine_refused",
}
# Routine: printed, but do not end a --until-event wait.
ROUTINE = {"stop_moved"}
# Expected on Demo Trading: it lacks some new symbols, and the risk rules reject entries.
NOISE = ("not a perpetual on this venue", "KeyError(", "'status': 'rejected'")
STALE = timedelta(minutes=10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dbs", nargs="*", default=["state/live/bot.db", "state/demo/bot.db"])
    parser.add_argument("--until-event", action="store_true")
    parser.add_argument("--max-minutes", type=float, default=0)
    args = parser.parse_args()

    stores = {Path(db).parent.name: Store(Path(db)) for db in args.dbs if Path(db).exists()}
    last = {
        env: max((event.id for event in store.last_events(1)), default=0)
        for env, store in stores.items()
    }
    deadline = datetime.now(UTC) + timedelta(minutes=args.max_minutes) if args.max_minutes else None
    while deadline is None or datetime.now(UTC) < deadline:
        found = False
        for env, store in stores.items():
            for event in store.events(after_id=last[env], limit=200):
                last[env] = event.id
                text = str(event.payload)
                if event.kind in IMPORTANT and not any(noise in text for noise in NOISE):
                    print(f"{env} {event.ts:%H:%M:%S} {event.kind} {text[:200]}", flush=True)
                    found = found or event.kind not in ROUTINE
            status = store.get("status") or {}
            updated = status.get("time")
            age = datetime.now(UTC) - datetime.fromisoformat(updated) if updated else None
            if age is None or age > STALE:
                print(f"{env} status not updated for {age}: is the bot running?", flush=True)
                found = True
        if found and args.until_event:
            return
        time.sleep(15)
    print(f"no important events until {datetime.now(UTC):%H:%M} UTC", flush=True)


if __name__ == "__main__":
    main()
