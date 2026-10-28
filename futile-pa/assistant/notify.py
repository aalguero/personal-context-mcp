"""Where a check-in run's output goes until Phase 2 wires up Signal.

Phase 1 has no live transport, so this appends to a local log Andre can read
by hand while verifying the scheduler and the check-in logic. Phase 2 (the
Signal bridge) replaces send() with a real outbound message — nothing else in
this package should need to change, which is the point of isolating it here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "pending_notifications.log"
log = logging.getLogger("futile-assistant.notify")


def send(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"\n--- {stamp} ---\n{message}\n")
    log.info("Check-in produced output — see %s (Signal not wired up yet, Phase 2)", LOG_PATH)
