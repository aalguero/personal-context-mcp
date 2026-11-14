#!/usr/bin/env python3
"""scripts/rotate_token.py, the kill switch, on throwaway .env files. No database.

    python3 tests/test_rotate_token.py
"""
from __future__ import annotations

import pathlib
import stat
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "rotate_token.py"

passed = 0
failed: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed.append(f"{label} {detail}")
        print(f"  FAIL  {label}  {detail}")


OLD_AUTH = "zqold-auth-" + "a" * 40
OLD_SHOP = "zqold-shop-" + "b" * 40
BODY = (f"# comment line\nPGPASSWORD=zqpg\nPA_AUTH_TOKEN={OLD_AUTH}\n"
        f"PA_SHOP_TOKEN={OLD_SHOP}\r\nPA_AUTH_TOKEN_NOT=keep\nLAST=no-newline")


def run(env: pathlib.Path, var: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--var", var, "--env-file", str(env)],
                          capture_output=True, text=True)


def value(env: pathlib.Path, var: str) -> str:
    for line in env.read_text().splitlines():
        if line.startswith(f"{var}="):
            return line.split("=", 1)[1]
    return ""


with tempfile.TemporaryDirectory() as d:
    env = pathlib.Path(d) / ".env"
    env.write_text(BODY)
    env.chmod(0o640)
    out = run(env, "PA_AUTH_TOKEN")
    new = value(env, "PA_AUTH_TOKEN")
    check("exits 0", out.returncode == 0, out.stderr)
    check("the token changed", new and new != OLD_AUTH)
    check("the new token is long (>= 32, the server's floor)", len(new) >= 32, str(len(new)))
    check("the old token is nowhere in the file", OLD_AUTH not in env.read_text())
    check("the file is mode 600 (was 640)", stat.S_IMODE(env.stat().st_mode) == 0o600,
          oct(stat.S_IMODE(env.stat().st_mode)))
    want = BODY.replace(f"PA_AUTH_TOKEN={OLD_AUTH}", f"PA_AUTH_TOKEN={new}")
    check("every other line is byte-for-byte unchanged (CRLF and no-final-newline included)",
          env.read_bytes() == want.encode(), repr(env.read_bytes()[-80:]))
    check("the similarly named PA_AUTH_TOKEN_NOT was not touched", value(env, "PA_AUTH_TOKEN_NOT") == "keep")
    printed = out.stdout + out.stderr
    check("neither the old nor the new value was printed", OLD_AUTH not in printed and new not in printed)
    check("positive control: the new value is real text the check could have found",
          new in env.read_text() and len(new) > 0)
    out = run(env, "PA_SHOP_TOKEN")
    check("PA_SHOP_TOKEN rotates too, keeping its CRLF",
          out.returncode == 0 and value(env, "PA_SHOP_TOKEN") not in ("", OLD_SHOP)
          and b"\r\n" in env.read_bytes() and value(env, "PA_AUTH_TOKEN") == new)
    no_temp = [p.name for p in pathlib.Path(d).iterdir() if p.name.startswith(".env-rotate-")]
    check("no temp file left behind", not no_temp, str(no_temp))

    for label, body in (("absent", "PGPASSWORD=x\n"),
                        ("duplicated", f"PA_AUTH_TOKEN={OLD_AUTH}\nPA_AUTH_TOKEN={OLD_AUTH}\n")):
        env.write_text(body)
        before = env.read_bytes()
        out = run(env, "PA_AUTH_TOKEN")
        check(f"{label}: refuses", out.returncode != 0 and "REFUSING" in out.stdout + out.stderr,
              out.stdout + out.stderr)
        check(f"{label}: the file is unchanged", env.read_bytes() == before)
    check("an unknown variable is refused by argument parsing",
          run(env, "PGPASSWORD").returncode != 0)

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
