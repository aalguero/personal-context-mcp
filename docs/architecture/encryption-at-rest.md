# Encryption at rest — design note

Added to the backlog 2026-09-21 at Andre's request. **Item 1 (encrypted off-machine
backups) was designed 2026-10-07 and is now PROVEN END TO END, including recovery —
see "Decided" and "The restore that proved it". Item 2 was answered 2026-10-10: the
server's disk is NOT encrypted. Item 2b was answered 2026-10-19: shadow, the machine
holding the private key, IS encrypted. Items 3–5 remain open, and what to do about
the server's disk is Andre's decision.**

This note exists because "encrypt my personal info on disk" has a trap in it, and
the trap determines what is worth building.

## What is actually at risk

Two stores, both on the server:

- **`/home/dremon/.personal-context/context.json`** — medical conditions and
  medications, INR management, family names and locations, employment history and
  severance terms, complete investment positions, tax planning, income figures.
  Plus three rolling backups since the atomic-write fix.
- **PostgreSQL `futile_assistant`, schema `pa`** — as of October 2026 roughly a
  hundred and twenty fully triaged tasks, several hundred lines of hand-written
  reasoning in task notes, nine people, 32 projects and eight recurrences. Bills and
  finances are planned.

Also: the WAL, any `pg_dump` output, and the `.env` file holding the database
password, the auth token, and now the Backblaze B2 application key.

## The trap

**The server must decrypt this data to answer a tool call, and it starts unattended
under systemd.** So whatever key protects it has to be available to the machine at
boot without a human. Which means the key is on the machine.

This does not make encryption pointless — it makes it precise about what it defeats:

| Threat | Encryption at rest helps? |
|---|---|
| Disk pulled from a decommissioned or stolen machine | **Yes** |
| Backup copied to another host, a USB drive, or cloud storage | **Yes** |
| VM snapshot or hosting-provider disk image | **Yes** |
| Someone who compromises the running server | **No** — the key is loaded |
| Someone who gets the auth token and calls the MCP tools | **No** — that is just a legitimate-looking client |

Anyone who promises more than this without a passphrase at service start is selling a
feeling. The honest design is to pick the threats worth defeating, and to say plainly
which ones remain.

## Options, in order of value for effort

**1. Encrypted backups — DECIDED, BUILT, AND PROVEN. See below.**

**2. Confirm what full-disk encryption already exists. ANSWERED 2026-10-10: NONE on
the server.**

Evidence: `lsblk` shows no `crypto_LUKS` filesystem, there is no `/etc/crypttab`, and
`/home` is a plain ext4 LVM volume sitting directly on `nvme0n1p3`. The server agent
had no sudo, so Andre ran the definitive check himself
(`sudo cryptsetup isLuks /dev/nvme0n1p3`, task 112) and it showed no LUKS.

**So the stolen-disk case is uncovered on the server.** A pulled disk exposes the live
database, `~/.personal-context`, `.env` and any plaintext dumps. Only the age backups
are protected — and as of 2026-10-19 there are verified encrypted copies on
`vocalassistant` and in B2, so losing the server's disk no longer loses the history.
That was the first priority and it is now done.

If this is to be fixed at the disk level, LUKS on the data volume is the standard
answer, but **converting an installed, in-use volume is risky** and must not be
attempted blind on the running server. It needs a full verified backup first (now
satisfied) and a decision on the boot passphrase: type it on reboot (fine for a home
server that reboots rarely, painful if it reboots unattended after a power cut, and
Brook House is rural), or seal the key to a TPM (unattended boot, but then physical
possession of the whole machine defeats it — still fine against a pulled disk). A
reinstall with LUKS from the start is often safer than an in-place conversion. This is
Andre's decision, and "accept the risk for now given physical security" is a
legitimate answer.

**2b. Shadow — the machine that holds the private key. ANSWERED 2026-10-19: ENCRYPTED.**

Confirmed by Andre. This matters more than it sounds, and it is what makes the
asymmetric design hold in both directions:

- The **server** can create backups it cannot read. Its disk being unencrypted is
  bounded: a thief gets today's live data, not the history.
- **Shadow** can read them, and is protected against the threat a laptop actually
  faces — being picked up and carried off. Powered off, it yields no key, no
  decryptable archive, and no residue.

It also disposes of a smaller worry: after the 2026-10-19 practice restore, shadow
briefly held a decrypted bundle and two scratch Postgres databases full of this data.
`shred` on an SSD overwrites the logical block, not necessarily every physical copy —
but residue on an encrypted volume is encrypted residue, so the point is moot.

**3. Postgres-level encryption — probably not worth it.**

Community PostgreSQL has no transparent data encryption. `pgcrypto` column encryption
means the key lives in the application or in the database, encrypted columns cannot be
indexed or queried usefully, and every `pa_*` query that touches a protected field
would need rewriting. High cost, and it does not defeat a live compromise anyway
because the server holds the key. Skip unless a specific requirement appears.

**4. Application-level encryption of the sensitive subset — the only thing that
defeats a live compromise, and it costs operational comfort. STILL OPEN.**

Encrypt a chosen subset — the `investments`, `health` and `employment_situation`
categories, say — with a key that is *not* on the machine at rest: entered at service
start, or held on a hardware token. Then a compromised running server still exposes
them (the key is in memory), but a stolen disk, a leaked backup and a stopped machine
do not.

The real cost: the service cannot restart unattended. After a power cut, the server
stays down until Andre types a passphrase. For a system whose whole point is being
reachable from a phone at an EV charger, that is a genuine tradeoff, not a footnote.
**With item 2 known to be uncovered, this option also covers the stolen-disk case for
the subset it protects**, without converting the volume.

**5. Narrow the blast radius instead — cheap, and complementary. STILL OPEN.**

- The `pa_reader` role exists (nologin, 26 SELECT grants on `pa`) and is still unused.
  The read-only `pa_sql` tool could use it rather than the owning role, which holds
  182 grants.
- `.env` holds the database password, the auth token **and now the B2 application
  key** in plaintext, mode 600. Confirmed 600 as of 2026-10-19; keep confirming it.
  The B2 key is scoped to one bucket and the bucket has object lock, so even that
  credential cannot destroy the history — see below.
- Ask whether every category needs to be in the context store at all. The complete
  investment position and the income figures are there because they were useful in a
  conversation once. Some could live in a document Claude reads on demand rather than
  in a store the server serves on every call.
- **Remove leftover plaintext dumps** from `/home/dremon/backups/` and stop taking ad
  hoc plaintext dumps onto this disk. One remains:
  `futile_assistant-20260923-1631.dump`, plus whatever the briefs' own safety dumps
  leave. `backup.sh` itself is clean — its plaintext dump lives in a `mktemp -d` with
  an EXIT trap.

---

## DECIDED 2026-10-07 — encrypted off-machine backups

Status and what remains: `architecture/backup-status.md`.

### The design, and the one idea that makes it work

Backups are the single place this system can escape the trap above, and the mechanism
is **asymmetric encryption**.

`age` with a recipient public key on the server. The server holds only the public
half, so it can **create backups it cannot read**. Consequences worth stating plainly:

- Someone who compromises the running server gets today's data, but not the backup
  history.
- A backup file that leaks — left on a disk, copied to the wrong bucket, handed to a
  cloud provider — is inert.
- The private key never touches the machine that makes the backups.

This is strictly better than restic or borg with a repository passphrase, which is the
obvious alternative. Those need the passphrase present to run unattended, which means
it sits in `.env`, which means a server compromise hands over every backup ever made.

**The cost is real: lose the private key and every backup is unrecoverable.** Not
difficult — impossible. So key custody is its own small job, and it is the most likely
failure in the whole design, because keys get lost far more often than disks fail.

**Key custody status 2026-10-19:** keypair generated and verified (task 102 done);
held at `~/.age/futile-pa-backup.key` on shadow, mode 600, on an encrypted disk, and
in Andre's password manager. The printed copy for the estate documents is **task 111,
still owed** — and see the next section for why that copy is more sensitive than
"a backup key".

### The key is also a credential store — discovered 2026-10-19

The practice restore revealed that the archive is a full recovery **bundle**, not a
bare dump:

```
bundle/env                              ← .env: DB password, auth token, B2 key
bundle/personal-context/context.json    ← plus .1 .2 .3 rotations
bundle/futile_assistant.dump
bundle/futile_assistant.sql.gz
bundle/git-head.txt
bundle/personal-context-mcp.service
```

That is better engineering than the design note anticipated — a restore needs working
configuration and the context store, not just a database, and nobody should have to
remember what was in `.env` during an emergency.

**But it means: anyone holding the age private key plus any single backup file holds
the database password, the MCP auth token and the B2 application key.** The key is not
just a decryption key; it is the credential to the whole system.

Two consequences:

1. **Task 111 (the printed copy) is a higher-sensitivity artifact than it reads.** It
   belongs with the estate documents in a sealed, physically secured place — not in a
   drawer. Correct for its purpose: if Andre is incapacitated and Paolo or Edie need
   to recover anything, a key that exists only in his password manager is a key that
   is gone, which is the same problem project 29 exists to solve. But it should be
   stored as what it is.
2. **If the server is ever compromised, rotating `PGPASSWORD`, `PA_AUTH_TOKEN` and the
   B2 key does not retroactively protect old backups** — every archive still carries
   the credentials that were live when it was made. That is acceptable, because the
   archives are encrypted to a key the attacker does not have. It is worth knowing
   rather than discovering.

### The four choices Andre made

| | |
|---|---|
| **Destination** | Both — a fast off-machine copy over the tailnet, plus Backblaze B2 for off-site. A second machine in the same house survives a disk failure but not a fire, and Brook House is a wood farmhouse that burns wood. (The tailnet copy moved from `darkslip`, a laptop shut down at night, to `vocalassistant`, the always-on Pi, on 2026-10-16.) |
| **Cloud** | Backblaze B2, private bucket, application key scoped to that bucket only, **object lock on**. Cents a month at this size. Bucket created 2026-10-19. |
| **Versioning** | futile-pa became a **subdirectory of `personal-context-mcp`**. One clone, one pull; the server and the schema cannot drift to different versions. |
| **Monitoring** | A status file the system reads itself, rather than a third-party service. On failure the script also **creates a task**, so it surfaces in the daily brief with no code change to futile-pa. |

### Why object lock is the most valuable single setting here

The Pi protects against a **dead** server. An immutable bucket protects against a
**compromised** one.

Because the server prunes its own remote copies, an attacker with root on
futileassistant can erase every copy the server can reach — including the Pi's. B2
with object lock is the only copy that survives that, because the deletion is refused
by the storage provider rather than by anything the server controls.

**The cost is that GFS retention cannot apply to B2.** The server must never attempt
to prune it: a delete against a locked object fails every night, and a nightly error
is exactly the kind of noise that trains a human to stop reading the status file. So
B2 is append-only from the server's side, and expiry is the bucket's own lifecycle
rules. This is a deliberate divergence from the 7/4/12 policy used elsewhere.

The scoped key is belt; the lock is braces. The lock is the part that does not depend
on getting capability flags right in a web console.

### Verification, and the asymmetry it creates

A backup never restored is a hope, not a backup. But because the private key is
deliberately absent from the server, **the server cannot verify its own encrypted
backups** — verifying would require decrypting, which is exactly what the design
forbids.

So verification splits in two:

**On the server, every run, before encrypting.** Restore the plaintext dump into a
scratch database, run the 63-check suite against it, drop it. Then encrypt and ship.
If the suite fails, nothing is shipped and the run is a failure — shipping an
unverified dump is worse than not shipping, because it looks like success. This reuses
the test suite as a restore validator, a pleasing return on having written it. The
suite now also refuses to run against any database but `pa_verify` / `pa_scratch*`,
after it was once pointed at production and wrote 26 fixture rows into it.

**Off the server, annually, by Andre.** Recurrence 8, every February: fetch a backup,
decrypt it with the private key, restore the plain-SQL dump, and confirm the key is
still in both of its homes. February because it is indoor work in the quietest month,
far from the autumn pile-up.

That second one is the drill that matters. It is not really testing the backup — it is
testing whether the key still exists and still works.

### The restore that proved it — 2026-10-19 (task 113)

Run early rather than waiting for February, because until it happened every statement
above was a hope.

Andre decrypted `futile-pa-20261016-162037.tar.age` on shadow with
`age -d -i ~/.age/futile-pa-backup.key`, after verifying its sha256 first, and restored
`bundle/futile_assistant.dump` with `pg_restore` 18.6 into a scratch database.

**What makes the result conclusive rather than merely encouraging:** the comparison
baseline was taken from the *live* database deliberately **after** the snapshot was
made. Four tasks had changed in between. The restored copy showed 114 task rows, max
id 115, no rows at ids 116–117, and tasks 105 and 115 both still `next` — **the
correct past.** Matching totals could be coincidence; a correct past cannot be.

Also confirmed: all 14 functions and all 4 triggers restored, including
`tg_dependency_no_cycle`. Worth stating, because **a restore that returns every row
but silently drops the triggers looks perfect in a row count and has removed the
data's invariants** — you would discover it months later when something impossible got
into the data. And `pa.suggest_tasks` kept its full signature including `fit_bonus`
and `satisfiable_by`, so migration 003's generality penalty survived. The rows could
be retyped from memory in an afternoon; the scoring model could not.

**One gap the restore found** (task 119): `pg_dump` never dumps roles — they are
cluster-global, in `pg_authid`, outside any database. The bundle therefore lacks the
four roles its 208 grants depend on. The plain-SQL path exposed it as a wall of
`ERROR: role "pa_reader" does not exist`; the custom-format path was silent only
because `--no-privileges` had been passed. Fix is one line:
`pg_dumpall --globals-only --no-role-passwords > bundle/globals.sql`.
Without it, a bare-metal recovery yields a database nobody has permission to read and
an MCP server that dies on *permission denied*.

### Two details that are easy to get wrong

**Two dump formats, not one.** `pg_dump -Fc` for convenient `pg_restore`, *and* plain
SQL gzipped. A custom-format dump from PostgreSQL 18 cannot be read by an older
`pg_restore`, so the plain dump is what makes a restore possible on whatever machine is
actually available in an emergency. At this data size the duplication is free — and it
was the plain-SQL path that found the missing roles, so it has already earned its
keep.

**The plaintext dump never persists.** It lives in a `mktemp -d` with a trap on EXIT.
Everything written to `/home/dremon/backups/`, to the Pi or to B2 is already encrypted.

### What this does not cover

- **Machine-down is not detected.** The status file and the failure task both require
  the machine to be running. Andre accepted this: he uses the MCP server often enough
  to notice it being gone.
- **Warning tasks never self-resolve**, and the duplicate guard is title-exact with no
  time window, so **an open failure task suppresses any new one.** A stale alarm
  silences the alarm system. Task 105 did this for nine days; task 118 is doing it
  now. Fix is in the task-116 / B2 brief.
- **Wiring the status file into `pa_daily_brief`** is a futile-pa code change and
  belongs with the task-gates migration brief (backlog §H, once called 004,
  provisionally 009: task 218).
- **The server's disk is not encrypted** (item 2). Backups protect the history; they
  do not protect the live data on the machine.

### Tasks

- **102** — Generate the age keypair. **Done 2026-10-10.**
- **103** — B2 bucket and scoped application key with object lock. **Bucket created
  2026-10-19**; the server-side wiring is in the B2 brief.
- **111** — Print the private key and file it with the estate documents (35). **Still
  owed, and now understood to be a credential to the whole system, not only a
  decryption key.**
- **112** — Confirm LUKS status on the server. **Done 2026-10-10: none.**
- **113** — First practice restore. **Done 2026-10-19.** See above.
- **119** — Ship `globals.sql` in the bundle. Found by task 113.
- **Recurrence 8** — the annual February restore drill. Now a tested procedure rather
  than a plan.
