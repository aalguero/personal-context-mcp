"""Where a check-in run's output goes: the local log (a durable record, kept
even now) and, once Signal is configured, a real outbound message.

send() is tolerant of Signal not being configured yet — signal_bridge.send()
itself checks is_configured() and no-ops otherwise, so deploying this change
doesn't require the Docker container or the linked device to exist yet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from . import signal_bridge

LOG_PATH = Path(__file__).resolve().parent / "pending_notifications.log"
log = logging.getLogger("futile-assistant.notify")


def send(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"\n--- {stamp} ---\n{message}\n")

    if not signal_bridge.is_configured():
        log.info("Check-in produced output — see %s (Signal not configured yet)", LOG_PATH)
        return
    try:
        signal_bridge.send(message)
    except Exception:
        # The log line above already has the content — a Signal delivery
        # failure shouldn't be treated as the check-in run itself failing.
        log.exception("Failed to send check-in output over Signal — it's still in %s", LOG_PATH)
