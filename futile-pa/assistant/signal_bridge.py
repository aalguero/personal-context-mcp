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

from . import context_store, quickask
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


def send(message: str, *, record: bool = True) -> None:
    if not is_configured():
        log.info("Signal not configured yet (SIGNAL_OWN_NUMBER/SIGNAL_ALLOWED_SENDER unset) — not sending")
        return
    text = filter_outbound(message)
    _call("send", "POST", f"{API_BASE}/v2/send",
          json={"message": text, "number": OWN_NUMBER, "recipients": [ALLOWED_SENDER]})
    # Only after _call() returns: a message that failed to send is not part
    # of the conversation. And the filtered text, because that is what Andre
    # actually saw. record=False is the heartbeat (see notify.send).
    if record:
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
    Tolerates any shape: the bridge polls every 3s, and one malformed
    envelope must not cost the ones behind it.
    """
    data = envelope.get("envelope", envelope) if isinstance(envelope, dict) else None
    if not isinstance(data, dict):
        return None, None
    source = data.get("source") or data.get("sourceNumber")
    message = data.get("dataMessage")
    text = message.get("message") if isinstance(message, dict) else None
    return (source if isinstance(source, str) else None,
            text if isinstance(text, str) else None)


# ---------------------------------------------------------------------------
# Delivery receipts (task 205). Signal sends one automatically when Andre's
# device receives a message, and unlike read receipts he cannot switch them
# off. Any delivery receipt from him stamps task_agent_watchdog.
# last_delivered_at with *now*: proof that the channel to his phone was alive
# when the receipt arrived, which external_watchdog.py checks nightly. It is
# deliberately not matched to a specific send: it proves "a message from us
# reached his device", not "this check-in was delivered".
#
# Only this thread, inside the long-running service, ever stamps it. A
# separate process (checkin-once, signal-send) starts no bridge and never
# sees a receipt, so a manual run cannot make the channel look alive.
#
# Observed shape (bbernhard/signal-cli-rest-api, MODE=normal, 2026-11-09):
# envelope.receiptMessage = {when, isDelivery, isRead, isViewed, timestamps}.
# ---------------------------------------------------------------------------
DELIVERY_STAMP_MIN_SECONDS = 60   # a burst of receipts is one stamp, not N writes
_delivery_lock = threading.Lock()
_last_delivery_stamp: datetime | None = None


def _is_delivery_receipt(envelope: Any) -> bool:
    data = envelope.get("envelope", envelope) if isinstance(envelope, dict) else None
    receipt = data.get("receiptMessage") if isinstance(data, dict) else None
    return isinstance(receipt, dict) and receipt.get("isDelivery") is True


def _stamp_delivered() -> None:
    global _last_delivery_stamp
    now = datetime.now(timezone.utc)
    with _delivery_lock:
        if (_last_delivery_stamp is not None
                and (now - _last_delivery_stamp).total_seconds() < DELIVERY_STAMP_MIN_SECONDS):
            return
        _last_delivery_stamp = now
    # Outside the lock: the store write is file I/O.
    try:
        context_store.set("task_agent_watchdog", "last_delivered_at", now.isoformat(timespec="seconds"))
        # No sender, no message timestamp, no content -- a receipt is
        # metadata about a conversation and is treated like content.
        log.info("Delivery confirmed by receipt — last_delivered_at advanced")
    except Exception as e:
        with _delivery_lock:
            _last_delivery_stamp = None   # let the next receipt try again
        log.error("Could not record a delivery receipt (%s)", type(e).__name__)


def _handle_incoming(envelope: dict[str, Any]) -> None:
    source, text = _extract_text(envelope)
    if _is_delivery_receipt(envelope):
        # Never a reply, never history, never "a message from Andre".
        if source == ALLOWED_SENDER:
            _stamp_delivered()
        return
    if not text:
        return
    if source != ALLOWED_SENDER:
        log.warning("Ignored a message from a non-allowed sender")
        return

    snapshot = _append("user", text)
    try:
        reply = quickask.ask_with_history(snapshot)
    except Exception as e:
        # Class name only: model and tool errors can carry task content.
        log.error("On-demand reply failed (%s)", type(e).__name__)
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
            envelopes = _receive()
            for envelope in envelopes if isinstance(envelopes, list) else []:
                # Per envelope, so one that raises can't drop the rest of
                # the batch -- they are already consumed from the container.
                try:
                    _handle_incoming(envelope)
                except Exception as e:
                    log.error("Signal envelope handling error: %s", type(e).__name__)
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
