#!/usr/bin/env python3
"""The kill switch: replace an MCP bearer token in .env, without printing it.

    rotate_token.py --var PA_AUTH_TOKEN [--env-file PATH]
    rotate_token.py --var PA_SHOP_TOKEN [--env-file PATH]

Why it exists (Andre, 2026-11-13): context tiers are scoping, not security.
Anyone holding PA_AUTH_TOKEN can read every category by name, so the defence
against a lost phone, a leaked connector URL or a compromised account is
making the old token worthless quickly. This does that in one command, and
was proven on a copy of .env before it was ever needed.

What it does: generates a new token (secrets.token_urlsafe(48), 64
characters), and rewrites .env atomically (temp file in the same directory,
mode 600, fsync, os.replace), changing only that one line. Every other line
stays byte-for-byte. It refuses, changing nothing, if the variable is absent
or appears more than once.

What it does not do, on purpose:
  * print either the old or the new value (hard rule 1). The new one is read
    from .env by whoever updates a client;
  * keep a copy of the old value. The point is for it to stop working;
  * restart the server. The old token works until
        systemctl --user restart personal-context-mcp.service
    and after that the clients (claude.ai connector, Claude Code's
    ~/.claude.json on shadow, Home Assistant for PA_SHOP_TOKEN) need the new
    value before they work again.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import secrets
import sys
import tempfile

DEFAULT_ENV = pathlib.Path(__file__).resolve().parents[2] / ".env"
ALLOWED = ("PA_AUTH_TOKEN", "PA_SHOP_TOKEN")


def rotate(env_file: pathlib.Path, var: str) -> None:
    raw = env_file.read_bytes()
    lines = raw.splitlines(keepends=True)
    prefix = f"{var}=".encode()
    hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f"REFUSING: {var} appears {len(hits)} times in {env_file}; expected exactly once. "
                         "Nothing changed.")
    i = hits[0]
    old_line = lines[i]
    ending = old_line[len(old_line.rstrip(b"\r\n")):]
    new = secrets.token_urlsafe(48)
    lines[i] = prefix + new.encode() + ending
    payload = b"".join(lines)

    fd, tmp = tempfile.mkstemp(dir=str(env_file.parent), prefix=".env-rotate-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, env_file)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(env_file, 0o600)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--var", required=True, choices=ALLOWED)
    ap.add_argument("--env-file", default=str(DEFAULT_ENV))
    a = ap.parse_args()
    env_file = pathlib.Path(a.env_file)
    rotate(env_file, a.var)
    print(f"rotated {a.var} in {env_file} (mode 600); no value printed.")
    print("The old token works until: systemctl --user restart personal-context-mcp.service")
    print("Then give each client the new value, read from .env directly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
