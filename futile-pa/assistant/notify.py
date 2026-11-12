"""Where a check-in run's output goes: a real outbound Signal message, and,
only when that does not happen, a local file holding what was not delivered.

The file is for failures only (2026-11-12, Andre's decision). Until then it
recorded every message, unfiltered, before Signal was even tried — a leftover
from before Signal worked, when it was the only output. That put every
check-in question, including ones about tasks in health, finance or estate
projects, in a mode-664 file beside the code. Now a message is written only
when Signal is unconfigured or the send failed, after filter_outbound (the
same filter signal_bridge.send applies, so the file holds what Andre would
have received), to a file kept at mode 600. If the filter itself fails, the
message is withheld rather than written raw. A failed run is re-asked on the
next one anyway (checkin.UNDELIVERED_NOTE).

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
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import signal_bridge
from .sensitivity_filter import filter_outbound

LOG_PATH = Path(__file__).resolve().parent / "pending_notifications.log"
log = logging.getLogger("futile-assistant.notify")

# Seconds to wait before each retry. 5 + 20 covers a container restart
# (healthy again in ~25s, observed 11-09) without holding the scheduler
# thread long.
RETRY_DELAYS = (5, 20)


def _keep_undelivered(message: str) -> None:
    """Append a message that did not go out, filtered, to a mode-600 file."""
    try:
        text = filter_outbound(message)
    except Exception as e:
        # A filter that cannot run (it reads pa.v_task) must not become a
        # reason to write the raw text.
        text = f"(message withheld: the outbound filter failed with {type(e).__name__})"
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)  # a file created before 2026-11-12 is 664
        os.write(fd, f"\n--- {stamp} ---\n{text}\n".encode("utf-8"))
    finally:
        os.close(fd)


def send(message: str, *, record: bool = True) -> bool:
    """Send the message over Signal with retries; keep it locally only if it
    did not go out.

    record=False keeps it out of the bridge's conversation history: the
    heartbeat is not conversation, and four a day would otherwise stop the
    idle-based memory reset from ever firing.
    """
    if not signal_bridge.is_configured():
        _keep_undelivered(message)
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
            _keep_undelivered(message)
            log.error("Signal send failed after %d attempt(s) (%s) — message is in %s",
                      attempt, reason, LOG_PATH)
            return False
    return False  # unreachable; keeps the return type honest
