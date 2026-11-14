#!/usr/bin/env python3
"""The shop-scoped token, against a real running server (task 212 phase 4).

Andre's decision, 2026-11-13: Home Assistant's voice path gets PA_SHOP_TOKEN,
which reaches only pa_shop_add and pa_shop_list. PA_AUTH_TOKEN reaches every
tool, medical and financial tasks included, and HA sits behind a microphone
anyone in the room can talk to.

The property is about what a running server allows, so this starts server.py
--http as a subprocess on a free port, against a scratch database, and talks to
it with the MCP SDK's own client. Every request goes through the real
middleware, the real session managers and uvicorn's real logging setup. The
server's stderr is captured to prove no token reaches the log.

Run:  PA_DATABASE_URL=... python3 tests/test_server_scopes.py
Refuses any database but pa_verify / pa_scratch*. Writes one zqscope-prefixed
shopping item and removes it at exit. Tokens are random per run and never
printed.
"""
from __future__ import annotations

import asyncio
import atexit
import os
import pathlib
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
SERVER = REPO / "server.py"
sys.path.insert(0, str(HERE.parent))


def _require_scratch_database() -> str:
    url = os.environ.get("PA_DATABASE_URL") or ""
    name = urlparse(url).path.lstrip("/")
    if not (name == "pa_verify" or name.startswith("pa_scratch")):
        sys.exit(f"test_server_scopes.py: refusing to run against database {name or '(none)'!r}.")
    return name


_require_scratch_database()

from mcp import ClientSession                                    # noqa: E402
from mcp.client.streamable_http import streamablehttp_client    # noqa: E402

from pa.db import Db                                             # noqa: E402

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


def section(t: str) -> None:
    print(f"\n{t}\n{'-' * len(t)}")


db = Db(url=os.environ["PA_DATABASE_URL"], actor="andre", use_pool=False)
_FLOOR = db.value("select coalesce(max(id), 0) from pa.shopping_item")
atexit.register(lambda: db.execute("delete from pa.shopping_item where id > %s", (_FLOOR,)))

FULL = secrets.token_urlsafe(32)
SHOP = secrets.token_urlsafe(32)
ALL_SCOPED = {"pa_shop_add", "pa_shop_list"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """server.py --http in a subprocess; stderr captured to a file."""

    def __init__(self, env_extra: dict[str, str]):
        self.port = free_port()
        self.log = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / f"scopes-{self.port}.log"
        # Set, never just removed: server.py runs load_dotenv(override=False),
        # which fills any ABSENT variable from the repo's .env. On the server
        # that .env holds the real tokens, so "unset" must be an explicit
        # empty value, or a test of the unset case would silently get the
        # real PA_SHOP_TOKEN.
        env = dict(os.environ)
        env.update({"PA_AUTH_TOKEN": "", "PA_SHOP_TOKEN": "",
                    "PA_AUTH_ALLOW_PATH": "0", "PA_SHOP_ALLOW_PATH": "0"})
        env.update(env_extra)
        # A context store in a temp dir: never the real one.
        self.store = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / f"scopes-ctx-{self.port}.json"
        self.fh = open(self.log, "w")
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER), "--http", "--host", "127.0.0.1", "--port", str(self.port),
             "--store", str(self.store)],
            env=env, stdout=self.fh, stderr=subprocess.STDOUT, cwd=str(REPO))

    def wait_ready(self, timeout: float = 20) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                return False
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.2)
        return False

    def url(self, path: str = "/mcp") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self) -> str:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.fh.close()
        text = self.log.read_text()
        self.log.unlink(missing_ok=True)
        self.store.unlink(missing_ok=True)
        return text


def http_status(url: str, headers: dict | None = None) -> int:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def session_calls(url: str, headers: dict, calls: list[tuple[str, dict]]) -> dict:
    """Open an MCP session, list tools, make calls; return names, results, session id."""
    out: dict = {"calls": []}
    async with streamablehttp_client(url, headers=headers) as (read, write, get_sid):
        async with ClientSession(read, write) as s:
            await s.initialize()
            out["session_id"] = get_sid()
            out["tools"] = {t.name for t in (await s.list_tools()).tools}
            for name, args in calls:
                r = await s.call_tool(name, args)
                out["calls"].append("".join(c.text for c in r.content if hasattr(c, "text")))
    return out


def run(coro):
    return asyncio.run(coro)


# ===========================================================================
section("Startup refuses a scoped token that would protect nothing")
for label, extra in (("equal to the full token", {"PA_SHOP_TOKEN": FULL}),
                     ("shorter than 32 characters", {"PA_SHOP_TOKEN": "short"})):
    srv = Server({"PA_AUTH_TOKEN": FULL, **extra})
    try:
        rc = srv.proc.wait(20)
    except subprocess.TimeoutExpired:
        rc = "still running after 20s"   # it started: the refusal did not fire
    log = srv.stop()                     # stops it either way: never leave a server behind
    check(f"PA_SHOP_TOKEN {label}: exits non-zero", isinstance(rc, int) and rc != 0, f"rc={rc}")
    check(f"PA_SHOP_TOKEN {label}: says why, without the token",
          "PA_SHOP_TOKEN is shorter than 32" in log and FULL not in log, log[-300:])

# ===========================================================================
section("With PA_SHOP_TOKEN unset, the shop token is just a wrong token")
srv = Server({"PA_AUTH_TOKEN": FULL})
check("server starts", srv.wait_ready())
check("shop token -> 404", http_status(srv.url(), {"Authorization": f"Bearer {SHOP}"}) == 404)
check("full token reaches /mcp (not 404)", http_status(srv.url(), {"Authorization": f"Bearer {FULL}"}) != 404)
log = srv.stop()
check("no 'Shop-scoped' line when unset", "Shop-scoped token route" not in log)

# ===========================================================================
section("Both tokens: each reaches exactly its own surface")
srv = Server({"PA_AUTH_TOKEN": FULL, "PA_SHOP_TOKEN": SHOP, "PA_SHOP_ALLOW_PATH": "1"})
ready = srv.wait_ready()
check("server starts with both tokens", ready)
try:
    full = run(session_calls(srv.url(), {"Authorization": f"Bearer {FULL}"},
                             [("pa_task_list", {"limit": 1})]))
    shop = run(session_calls(srv.url(), {"Authorization": f"Bearer {SHOP}"}, [
        ("pa_shop_add", {"name": "zqscope beans", "category": "grocery"}),
        ("pa_shop_list", {"category": "grocery"}),
        ("pa_task_list", {"limit": 1}),
        ("get_context", {}),
        ("pa_shop_remove", {"items": ["zqscope beans"]}),
    ]))
    check("full token lists every tool (34)", len(full["tools"]) == 34, str(len(full["tools"])))
    check("full token can call a task tool", "error" not in full["calls"][0][:200].lower()
          and "Unknown tool" not in full["calls"][0], full["calls"][0][:120])
    check("shop token lists exactly pa_shop_add and pa_shop_list", shop["tools"] == ALL_SCOPED,
          str(sorted(shop["tools"])))
    check("positive control: the full list does contain what the shop list hides",
          {"pa_task_list", "get_context", "pa_shop_remove"} <= full["tools"])
    check("shop token: pa_shop_add works", '"action": "added"' in shop["calls"][0], shop["calls"][0][:160])
    check("shop token: pa_shop_list sees it", "zqscope beans" in shop["calls"][1], shop["calls"][1][:160])
    for i, name in ((2, "pa_task_list"), (3, "get_context"), (4, "pa_shop_remove")):
        check(f"shop token: {name} is refused as an unknown tool",
              shop["calls"][i].strip() == f"Unknown tool: {name}", shop["calls"][i][:120])
    check("the refused remove changed nothing",
          db.value("select count(*) from pa.shopping_item where name = 'zqscope beans' "
                   "and removed_at is null") == 1)

    # A session id from one route is unknown to the other.
    sid = shop["session_id"]
    st = http_status(srv.url(), {"Authorization": f"Bearer {FULL}", "mcp-session-id": sid,
                                 "Accept": "text/event-stream"})
    check("a shop session id presented with the full token is not honoured", st in (400, 404), str(st))
    sid = full["session_id"]
    st = http_status(srv.url(), {"Authorization": f"Bearer {SHOP}", "mcp-session-id": sid,
                                 "Accept": "text/event-stream"})
    check("a full session id presented with the shop token is not honoured", st in (400, 404), str(st))

    # The path form: allowed for the shop token here, not for the full one.
    via_path = run(session_calls(srv.url(f"/t/{SHOP}/mcp"), {}, []))
    check("shop token by path prefix reaches the scoped surface", via_path["tools"] == ALL_SCOPED,
          str(sorted(via_path["tools"])))
    check("full token by path prefix is 404 (PA_AUTH_ALLOW_PATH unset)",
          http_status(srv.url(f"/t/{FULL}/mcp")) == 404)
    check("no token -> 404", http_status(srv.url()) == 404)
    check("/.well-known stays 404, with either token",
          http_status(srv.url("/.well-known/oauth-authorization-server")) == 404
          and http_status(srv.url("/.well-known/oauth-authorization-server"),
                          {"Authorization": f"Bearer {SHOP}"}) == 404)
finally:
    log = srv.stop()

section("Neither token reaches the log")
check("startup announces the scoped route, naming tools, not the token",
      "Shop-scoped token route is ENABLED (pa_shop_add, pa_shop_list), path-prefix allowed" in log)
check("the shop token appears nowhere in the server's output", SHOP not in log)
check("the full token appears nowhere in the server's output", FULL not in log)
check("the path request was logged, redacted", "/t/<redacted>/mcp" in log, log[-400:])
check("positive control: path requests really were access-logged",
      log.count("/t/<redacted>") >= 2, str(log.count("/t/<redacted>")))

print(f"\n{'=' * 60}")
print(f"{passed} passed, {len(failed)} failed")
for f in failed:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
