"""The Signal front door: a `signal-cli-rest-api` container (Docker,
localhost only) is the only way in or out — see
docs/architecture/standalone-assistant.md for why. Polls for incoming
messages, checks the sender against the allowlist, runs the same on-demand
agent loop quickask.py exposes with a short rolling conversation history,
and sends the reply back through the same sensitivity filter every other
outbound Signal message goes through (notify.py included, once wired).

Separate-number mode: the bot is its own Signal account (registered
2026-11-04), not a linked device on Andre's account — a linked device would
receive every conversation and could send as him. SIGNAL_OWN_NUMBER is the
bot's number and SIGNAL_ALLOWED_SENDER is Andre's; the two must differ.

Neither number may reach a log. The receive URL contains the bot's number
and requests puts the URL into its exception messages, so every HTTP call
goes through _call(), which re-raises as SignalAPIError carrying only a
fixed label and a status code. Callers log that, never a traceback.

Deliberately tolerant of being unconfigured: this module can be deployed
and imported before the Docker container exists. is_configured() gates
both send() and run_bridge() rather than raising at import time.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from . import quickask
from .sensitivity_filter import filter_outbound

log = logging.getLogger("futile-assistant.signal")
# urllib3 logs each request path at DEBUG, and the receive path contains the
# bot's number. main.py runs at INFO so it is silent today; this keeps it
# silent if someone turns the root logger up to DEBUG to troubleshoot.
logging.getLogger("urllib3").setLevel(logging.WARNING)

API_BASE = os.environ.get("SIGNAL_API_BASE", "http://127.0.0.1:8080")
# The bot's own number, and the only sender it answers. Must differ.
OWN_NUMBER = os.environ.get("SIGNAL_OWN_NUMBER", "")
ALLOWED_SENDER = os.environ.get("SIGNAL_ALLOWED_SENDER", "")
POLL_SECONDS = 3

# Every message that actually reached Andre, in both directions — including
# scheduled check-ins sent from the scheduler thread, so a reply to one has
# the question it answers. In memory only (task 197): a restart loses
# continuity, but every question carries its task id, so not correctness.
# Written by the bridge thread and the scheduler thread; _history_lock guards
# every read-modify-write of _history and _last_message_at, and is never held
# across a model call or an HTTP call.
_history: list[dict[str, Any]] = []
_last_message_at = datetime.now(timezone.utc)
_history_lock = threading.Lock()


def is_configured() -> bool:
    return bool(OWN_NUMBER and ALLOWED_SENDER)


def _memory_reset_hours() -> float:
    # The conversation resets after this many hours of *silence* — measured
    # from the last message in either direction, not from when the
    # conversation began. (Before task 197 it was time since start, which
    # could delete a check-in question minutes before Andre answered it.)
    # Read fresh on every check, not cached at import time — Andre asked
    # specifically for this to be adjustable without a deploy.
    return float(os.environ.get("SIGNAL_MEMORY_RESET_HOURS", "24"))


class SignalAPIError(Exception):
    """A failed call to the container. The message is a fixed label plus a
    status code or exception class name — never a URL, body or number."""


def _call(label: str, method: str, url: str, **kwargs: Any) -> requests.Response:
    try:
        resp = requests.request(method, url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        raise SignalAPIError(f"{label}: HTTP {status}") from None
    except requests.RequestException as e:
        raise SignalAPIError(f"{label}: {type(e).__name__}") from None


def send(message: str) -> None:
    if not is_configured():
        log.info("Signal not configured yet (SIGNAL_OWN_NUMBER/SIGNAL_ALLOWED_SENDER unset) — not sending")
        return
    text = filter_outbound(message)
    _call("send", "POST", f"{API_BASE}/v2/send",
          json={"message": text, "number": OWN_NUMBER, "recipients": [ALLOWED_SENDER]})
    # Only after _call() returns: a message that failed to send is not part
    # of the conversation. And the filtered text, because that is what Andre
    # actually saw.
    _append("assistant", text)


def _append(role: str, content: str) -> list[dict[str, Any]]:
    """Add one turn and return a snapshot copy for the caller to read
    outside the lock. Resets first if the conversation has gone quiet, so a
    check-in after a day's silence starts a fresh conversation rather than
    reviving yesterday's."""
    global _last_message_at
    with _history_lock:
        _reset_memory_if_due()
        _history.append({"role": role, "content": content})
        _last_message_at = datetime.now(timezone.utc)
        return list(_history)


def _receive() -> list[dict[str, Any]]:
    return _call("receive", "GET", f"{API_BASE}/v1/receive/{OWN_NUMBER}").json()


def _reset_memory_if_due() -> None:
    # Caller holds _history_lock.
    global _last_message_at
    idle_hours = (datetime.now(timezone.utc) - _last_message_at).total_seconds() / 3600
    if _history and idle_hours >= _memory_reset_hours():
        log.info("Conversation memory reset (%.1fh idle, threshold %.1fh)",
                 idle_hours, _memory_reset_hours())
        _history.clear()
        _last_message_at = datetime.now(timezone.utc)


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

    snapshot = _append("user", text)
    try:
        reply = quickask.ask_with_history(snapshot)
    except Exception:
        log.exception("On-demand reply failed")
        reply = "Something went wrong on my end — try again in a moment."
    # send() records the reply — appending it here too would double it.
    send(reply)


def run_bridge() -> None:
    if not is_configured():
        log.warning("Signal bridge disabled — SIGNAL_OWN_NUMBER/SIGNAL_ALLOWED_SENDER not set")
        return
    log.info("Signal bridge starting, polling %s every %ss", API_BASE, POLL_SECONDS)
    # Log on state change only: a dead container would otherwise write a
    # line every POLL_SECONDS for as long as it stays dead.
    failing = False
    while True:
        try:
            for envelope in _receive():
                _handle_incoming(envelope)
            if failing:
                log.info("Signal receive recovered")
                failing = False
        except SignalAPIError as e:
            if not failing:
                log.warning("Signal receive failing (%s) — will keep retrying quietly", e)
                failing = True
        except Exception as e:
            # Not a traceback: an unexpected exception's message could carry
            # envelope values. The class name is enough to start from.
            log.error("Signal receive loop error: %s", type(e).__name__)
        time.sleep(POLL_SECONDS)
