"""The Signal front door: a `signal-cli-rest-api` container (Docker,
localhost only) is the only way in or out — see
docs/architecture/standalone-assistant.md for why. Polls for incoming
messages, checks the sender against the allowlist, runs the same on-demand
agent loop quickask.py exposes with a short rolling conversation history,
and sends the reply back through the same sensitivity filter every other
outbound Signal message goes through (notify.py included, once wired).

Deliberately tolerant of being unconfigured: this module can be deployed
and imported before the Docker container or the linked device exist, so
Phase 1 keeps working while Phase 2 is being set up. is_configured() gates
both send() and run_bridge() rather than raising at import time.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

from . import quickask
from .sensitivity_filter import filter_outbound

log = logging.getLogger("futile-assistant.signal")

API_BASE = os.environ.get("SIGNAL_API_BASE", "http://127.0.0.1:8080")
# Linked-device mode (the decided default): signal-cli operates as Andre's
# own number, so OWN_NUMBER and ALLOWED_SENDER end up holding the same
# value. Kept as two names anyway — a separately registered number would
# need them to differ, and that's a config change, not a code change.
OWN_NUMBER = os.environ.get("SIGNAL_OWN_NUMBER", "")
ALLOWED_SENDER = os.environ.get("SIGNAL_ALLOWED_SENDER", "")
POLL_SECONDS = 3

_history: list[dict[str, Any]] = []
_history_started_at = datetime.now(timezone.utc)


def is_configured() -> bool:
    return bool(OWN_NUMBER and ALLOWED_SENDER)


def _memory_reset_hours() -> float:
    # Read fresh on every check, not cached at import time — Andre asked
    # specifically for this to be adjustable without a deploy.
    return float(os.environ.get("SIGNAL_MEMORY_RESET_HOURS", "24"))


def send(message: str) -> None:
    if not is_configured():
        log.info("Signal not configured yet (SIGNAL_OWN_NUMBER/SIGNAL_ALLOWED_SENDER unset) — not sending")
        return
    text = filter_outbound(message)
    resp = requests.post(
        f"{API_BASE}/v2/send",
        json={"message": text, "number": OWN_NUMBER, "recipients": [ALLOWED_SENDER]},
        timeout=30,
    )
    resp.raise_for_status()


def _receive() -> list[dict[str, Any]]:
    resp = requests.get(f"{API_BASE}/v1/receive/{OWN_NUMBER}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _reset_memory_if_due() -> None:
    global _history, _history_started_at
    age_hours = (datetime.now(timezone.utc) - _history_started_at).total_seconds() / 3600
    if age_hours >= _memory_reset_hours():
        log.info("Conversation memory reset (%.1fh elapsed, threshold %.1fh)",
                  age_hours, _memory_reset_hours())
        _history = []
        _history_started_at = datetime.now(timezone.utc)


def _extract_text(envelope: dict[str, Any]) -> tuple[str | None, str | None]:
    """Returns (source_number, message_text), either of which may be None —
    receipts, typing indicators and read markers have no dataMessage.text
    and should be silently skipped, not treated as empty questions.
    """
    data = envelope.get("envelope", envelope)
    source = data.get("source") or data.get("sourceNumber")
    text = (data.get("dataMessage") or {}).get("message")
    return source, text


def _handle_incoming(envelope: dict[str, Any]) -> None:
    source, text = _extract_text(envelope)
    if not text:
        return
    if source != ALLOWED_SENDER:
        log.warning("Ignored a message from a non-allowed sender")
        return

    _reset_memory_if_due()
    _history.append({"role": "user", "content": text})
    try:
        reply = quickask.ask_with_history(_history)
    except Exception:
        log.exception("On-demand reply failed")
        reply = "Something went wrong on my end — try again in a moment."
    _history.append({"role": "assistant", "content": reply})
    send(reply)


def run_bridge() -> None:
    if not is_configured():
        log.warning("Signal bridge disabled — SIGNAL_OWN_NUMBER/SIGNAL_ALLOWED_SENDER not set")
        return
    log.info("Signal bridge starting, polling %s every %ss", API_BASE, POLL_SECONDS)
    while True:
        try:
            for envelope in _receive():
                _handle_incoming(envelope)
        except Exception:
            log.exception("Signal receive loop error")
        time.sleep(POLL_SECONDS)
