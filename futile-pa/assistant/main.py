"""Entry point.

With no arguments: runs the periodic check-in scheduler forever (the
deployed mode — see systemd/futile-assistant.service).

For manual verification before Signal exists (Phase 2):
    python -m assistant.main checkin-once      # run one check-in cycle now
    python -m assistant.main ask "..."         # one on-demand exchange
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import checkin, quickask

# CRON_TZ=America/New_York 48 7,11,15,19 * * * from task-agent-brief.md,
# reimplemented directly rather than via a cron-string parser — four fixed
# fire times is simpler to read and verify than a dependency to express it.
TZ = ZoneInfo("America/New_York")
FIRE_MINUTE_BY_HOUR = {7: 48, 11: 48, 15: 48, 19: 48}
POLL_SECONDS = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("futile-assistant")


def _due(now: datetime) -> bool:
    return FIRE_MINUTE_BY_HOUR.get(now.hour) == now.minute


def run_scheduler() -> None:
    log.info("Scheduler starting — fires daily at %s America/New_York",
              sorted(f"{h}:{m:02d}" for h, m in FIRE_MINUTE_BY_HOUR.items()))
    # Tracks the (y, m, d, h, min) of the last fire so a 30s poll tick cannot
    # fire the same minute twice, without needing a separate "already ran
    # today" flag that would need its own reset-at-midnight logic.
    last_fired: tuple[int, int, int, int, int] | None = None
    while True:
        now = datetime.now(TZ)
        marker = (now.year, now.month, now.day, now.hour, now.minute)
        if _due(now) and marker != last_fired:
            last_fired = marker
            log.info("Firing check-in for %s", now.isoformat())
            try:
                checkin.run_once()
            except Exception:
                log.exception("Check-in run failed")
        time.sleep(POLL_SECONDS)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "ask":
        message = " ".join(sys.argv[2:]) or input("Ask> ")
        print(quickask.ask(message))
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "checkin-once":
        print(checkin.run_once())
        return
    run_scheduler()


if __name__ == "__main__":
    main()
