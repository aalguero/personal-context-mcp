# Deployment status — futile-pa

Last updated: 2026-10-20. **Live, connected, fully triaged, in version control. THE
BACKUP PROJECT IS COMPLETE: verified dumps, encryption the server cannot reverse, a
proven restore, an off-machine copy on the Pi, and a compliance-locked off-site copy
in Backblaze B2. The age private key is printed and filed with the estate documents.
What remains is small and listed at the end.**

Design record: `architecture/phase-1-task-backend.md`. Open work:
`architecture/backlog.md`. Backups: `architecture/backup-status.md`. Security:
`architecture/encryption-at-rest.md`. Voice component: `architecture/voice-assistant.md`.

## HOW WE WORK — the brief loop

**Andre's standing instruction, 2026-10-07: use `/home/andre/developer/tmp` on
shadow for both writing briefs and reading results.**

```
write  ──▶  /home/andre/developer/tmp/XXX_BRIEF.md     (on shadow)
                      │
            Andre hands it to the Claude CLI on the server
                      │
read   ◀──  /home/andre/developer/tmp/xxx-report.md
```

Do **not** deliver a brief into the chat and assume it exists on disk. Write it with
`device_commit_files`.

**The server cannot reach shadow**, so the executing Claude writes its report to
`/home/dremon/developer/tmp/` and Andre carries it across. Name that path as the
primary destination in every brief; naming the shadow path first just costs a
correction.

**Read the exchange folder before writing a brief.** Brief 13's first draft asserted
things two intervening briefs had already fixed, because this session had no record
of them. A context window is not the project's memory; the folder and these docs are.

**Run checks on the machine that has the thing.** Three times now, diagnostic
commands have been run on shadow for a condition on futileassistant. State the host
explicitly in briefs and in chat.

Naming: briefs `UPPERCASE_BRIEF.md`, reports `lowercase-report.md`. A `server-brief`
skill captures the procedure and the recurring machine facts.

**Why briefs at all:** this session has no route to the server. It reaches shadow
through the device bridge, but the MCP server, Postgres and futile-pa live on a
machine only Andre can reach. `pa_sql` is a read-only way to check the database from
here, which is how a restore was independently verified.

## Current state

| | |
|---|---|
| MCP server | `/home/dremon/tools/personal-context-mcp/server.py` (git: `github.com/aalguero/personal-context-mcp`) |
| futile-pa | `/home/dremon/tools/personal-context-mcp/futile-pa` — inside the repo. Old tree at `/home/dremon/futile-pa.pre-git` |
| Backup scripts | `futile-pa/scripts/backup.sh`, `futile-pa/scripts/prune_backups.py` — **note the `futile-pa/` prefix** |
| Interpreter | `/home/dremon/tools/personal-context-mcp/.venv-new/bin/python` — Python 3.13.5 |
| Database | `futile_assistant`, role `musicbrainz`, PostgreSQL 18.6, schema `pa` |
| Config | `.env` beside `server.py`, mode 600. Holds `PGPASSWORD`, `PA_AUTH_TOKEN`, the age recipient, and the B2 credentials. **No backup destinations** — those are top-of-script variables |
| Service | `~/.config/systemd/user/personal-context-mcp.service`, `WorkingDirectory=` set, `Restart=on-failure`, lingering enabled for `dremon` |
| Transport | streamable HTTP at `/mcp` only |
| Public URL | `https://futileassistant.cormorant-barb.ts.net/mcp` via Tailscale Funnel (`/ → 127.0.0.1:8765`) |
| Auth | shared secret, ASGI middleware, all paths, 404 on failure, fail-closed at startup |
| Tools | 29 = 4 context tools + 25 `pa_*` |
| Migrations | 001, 002, 003 applied. **004 specified, not written** — backlog H |
| Backups | **Working again as of 2026-10-28, `a07569e`/`09d0f97`.** Local + `vocalassistant` + B2 (compliance-locked 30 days). Restore proven 2026-10-19. **Zero backups shipped 10-21 to 10-28** — a smoke-test check was silently coupled to production rankings; fixed, and the watchdog now keys on `last_success` so this can't go unnoticed again. See `backup-status.md`'s 10-21/10-28 incident |
| Test suite | 63 checks, 0.28 s. **Guarded:** refuses any database but `pa_verify` / `pa_scratch*` |
| Disk encryption | **Server: NONE** (task 112). **shadow: YES** — and shadow is where the age private key lives, which is what makes the asymmetry hold. See `encryption-at-rest.md` |
| Journal access | `dremon` is in `systemd-journal` on both the server and the Pi as of 2026-10-20 (task 122) |
| Tailnet | futileassistant `100.108.86.16` / LAN `192.168.86.35`; shadow (DNS name `darkslip`) `100.102.50.58` / LAN `192.168.86.40`; vocalassistant `100.75.132.20` / LAN `192.168.86.251`; homeassistant `100.96.213.73`; pixelatedandroid `100.122.109.81` |

**Network shape after the room move (2026-10-20):** the server and the Pi are on the
same `192.168.86.0/24` subnet, so the nightly transfer takes a direct LAN path rather
than relaying through DERP. The move is also what caused the Pi poweroffs that broke
the 2026-10-17 run; it is not a recurring condition.

**Home Assistant is reachable securely from the phone** at
`https://homeassistant.cormorant-barb.ts.net` via Tailscale Serve, tailnet-only (not
Funnel). Details in `voice-assistant.md`.

**Load-bearing detail:** `/.well-known/*` must keep returning 404. Anything
OAuth-shaped there makes claude.ai attempt dynamic client registration, which fails
and already cost a day. Re-verified 2026-10-20.

## Known-benign log noise — recorded so nobody investigates it twice

**`tailscaled: Drop: TCP{...} > [...]:51865 ... no rules matched` on futileassistant.
Baseline: about 13 a day. This is normal.**

Investigated 2026-10-20 and closed with no action. The sources carry `tag:ingress`
and the capability `https://tailscale.com/cap/ingress` — they are **Tailscale's
Funnel ingress relays**, not nodes in this tailnet (`tailscale whois` says
`peer not found` for both). The destination is `tailscaled`'s own live Funnel
listener. So this is deny-by-default refusing connections from relays that are not
carrying the active session, while the relay that *is* carrying it gets through.

Evidence that Funnel is healthy: `curl` against `/mcp` and
`/.well-known/oauth-authorization-server` both return 404 (correct by design),
`tailscale funnel status` shows `/ → http://127.0.0.1:8765`, and every `pa_sql` call
from the chat session travels that exact path.

**The number is the point.** 13/day is the baseline. A jump to hundreds would mean
something; without the baseline written down, the next person with journal access
spends an hour rediscovering this.

Also harmless: `tailscaled` advertises the Docker bridge (`172.17.0.1:41641`) as a
local endpoint.

## Data

~120 tasks. Eight recurrences, 32 projects, 18 contexts, 9 situations, 9 people,
9 goals.

Structure, per Andre's framing: **Brook House** is the area covering house and land
(renamed from "House & Land"; slug stays `house-land`). Beneath it: Farmhouse
Projects, Native Plant Garden, Land Maintenance, Vehicles. Added during triage:
Backup Power, Estate Documents and Authorizations, Seven Fires Cooking Setup,
Basement and Moisture.

Full triage record in `architecture/backlog.md` section G.

## Migration 003 — found only by loading real data

1. **`land_day` was providing `@home`** ("indoors"), so a day on the five acres
   suggested "Snuff boxes" above "Wood pile".
2. **A generality penalty in `pa.suggest_tasks`.** `@phone` tasks were satisfiable by
   six of nine situations and crowded every list.
   `fit_bonus = (total situations − situations that can satisfy this task) × 4`,
   capped at 32. Reasoning is opportunity, not preference: a task one situation can
   satisfy has one chance to get done. A bonus rather than a sort key, so an overdue
   hard deadline still wins.

## The next real gap — situation vocabulary is too coarse

`dog_walk`, `evening_low`, `waiting_room` and `morning_coffee` all reduce to
`phone` + `lowenergy`/`online`, so the engine cannot tell a dog walk from a waiting
room.

| | dog walk | waiting room |
|---|---|---|
| Can talk at length, out loud | yes | **no** |
| Overheard by strangers | no | **yes** |
| Hands free | yes | no |
| Can read or tap a screen | no | yes |

A private call about his father's finances is ideal on a dog walk and wrong in a
waiting room. Likely fix: split `@phone` into `@call`, `@private`, `@handsfree`,
`@screen` and retag.

**Risk if left:** Andre tunes out the situation feature for exactly the cases where
it was meant to be most useful.

## Corrections that came back from the machine

Thirteen now, and every one came from data rather than from more reasoning.

1. Broken venv was a typo'd path baked into pip's console scripts
   (`personal-cont**a**ct-mcp`), not a Python upgrade.
2. Funnel was running correctly with `--bg`, not dead.
3. The `/messages/` double-send mechanism was real; the claimed connection corruption
   did not reproduce.
4. The connector failure was a transport mismatch, not OAuth.
5. A path-rewrite fix Andre correctly called hacky; replaced with a structural one.
6. `env -u PA_DATABASE_URL` does not unset it — `load_dotenv()` defaults to
   `override=False` and reinstates it.
7. The import brief gave the venv as `/home/dremon/futile-pa/.venv-new/`. It is at
   `/home/dremon/tools/personal-context-mcp/.venv-new/`.
8. **`load_dotenv()` ran *after* `FUTILE_PA_PATH` was read** — see below.
9. **The darkslip failure was a tailnet SSH policy refusal for the login user
   (`dremon`), not a dead sshd.** And a note claimed a change was "covered in
   `BACKUP_FIX_BRIEF.md` section 5" when it was never in that brief. A note that
   points at a document is a claim; read the document.
10. **System packages would not shadow the hand-installed binaries.** `~/.local/bin`
    is first in PATH. (For `rsync` specifically, apt's copy was *already installed*
    and merely shadowed. The hand-installed `age`/`rclone`/`rsync` were retired to
    `~/.local/bin/retired/` on 10-10; the live ones are Debian's in `/usr/bin`, and
    the script's PATH prepend is now stale.)
11. **Tailscale SSH and sshd do not necessarily present different host keys.** On the
    Pi both present the same ed25519 key — the banners prove two daemons, one key —
    because tailscaled appears to reuse `/etc/ssh/ssh_host_*`. The consequence
    inverts: a LAN-IP connection fails on *authentication*, not on the host key.
12. **A run reporting `result: ok` had been pruning only one remote file per run since
    the code was written.** `ssh` inside a `while read` loop inherits the loop's stdin
    and eats the rest of the here-string; `ssh -n` fixes it.
13. **The dropped tailnet packets were explained as "stale connections settling after
    the room move" — reasoned from timing, while the `whois` that disproved it was
    already running.** They were Funnel ingress relays hitting a live listener. The
    explanation was wrong in substance and wrong in method: an answer was seconds
    away and got pre-empted by a plausible story. (Outcome was still benign — see
    "Known-benign log noise".)

Also: brief 13 carried two path errors of its own — `scripts/backup.sh` (it is
`futile-pa/scripts/backup.sh`) and the shadow report path.

### Correction 8 in full, because the shape of it matters

`server.py` read the variable three lines before loading `.env`:

```python
FUTILE_PA_PATH = os.environ.get("FUTILE_PA_PATH", "/home/dremon/futile-pa")
sys.path.insert(0, FUTILE_PA_PATH)
from pa.tools import ...            # line 40
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")   # line 43
```

So **the `.env` value was never read, on any start, ever.** The housekeeping change
that "replaced the hardcoded path with an environment variable" didn't — it moved the
hardcoded path into a default that was unconditionally taken. It looked correct
because the default *was* the real path.

The next brief then moved futile-pa into the repo and pointed `.env` at the new
location. That change was **also inert**, for the same reason, and appeared verified
only because the old directory still existed at the moment of the restart. It was
renamed to `.pre-git` afterwards — arming a landmine the next restart would trigger.
Which happened a week later during an unrelated password rotation, presenting as
`ModuleNotFoundError: No module named 'pa'`.

**Fixed:** `load_dotenv()` moved above the read, the default changed to the in-repo
sibling, and a missing directory now exits with a message naming the cause.

### The pattern, now seen six times

| | The change | Why it looked fine | What actually proved it |
|---|---|---|---|
| 1 | `FUTILE_PA_PATH` in `.env` | the hardcoded default happened to be correct | pointing it at nonsense |
| 2 | the dedicated SSH backup key | rsync failures read as key problems | `ssh -vv` showing key exchange *succeed* |
| 3 | remote retention | the run reported `result: ok` | listing the remote directory |
| 4 | the unattended timer run | two manual runs succeeded | reading the journal for 03:25 |
| 5 | the B2 upload | the status said `skipped (not configured)` | giving it credentials |
| 6 | the off-site alarm (open) | `result: ok` while B2 could be dead | **task 116** |

***A configuration or code change is not verified by the system still working. It is
verified by proving it controls something.*** This belongs in every future brief, and
it is why briefs ask for listings, hashes and verbatim output rather than exit codes.

Corollary learned 2026-10-16: **a protective feature being *enabled* is not the same
as the protection *applying*.** B2 Object Lock was on with no default retention, so
every object would have arrived unlocked while the status file reported success.
Check the object, not the setting.

Corollary learned 2026-10-19: **a monitoring decision has a shelf life.** Declining to
alarm on `off_site_copies == 0` was right when B2 was unconfigured and zero was
normal. B2 works now, so zero is an anomaly. The right alarm depends on what normal
is, and normal changes.

### A fourth kind: the prerequisite invisible until it fires

Host keys. The first `BatchMode=yes` probe to the Pi failed with
`Host key verification failed.` — nothing to do with the destination change, and it
would have broken the 03:15 run with no symptom but `off_machine_copies: 0`.
**Host-key provisioning is a named prerequisite for any new backup destination**, and
the right way to establish a key is from the control plane
(`tailscale status --json`, `Peer[].sshHostKeys`) rather than by accepting whatever
answered. darkslip's own key, accepted blind by a TOFU prompt on 10-07, was audited
retroactively and does match.

### Diagnostics that lie

A journal read without permission returns an **empty result**, not a refusal. On
10-17 that made a hypothesis look disproven when it was merely unreadable. Fixed by
task 122; an empty result that reads like evidence is the worst failure mode a
diagnostic tool has.

**Follow-up owed:** the same housekeeping pass changed `PA_DATABASE_URL` from a bare
subscript to `.get()` with a warning. Confirm that read also happens after
`load_dotenv`, rather than assuming.

**Method that works here: test before theorising.** Two curls settled the transport
question after a day of speculation. Loading 78 real tasks caught two ranking bugs. A
3-line synthetic loop settled the `ssh -n` question in one command. Retitling a task
and re-running proved the metadata markers were load-bearing.

A corollary from triage: **imported titles record what, never why, and why determines
method.** "Fill cracks" was rewritten three times — moisture sealing, structural
repointing, rodent exclusion — because the purpose lived only in Andre's head.

## Identity belongs in a stable key, never in a label

Three instances, same lesson:

- A test asserting the display name `"House & Land / Native Plant Garden"` broke when
  Andre renamed the area. Fixed to resolve by **slug**.
- Two more hardcoded display names in the suite, fixed the same way.
- The backup alert dedupe and auto-close matched tasks by **exact title**, including
  an em dash. Moved to `pa.task.metadata->>'backup_alert'`. Proven by retitling a
  task and confirming no duplicate appeared.

## Incident, 2026-10-10 — the test suite wrote into production

Recorded in full in `backup-status.md`. An agent aimed `test_smoke.py` (an
integration test that writes) at the live database; ~26 fixture tasks landed and a
review date was reset. Recovered from a two-minute-old dump; Andre independently
confirmed 110 rows, max id 111. **Structural fix in place** (`5568ff1`): the suite
refuses any database not on a scratch allowlist, no override. The lesson is not "be
more careful"; a suite that writes to whatever it is pointed at will eventually be
pointed at production.

## Open items

**Backups — small, and the project is otherwise done:**

- **Task 116:** the off-site alarm gap (brief written:
  `BACKUP_ALARM_BRIEF.md`), plus weekly `sha256sum -c` of the Pi's copies, and
  removing the stale PATH prepend.
- **Task 123:** narrow the B2 key's capabilities via the b2 CLI (`b2 key create
  --bucket <name> <keyname> listBuckets,listFiles,readFiles,writeFiles`). Deliberately
  **after** 116 so only one variable moves.
- **Push the local commits.** `origin/main` was `7baec05`; local HEAD is `3530fc5`.

**futile-pa proper — the actual next work:**

- **Migration 004:** task gates (`pa.task_gate` with kinds `budget` /
  `person_present` / `pet_absent`), `estimated_cost`, `pa.v_budget_queue`, a
  `pa_budget_queue` tool, and a proper `source` value for "an unattended script
  noticed something" (`pa.task.source` currently allows only `manual, claude,
  google_tasks, gmail, calendar, recurrence, import`).
- **Split `@phone`** into `@call` / `@private` / `@handsfree` / `@screen` and retag.
- ~~Wire the backup status file into `pa_daily_brief`.~~ **Addressed differently, 2026-10-28**: a standalone watchdog (`futile-pa/assistant/watchdog.py`, part of the new assistant backend — `docs/architecture/standalone-assistant.md`) checks `last_success` on its own schedule and raises a task, rather than waiting for a human to read the daily brief. Same need, a more independent mechanism.
- **Nine tool-surface gaps** in backlog section J — chiefly that `pa_task_update`
  replaces `notes` while `pa_task_complete` appends, and that `pa_task_update` has no
  `depends_on`, `parent_task_id` or `kind`.

**Household / personal, still open:**

- **Decide what to do about no disk encryption on the server.** Cheap regardless:
  remove the leftover 2026-09-23 plaintext dump, keep `.env` at 600.
- Drop `futile_assistant_broken_20261010` (still present; verify the name; his call).
- Delete the dead `/home/dremon/.ssh/futile_pa_backup*` key pair.
- **Task 117:** delete `/home/dremon/backups/pre-prune-hold-20261016/` — now unblocked,
  since the practice restore succeeded.
- **Task 109:** darkslip cleanup — `probe-delete-me`, optional openssh-server removal.
  On the **Pi**, leave openssh-server installed: it is headless and LAN sshd is the
  only way back if Tailscale misbehaves there.
- **Task 108:** `sudo apt install age rclone` (rsync's apt copy is already installed),
  then remove `~/.local/bin/retired/`.
- **Mark task 106 done.** Password rotation complete and verified; still shows
  overdue. Also check `~/.pgpass` and the MusicBrainz tooling for the old password.
- **Gary** — confirm the "Senior neighbor" record is him before renaming.
- **Rosco's medication schedule** — still to be rebuilt as recurrences.
- Optional Pi hardening if an overnight failure ever recurs: Wi-Fi power save is on
  (`sudo nmcli con modify westwind 802-11-wireless.powersave 2`), and `eth0` is down.
