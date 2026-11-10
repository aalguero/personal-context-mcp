"""Where a check-in run's output goes: the local log (a durable record, kept
even now) and, once Signal is configured, a real outbound message.

send() returns whether Signal accepted the message, and the caller has to
look (task 205). It used to swallow a failed send and return normally, so
checkin.run_once() logged "produced output" and the watchdog stamped a
successful run when not one question had left the machine. A return value
rather than a propagated exception: the check-in's model work is done and
written by the time this runs, and raising would make the caller skip
last_run_at and read as "agent not running", which is the wrong diagnosis.

True means signal-cli *accepted* the message, not that Andre's phone has it.
Delivery is proven only by a receipt (signal_bridge stamps last_delivered_at).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import signal_bridge

LOG_PATH = Path(__file__).resolve().parent / "pending_notifications.log"
log = logging.getLogger("futile-assistant.notify")

# Seconds to wait before each retry. 5 + 20 covers a container restart
# (healthy again in ~25s, observed 11-09) without holding the scheduler
# thread long.
RETRY_DELAYS = (5, 20)


def send(message: str, *, record: bool = True) -> bool:
    """Log the message locally, then send it over Signal with retries.

    record=False keeps it out of the bridge's conversation history: the
    heartbeat is not conversation, and four a day would otherwise stop the
    idle-based memory reset from ever firing.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"\n--- {stamp} ---\n{message}\n")

    if not signal_bridge.is_configured():
        log.info("Check-in produced output — see %s (Signal not configured yet)", LOG_PATH)
        return False

    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        try:
            signal_bridge.send(message, record=record)
            return True
        except Exception as e:
            # No traceback: see signal_bridge's docstring on keeping numbers
            # out of logs. SignalAPIError's text is safe; anything else gets
            # its class name only.
            reason = str(e) if isinstance(e, signal_bridge.SignalAPIError) else type(e).__name__
            # A read timeout is ambiguous -- the container may have sent it --
            # so it is not retried: a duplicate question is worse than one
            # recorded as undelivered and re-asked next run.
            if attempt < attempts and "Timeout" not in reason:
                log.warning("Signal send attempt %d/%d failed (%s) — retrying in %ss",
                            attempt, attempts, reason, RETRY_DELAYS[attempt - 1])
                time.sleep(RETRY_DELAYS[attempt - 1])
                continue
            log.error("Signal send failed after %d attempt(s) (%s) — message is in %s",
                      attempt, reason, LOG_PATH)
            return False
    return False  # unreachable; keeps the return type honest
