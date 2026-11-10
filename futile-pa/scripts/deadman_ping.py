#!/usr/bin/env python3
"""Dead man's switch: say nothing, continuously, so an outside service can
complain when the nothing stops (task 211).

On 2026-11-10 futileassistant lost power at 10:04 and stayed off until Andre
switched it on by hand at 10:53. Every alarm built before this one runs on
this machine, so all of them were off with it. This ping goes to an external
check-in monitor (healthchecks.io); when pings stop arriving, *it* emails
Andre. That is the only shape of alarm that survives the machine it watches
being switched off.

WHAT A GREEN CHECK MEANS -- and nothing more:

    this machine is powered on, its user session is running its timers,
    and it has outbound network.

It does NOT mean the assistant is healthy. The ping is unconditional on
purpose. Agent health is watched twice already: external_watchdog.py nightly
(last_run_at, last_delivered_at) and the heartbeat four times a day. Making
this ping conditional on those would import their >12h thresholds and add a
new false-alarm path. A green check here says the lights are on. Do not read
it as anything else.

Why a separate hourly timer rather than a ping from checkin.run_once(): the
check-ins fire at 07:48, 11:48, 15:48 and 19:48, so the longest healthy gap is
the overnight twelve hours. A ping tied to the check-in would need a window
over 12h, and the machine dying at 21:00 would go unnoticed until morning. An
hourly ping catches it in about two hours. The cadence of the thing being
watched must not set the resolution of the watcher.

The ping URL is a credential: whoever holds it can silence the alarm. It is
read from .env inside this process and never passed on a command line (argv
is visible in any process listing), never logged, never printed. The command
line names only the .env *key* to read.

A failed ping logs a class name or "HTTP <code>" and still exits 0. A missed
ping is the signal, and the external service already raises it. A noisy unit
failure would add nothing.

Usage:
  deadman_ping.py                        # pings DEADMAN_PING_URL (the hourly timer)
  deadman_ping.py --key BACKUP_PING_URL  # backup.sh, on a successful run only
  deadman_ping.py --env-file FILE        # tests
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request

ENV_FILE = "/home/dremon/tools/personal-context-mcp/.env"
TIMEOUT_SECONDS = 10


def read_env_value(path: str, key: str) -> str | None:
    """The one value, read without python-dotenv so this has no dependency
    and does not import the rest of .env into the environment."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(rf"^\s*{re.escape(key)}\s*=\s*(.*?)\s*$", line)
                if m:
                    return m.group(1).strip("'\"") or None
    except OSError as e:
        print(f"deadman_ping: {key}: cannot read env file ({type(e).__name__})")
    return None


def ping(url: str) -> str:
    """Returns a short outcome for the log. Never contains the URL."""
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "futile-pa-deadman"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return f"ok (HTTP {resp.status})"
    except urllib.error.HTTPError as e:
        return f"failed (HTTP {e.code})"
    except Exception as e:  # URLError, timeout, ValueError on a malformed URL
        reason = getattr(e, "reason", None)
        detail = type(reason).__name__ if isinstance(reason, BaseException) else type(e).__name__
        return f"failed ({detail})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="DEADMAN_PING_URL", help="the .env key holding the URL (not the URL)")
    ap.add_argument("--env-file", default=ENV_FILE)
    args = ap.parse_args()

    url = read_env_value(args.env_file, args.key)
    if not url:
        print(f"deadman_ping: {args.key}: not configured, nothing sent")
        return 0
    print(f"deadman_ping: {args.key}: {ping(url)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
