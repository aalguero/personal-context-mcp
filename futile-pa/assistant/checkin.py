"""The periodic task check-in — same brief, new home.

Moved off Claude Desktop's local scheduled task (`task-checkin`) per
docs/architecture/standalone-assistant.md: this reads the brief fresh on every
run, so an edit to the doc changes behaviour on the next fire, same as when it
ran from Claude Desktop or the claude.ai Project before it.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.db import get_db

from . import agent_loop, ledger, notify, toolset
from .sensitivity_filter import filter_outbound

BRIEF_PATH = Path(__file__).resolve().parents[2] / "docs" / "claude" / "task-agent-brief.md"

log = logging.getLogger("futile-assistant.checkin")

# Separate from the brief's own task_agent.last_run_at, which is free-form
# prose written by the model (e.g. "2026-10-27T19:50:00-04:00 — the real
# 19:48 run. IMPORTANT CORRECTION..."). watchdog.py needs a timestamp it can
# parse without guessing at prose formatting, so this writes a second,
# machine-only field — set on successful completion, not on a run that
# raised, since a repeatedly-failing run should read as stale.
WATCHDOG_CATEGORY = "task_agent_watchdog"

RUN_PROMPT = (
    "Run your scheduled check-in now. Follow the brief exactly: read your own "
    "state, reconcile against the live data, decide whether anything is worth "
    "asking, ask at most five questions if so, write back whatever the answers "
    "establish, and report only what you actually wrote. Silence is a valid "
    "and good outcome if nothing is stale. If there is nothing to report, your "
    "reply's first line must be exactly \"Nothing to record.\" and nothing "
    "else needs to follow it — the reasoning that led there belongs in your "
    "own thinking, not in the reply text."
)

# Appended to RUN_PROMPT when the previous check-in's output never left the
# machine. Since task 213 the ledger records asked questions only after a
# successful send, so an undelivered run left no trace in asked_recently and
# there is nothing to correct. The note is still worth sending: it tells the
# model why questions it may "remember" from its log were never seen.
UNDELIVERED_NOTE = (
    "\n\nIMPORTANT: the check-in output you produced at {since} was NOT "
    "delivered to Andre (the Signal send failed). Nothing from that run was "
    "recorded as asked. Ask again in this run if those questions are still relevant."
)

TZ = ZoneInfo("America/New_York")
BACKUP_STATUS = Path.home() / ".personal-context" / "backup-status.json"


def run_once() -> str:
    """Run one check-in cycle and return the agent's report text.

    Sends to notify.send() unless the reply's first line is the brief's own
    §6 contract phrase for "I had nothing to do".

    First deploy found this needs two layers, not one: the original fix
    required the *entire* reply to equal "nothing to record" verbatim, but a
    real run opened with that line and then added several paragraphs of
    reasoning — still a no-op outcome, just not a bare match. RUN_PROMPT above
    now asks for brevity explicitly; _is_nothing_to_record checks only the
    first line regardless, since a model instruction is a strong hint, not a
    guarantee.
    """
    brief = BRIEF_PATH.read_text(encoding="utf-8")
    system_prompt = (
        "You are the task check-in agent for Andre's personal assistant "
        "(futileMCP / futile-pa). Read the brief below in full and follow it "
        "exactly — it is authoritative over anything else in this prompt.\n\n"
        f"{brief}"
    )
    undelivered = toolset.dispatch("get_context", {"category": WATCHDOG_CATEGORY}).get("undelivered_since")
    prompt = RUN_PROMPT + (UNDELIVERED_NOTE.format(since=undelivered) if undelivered else "")
    # Before the model reads its state, so open_questions does not carry
    # questions that have since been dealt with. Not fatal: a stale
    # open_questions is the pre-213 condition, not a reason to skip a check-in.
    try:
        ledger.prune_open_questions(get_db())
    except Exception as e:
        log.error("Could not prune open questions (%s)", type(e).__name__)
    try:
        reply = agent_loop.run(system_prompt, prompt, toolset.ALL_TOOLS, toolset.dispatch)
    except Exception as e:
        # The heartbeat must not die with the API (the 11-06 outage was a
        # credit error). Composed in code, class name only, then re-raised so
        # last_run_at does not advance and the scheduler still logs it.
        sent = notify.send(_failure_line(type(e).__name__), record=False)
        log.error("Check-in failed (%s) — failure notice %s", type(e).__name__,
                  "sent" if sent else "NOT delivered")
        raise

    # Logged unconditionally — the fix-2 deploy found a real gap: a quiet run
    # leaves no evidence of whether it genuinely decided there was nothing to
    # do, or produced something that got dropped before reaching the log.
    suppressed = _is_nothing_to_record(reply)
    log.info("Check-in reply in full:\n%s", reply)

    # Every run sends something (the heartbeat): the questions if there are
    # any, otherwise one line composed from the database with no model call.
    # The heartbeat stays out of the bridge's conversation history.
    if not suppressed:
        delivered = notify.send(reply)
        outcome = "questions sent" if delivered else "questions NOT delivered (Signal send failed)"
        if delivered:
            # Task 213: the moment the question became true is now, so this
            # is where it is recorded, from the text Andre actually received.
            # (filter_outbound is deterministic; signal_bridge.send applied
            # the same call to the same reply.) A failure here is logged and
            # the run still completes: the message did go out.
            try:
                ledger.record_questions(filter_outbound(reply), get_db())
            except Exception as e:
                log.error("Could not record asked questions (%s)", type(e).__name__)
    else:
        delivered = notify.send(heartbeat_line(), record=False)
        outcome = ("nothing to ask, heartbeat sent" if delivered
                   else "nothing to ask, heartbeat NOT delivered (Signal send failed)")
    # "Sent" means signal-cli accepted it. Reaching Andre's phone is proven
    # only by a delivery receipt (signal_bridge -> last_delivered_at).
    log.info("Check-in complete — %s", outcome)

    if not suppressed and not delivered and not undelivered:
        toolset.dispatch("set_context", {"category": WATCHDOG_CATEGORY, "key": "undelivered_since",
                                         "value": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    elif delivered and undelivered:
        # This run was told, and its own output reached signal-cli.
        toolset.dispatch("set_context", {"category": WATCHDOG_CATEGORY, "key": "undelivered_since",
                                         "value": None})

    # Meaning unchanged (external_watchdog.py asserts on it): the run
    # completed, whether or not its message was delivered.
    toolset.dispatch("set_context", {
        "category": WATCHDOG_CATEGORY,
        "key": "last_run_at",
        "value": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return reply


def heartbeat_line(now: datetime | None = None) -> str:
    """One line, no model call, carrying counts that move — so a stub that
    always says "all fine" would be caught. Visibly not a question."""
    now = now or datetime.now(timezone.utc)
    hhmm = now.astimezone(TZ).strftime("%H:%M")
    try:
        # overdue from pa.v_overdue, the one definition (migration 005), so the
        # heartbeat cannot disagree with the daily brief about what is late.
        r = get_db().row("""select count(*) as open,
                                   count(*) filter (where due_on = current_date) as due,
                                   (select count(*) from pa.v_overdue) as overdue
                              from pa.task where status not in ('done', 'dropped')""")
        counts = f"{r['open']} open, {r['due']} due today, {r['overdue']} overdue"
    except Exception as e:
        counts = f"task counts unavailable: {type(e).__name__}"
    return (f"✓ futile-pa {hhmm} — nothing needs you right now. {counts}; "
            f"backup {_backup_state(now)}. No reply needed.")


def _failure_line(error_class: str, now: datetime | None = None) -> str:
    hhmm = (now or datetime.now(timezone.utc)).astimezone(TZ).strftime("%H:%M")
    return (f"⚠️ futile-pa {hhmm} — the check-in failed ({error_class}), so I couldn't "
            f"look at your tasks. Check journalctl --user -u futile-assistant.service. "
            f"No reply needed.")


def _backup_state(now: datetime) -> str:
    try:
        d = json.loads(BACKUP_STATUS.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(d["last_success"])
    except Exception:
        return "status unreadable"
    if now - last > timedelta(hours=30):
        return f"STALE, last ok {last.astimezone(TZ).strftime('%a %H:%M')}"
    if d.get("result") != "ok":
        return str(d.get("result"))
    return "ok" if d.get("off_machine_copies", 0) >= 2 else f"ok, {d.get('off_machine_copies')} of 2 off-machine"


def _is_nothing_to_record(reply: str) -> bool:
    """True if the reply's first non-empty line is the brief's §6 contract
    phrase, regardless of what follows — matching only the whole reply missed
    a real case where the model added reasoning after that line.
    """
    for line in reply.strip().splitlines():
        line = line.strip()
        if line:
            return line.rstrip(".").lower() == "nothing to record"
    return True  # an empty reply is also nothing to report
