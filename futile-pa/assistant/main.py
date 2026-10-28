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

from pa.db import get_db

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
              [f"{h:02d}:{m:02d}" for h, m in sorted(FIRE_MINUTE_BY_HOUR.items())])
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
    # The one-shot commands below close the DB pool before exiting — found by
    # the first real deploy: an unclosed pool leaves background threads
    # running, and the interpreter waits ~20s (4 workers x 5s) trying to stop
    # them on exit. The long-running scheduler never reaches this, since it
    # runs forever and the pool should stay open for its whole lifetime.
    if len(sys.argv) >= 2 and sys.argv[1] == "ask":
        message = " ".join(sys.argv[2:]) or input("Ask> ")
        print(quickask.ask(message))
        get_db().close()
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "checkin-once":
        print(checkin.run_once())
        get_db().close()
        return
    run_scheduler()


if __name__ == "__main__":
    main()
