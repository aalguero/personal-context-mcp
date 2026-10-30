# Backup and version control — build status

Last updated: 2026-10-16 (after brief 13). Design and reasoning:
`architecture/encryption-at-rest.md`. Open work: `architecture/backlog.md`.

**Summary: the backup system is done, except for off-site. Brief 13 moved the
off-machine leg from darkslip (Andre's laptop, shut down at night, so the 03:15 copy
essentially never landed) to `vocalassistant` (the always-on Pi 5 that runs the voice
assistant). Two runs on 2026-10-16 finished `result: ok`, `verified: true`,
`smoke_test: "63 passed, 0 failed"`, `encrypted_bytes: 471352`, `offsite_copies: 1`,
`destinations.vocalassistant: "ok"`. All 8 retained archives exist on both machines
with identical sha256. Tasks 105 and 115 are closed. Two bugs were found and fixed on
the way: the Pi's host key was missing from `known_hosts` (which would have broken the
unattended run silently), and remote pruning had been deleting only one file per run
since it was written. What is still missing: nobody has decrypted a real backup
(task 113), and there is still no off-SITE copy — the Pi is ten feet from the server
(task 103).**

---

## Done and verified

| | |
|---|---|
| futile-pa in version control | **Yes.** Moved into `personal-context-mcp` as a subdirectory. `fde42ad` (move), `d1c3f4d` (scripts), pushed. |
| Old tree | Preserved at `/home/dremon/futile-pa.pre-git`. Andre removes it when satisfied. |
| Scripts | `futile-pa/scripts/backup.sh`, `futile-pa/scripts/prune_backups.py`. **Note the `futile-pa/` prefix** — earlier briefs said `scripts/backup.sh` and were wrong. |
| Verification design | Restores into a throwaway `pa_verify`, runs the 63-check suite, *then* encrypts. |
| Retention | GFS 7/4/12. Never prunes unless the run is `ok`; never empties a destination; a backstop never deletes the last file. |
| Lingering and timer | `/var/lib/systemd/linger/dremon` exists; `OnCalendar=*-*-* 03:15`, `RandomizedDelaySec=900`, `Persistent=true`, enabled. |
| age keypair | Generated and verified 2026-10-10 (task 102). Private key never on the server. **Printed copy for the estate documents still owed (task 111).** |
| Test-suite guard | `5568ff1`. Refuses to run unless the database is `pa_verify` exactly or starts with `pa_scratch`. Allowlist, no override. |
| Slug-based assertions | `9b33198`. Three hardcoded display names replaced with slug lookups, so a rename cannot break the suite. |
| Three result states | `ae7d8d3`. See the table below. |
| **Off-machine destination** | `abfa582`. `dremon@vocalassistant:/home/dremon/backups/futile-pa/`. darkslip removed (one explanatory comment remains). No `-i` key flag anywhere. |
| **Remote prune fix** | `e54ce08`. See "The prune bug" below. |
| **Host keys pinned** | Pi's ed25519 key verified against the tailnet record and pinned under `vocalassistant`, the MagicDNS FQDN, and `100.75.132.20`. Backup of the old file: `~/.ssh/known_hosts.bak-20261016`. |
| Postgres password | Rotated and verified 2026-10-07 (task 106). |
| LUKS check | Done (task 112). **No LUKS.** See below. |
| Tasks 105 / 115 | **Closed 2026-10-16**, with the reasoning recorded in their notes. |

## Result states (implemented in `ae7d8d3`)

| `result` | Meaning |
|---|---|
| `ok` | verified, encrypted, local copy written, **and at least one off-machine copy landed** |
| `degraded` | verified, encrypted, local copy written, **zero off-machine copies** |
| `failed` | verification or encryption did not happen; nothing trustworthy exists |

`offsite_copies` counts off-machine destinations reporting `ok`; an otherwise-`ok` run
with a count of 0 downgrades to `degraded`. Pruning runs only on `ok`. `ok` and
`degraded` exit 0; `failed` exits 1. Destination failures go through `dest_error()`,
which logs without forcing `failed`, so one dead destination no longer skips the other.

**The field name is wrong and should be fixed (task 116).** `offsite_copies` counts
off-MACHINE copies. The Pi is on the same LAN in the same house. The status file
currently reports `offsite_copies: 1` when the true off-site count is zero.

## The destination: vocalassistant

| | |
|---|---|
| Host | `vocalassistant` (tailnet `100.75.132.20`, LAN `192.168.86.251`) |
| Login user | `dremon` |
| Path | `/home/dremon/backups/futile-pa/` |
| Storage | microSD, 119 GB, 102 GB free. About 4 MB retained, ~0.5 MB/day |
| Transport | Tailscale SSH, `-o BatchMode=yes -o ConnectTimeout=15`, no key file |

**Why the voice assistant is a safe place for backups:** the Pi never holds the age
private key, so compromising it yields inert files. That is the property the
asymmetric design was chosen for. Andre enabled Tailscale SSH there on 2026-10-14
with `sudo tailscale set --ssh`; his own session dropped, as warned, and reconnected.

**Why not darkslip:** it is a laptop and he shuts it down at night. The timer fires at
03:15. The old copies on darkslip were left in place — they are valid encrypted files
and cost nothing. Removing openssh-server there and clearing `probe-delete-me` is
task 109.

**microSD risk, assessed and accepted for now:** write volume is trivial and will not
wear the card. The real exposure is that SD cards corrupt silently, and nothing
re-checks the Pi's copies after arrival — `backup.sh` checks size once and moves on.
The `.sha256` files are already shipped alongside each archive, so a periodic
`sha256sum -c` on the Pi would catch bit rot cheaply (task 116). A card failure also
takes out the voice assistant, so it is one cause with two consequences.

## Host keys are a prerequisite for every new destination

**The failure mode brief 13 exposed, and the most transferable lesson in this file.**
The first `BatchMode=yes` probe to the Pi returned `Host key verification failed.`
(exit 255) — not a refusal, not a prompt, not a login URL. `BatchMode=yes`
deliberately disables the interactive *"are you sure you want to continue
connecting?"* prompt, so the **first** connection to any host fails by design.

This had nothing to do with the destination change. The 03:15 run would have failed
identically, and the only symptom would have been `offsite_copies: 0` every morning
with no obvious cause. **`BatchMode=yes` in the brief is the only reason it surfaced
before the timer found it.**

**How the key was established, which is better than the normal route.** The agent read
the Pi's published host keys from `tailscale status --json` (`Peer[].sshHostKeys`) —
the control plane, over an authenticated channel — and compared them to what the Pi
presented via `ssh -v` and `ssh-keyscan` on all three names. Byte-identical. Typing
"yes" at a TOFU prompt trusts whatever answered; checking the tailnet record does not.

**Pinned under all three names** (`vocalassistant`, the MagicDNS FQDN, and the tailnet
IP) because `known_hosts` matches on the literal host string given on the command
line, so a later switch from short name to IP would reintroduce the same failure.

**Correction to an assumption recorded here earlier:** *Tailscale SSH and sshd do not
necessarily present different host keys.* On this Pi they present the **same**
ed25519 key — `tailscale status` and `ssh-keyscan` agree, while the banners
(`remote software version Tailscale` on the tailnet IP vs `OpenSSH_10.0p2` on the LAN
IP) prove they are two different daemons. The likely reason is that tailscaled reuses
`/etc/ssh/ssh_host_*` when it exists (inferred; the Pi's tailscaled config was not
read). Consequence: connecting by LAN IP would not hit a key mismatch, but would reach
a daemon that authenticates by key or password rather than tailnet policy, so it would
still fail under BatchMode.

**darkslip's entry was audited retroactively and matches.** Its key was accepted blind
by an interactive TOFU prompt on 2026-10-07 (the terminal said *"Permanently added
'darkslip' (ED25519)"*) and had never been checked against anything. It is now
confirmed identical to the tailnet record. Note the tailnet lists that device under OS
hostname `shadow` with DNS name `darkslip.cormorant-barb.ts.net`.

**Which `known_hosts` the timer actually reads** was proven, not assumed: the unit is
a systemd *user* unit with no `User=` and no `Environment=`,
`systemctl --user show-environment` gives `HOME=/home/dremon`, the only
`UserKnownHostsFile` override on the box is scoped to `Host unix/* vsock/* machine/*`,
and — the real proof — both runs reached the Pi under BatchMode, which they could only
do by reading that file.

## The prune bug (`e54ce08`) — latent since the retention code was written

```bash
while read f; do ssh … rm …; done <<< "$to_delete"      # wrong
while read f; do ssh -n … rm …; done <<< "$to_delete"   # fixed
```

Without `-n`, the first `ssh` inherits the loop's stdin and consumes the rest of the
here-string, so **only one file was ever deleted per run.** Reproduced harmlessly: a
3-line loop ran once with plain `ssh` and three times with `ssh -n`.

It had been in the darkslip code from the start and never showed, because darkslip
never received a prune with more than one file to delete. It surfaced only when the Pi
had five expired archives at once — and it surfaced **after** a run that reported
`result: ok`.

**That is this project's recurring lesson in its purest form yet:** a successful run
told us nothing about whether retention actually controlled the remote. Only listing
the remote directory did. Compare the `FUTILE_PA_PATH` bug (a config value read before
`load_dotenv()`, inert for weeks because the hardcoded default happened to be right)
and the dedicated SSH key that was inert from the day it was generated because
Tailscale SSH never reads `authorized_keys`. Three instances, same shape:
**a configuration or code change is verified by proving it controls something, never
by the system continuing to work.**

## Why retention had five files due at once

Every run from 2026-10-10 to 2026-10-16 was `degraded` (darkslip asleep), and degraded
runs skip pruning entirely. So the first `ok` run had a week of deferred retention to
execute, contradicting the brief's instruction to confirm nothing was pruned. The
agent stopped, asked, and held copies in
`/home/dremon/backups/pre-prune-hold-20261016/` (5 archives + checksums, all verified,
outside the prune glob). Task 117 deletes it — **after** the practice restore, not
before, since until then no archive is proven recoverable.

Worth fixing: letting `degraded` runs prune local but never remote would avoid
unbounded local growth and this kind of surprise (task 116).

## What "verified" does and does not mean

The server holds only the public key, so it cannot decrypt its own output. `verified`
means the dump restored into a throwaway database, the 63-check suite passed on it,
and the archive was then encrypted and checksummed. It does **not** prove the
encrypted file can be decrypted and restored. Only the private key shows that, which
is why task 113 exists and why it is the single most valuable open item.

## Tailscale SSH — the traps, kept because they cost a session each

1. **SSH `dst` cannot be a device name.** Only a tag, `autogroup:self`, or a single
   named user.
2. **A matching `check` rule always beats a matching `accept` rule.** Evaluation is
   most-restrictive-first; array order is irrelevant.
3. **`check` cannot cover an unattended path** — browser sign-in every 12 hours. And
   the trap inside the trap: signing in satisfies it for 12 hours, so a probe right
   afterwards passes while 03:15 still fails.
4. **The login user is authorization, not decoration.** `dremon@darkslip` was refused
   with *"tailnet policy does not permit you to SSH as user dremon"* — almost
   certainly because no `dremon` account exists on that laptop. Two briefs mistook
   this for a key problem and generated keys for it.

The working policy, unchanged and sufficient for the Pi:

```json
"ssh": [
  { "action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self"], "users": ["autogroup:nonroot"] },
  { "action": "check",  "src": ["autogroup:member"], "dst": ["autogroup:self"], "users": ["root"] }
]
```

## Incident, 2026-10-10: the test suite writes to whatever it is pointed at

An executing agent built a connection string by hand and pointed `test_smoke.py` at
the live `futile_assistant` database. The suite is an integration test that writes. It
inserted about 26 fixture tasks and reset `native-garden`'s `last_reviewed_on`.

**Recovery:** the agent caught it by diffing timestamps, confirmed with Andre, stopped
the service, renamed the live database aside, restored the two-minute-old dump,
verified, restarted. Andre's independent check: 110 tasks, max id 111. Nothing lost —
but only because the dump happened to be fresh.

**Structural fix (`5568ff1`):** the allowlist guard. The lesson is not "be more
careful"; a suite that writes to whatever it is pointed at will eventually be pointed
at production.

**`futile_assistant_broken_20261010` is still on the server.** Reported as dropped; it
is not. What Andre deleted was probably the plaintext dump file. Dropping it is his
call, reasonably after the practice restore succeeds.

## Incident, 2026-10-21 to 2026-10-28: eight nights of zero backups

Every nightly `backup.sh` run from 10-21 through 10-28 failed at the
verification gate on one `tests/test_smoke.py` check — "hard deadline outranks
a soft one at similar distance" — which asserted a fixture task ranked in the
top 6 by score **across the whole database**, fixtures and real data together.
Andre's real backlog grew past the fixture on the evening of 10-20; the check
started failing the very next night and stayed broken, because overdue tasks
only gain urgency with age. The gate correctly failed closed — nothing was
encrypted, kept or shipped for eight consecutive nights — but task 156, the
alert this exact scenario is designed to raise, sat open and uninvestigated
the entire time (see the working agreement's own history with task 156).
No data was lost: the infrastructure (Postgres, age, the Pi, B2, disk, timer)
was healthy throughout, and the 10-20 15:21 backup remained the newest copy
everywhere until the fix landed.

**Found by:** Andre observing no recent activity in the Backblaze B2 console
directly — not by any alert, not by the dashboard. The automated system caught
its own failure correctly (task 156) and then the catch itself went unseen,
which is this project's recurring failure shape one level deeper than usual:
the alarm fired and sat unread through the very reorganisation built to stop
that from happening.

**Fixed, `a07569e`/`09d0f97`, verified 2026-10-28 22:02–22:05:** the check now
compares a same-distance soft-deadline twin fixture directly against the
hard-deadline one, rather than asserting a position in a ranking real data was
always going to eventually invade — and queries the two fixtures by id, not a
bounded top-N, after a deploy-time review caught that the first version of the
fix (`limit=200`) was the same bug shape with a higher, still-finite ceiling.
A real run that night succeeded end to end: `63 passed, 0 failed`, identical
sha256 in all three destinations, task 156 closed itself. The backup
watchdog (`assistant/watchdog.py`) was also found keying on `last_attempt`,
which updates on a failed run too — it reported "fresh" through all eight
nights. Now keys on `last_success`.

**Decided 2026-10-29 (Andre):** stay fail-closed on a verification failure —
do **not** keep a local copy marked `verified: false` as a consolation. A day
or two of drift is an acceptable worst case; a system that sometimes ships
unverified copies is a harder property to reason about than "verified or
nothing," and the watchdog now means a dead run can't go unnoticed for eight
days again regardless.

## The disk is not encrypted at rest (confirmed 2026-10-10, task 112)

No `crypto_LUKS`, no `/etc/crypttab`, `/home` is plain ext4 on LVM on `nvme0n1p3`, and
Andre's `cryptsetup isLuks` check confirmed it.

Consequence: a pulled or stolen disk exposes the live database, `~/.personal-context`
(medical, financial, estate detail), `.env`, and any plaintext dumps. Only the
age-encrypted backups are protected. What to do about it is Andre's decision and not
started; see `encryption-at-rest.md`.

## Still unproven

- **Restorability.** Nobody has decrypted a real backup (task 113). Until then every
  archive is only *believed* recoverable. Do it once now on Andre's own machine, then
  annually (recurrence 8).
- **No off-site copy.** The Pi is off-machine, not off-site: same LAN, same house. One
  fire, burglary, flood or surge takes both. B2 (task 103) is the half that survives
  the house, and **object lock has been upgraded from "consider" to "do"** — because
  the server prunes its own remote copies, an immutable bucket is the only copy that
  survives a compromised server.
- **The unattended run.** The 2026-10-16 proof came from two manual
  `systemctl --user start` runs. The 03:15 timer run uses the same unit and the same
  environment, so it should behave identically — but that is an inference, and this
  project has been punished for inferences of exactly that shape. Tomorrow's status
  file settles it.

## Blocked / waiting on Andre

1. **Task 113: the first practice restore.** The one that converts "believed
   recoverable" into "recoverable". Everything else is bookkeeping next to this.
2. Task 103: B2 bucket, scoped key, object lock.
3. Task 111: printed copy of the age private key for the estate documents.
4. Task 116: the next small backup brief (self-resolving warning tasks, the
   `offsite_copies` rename, Pi-side integrity re-checks, degraded-prunes-local).
5. Task 117: delete the pre-prune hold directory — after task 113.
6. Task 109: darkslip cleanup, including `probe-delete-me`.
7. Task 108: `sudo apt install age rclone rsync`, then delete the hand-installed
   copies in `~/.local/bin`.
8. His call: drop `futile_assistant_broken_20261010`, delete the 2026-09-23 plaintext
   dump, delete the dead `/home/dremon/.ssh/futile_pa_backup*` key pair.

## Deviations worth knowing about

- **`age`, `rclone`, `rsync` are hand-installed** in `/home/dremon/.local/bin` and get
  no security updates. `~/.local/bin` comes first in PATH, so system packages do
  **not** shadow them; after `apt install` the hand-installed copies must be deleted.
  **rsync is a special case:** apt's rsync 3.5.0 is *already installed* at
  `/usr/bin/rsync` and merely shadowed by a hand-installed 3.4.1, so for rsync task 108
  is "stop shadowing it", not "install it".
- **Retention is Python, not bash.** The awk draft was tested against 400 synthetic
  filenames and found wrong.
- **Remote filenames are fed back into a remote `rm`.** Safe because the helper only
  emits names matching `^futile-pa-\d{8}-\d{6}\.tar\.age$`.
- **B2 credentials will reach rclone only via `RCLONE_CONFIG_B2_*` env vars** — no
  `rclone.conf` on disk.
- **`source="backup.sh"` does not work.** `pa.task.source` allows only `manual, claude,
  google_tasks, gmail, calendar, recurrence, import`. `"manual"` substituted; a proper
  value for "an unattended script noticed something" goes into migration 004.
- **The `error` field stays `null` on a `degraded` result, by design** — it is reserved
  for "nothing trustworthy exists". `destinations` carries which leg failed.
- **psycopg_pool shutdown noise is gone.** `atexit.register(db.close)` removed ~20 s of
  forced per-thread waits; the 63-check suite now runs in 0.28 s.
