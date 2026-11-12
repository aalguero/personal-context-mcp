# futileMCP / futile-pa — orientation for Claude Code

> **Rewritten 2026-11-11.** This file is now a pointer. The project's state, rules,
> decisions and backlog live in **`docs/claude/claude-code-handoff.md`**, imported
> below. This file holds only what differs for a **Claude Code session on shadow**.
> Where the two disagree, the handoff is newer for project facts; this file is newer
> for facts about shadow. If either disagrees with what you observe, **trust what you
> observe and say so.**

@docs/claude/claude-code-handoff.md
@docs/claude/working-agreement.md

Read on demand: `docs/architecture/deployment-status.md` (current state and the
corrections log), and the rest per handoff §11.

## You are on shadow, not the server

shadow is Andre's laptop (OS user `andre`, tailnet DNS `darkslip`). Everything that
runs — Postgres, the MCP server, the check-in agent, Signal, the timers — is on
**futileassistant**, and Andre is the only route there. **Don't SSH to it.** Don't SSH
to `vocalassistant` either unless he says so.

| Thing | Where | How you reach it |
|---|---|---|
| Code | `~/developer/futile-pa`, a clone of `github.com/aalguero/personal-context-mcp` (futile-pa is the `futile-pa/` subdirectory on the server) | Edit and commit here |
| Brief exchange folder | `~/developer/tmp` | Read and write directly with Read/Write — **not** `device_commit_files`, which is the chat session's tool |
| Live data | Postgres `futile_assistant`, schema `pa` | The futileMCP tools if this session has them. `pa_sql` is read-only |
| Running system | futileassistant | Only through a brief (handoff §3, and the `server-brief` skill) |

## Where the handoff's chat-session framing doesn't apply

The handoff was written from the claude.ai chat side. For this session:

- **Writing files:** use Write/Edit here. The rule still holds — verify a write by
  reading back content that exists only in the new version.
- **Git:** commit here, straight to `main` (no PR workflow). **Pushing is Andre's** from
  his own terminal unless he says otherwise; whether this session can push has not been
  tested. A commit here is not on the server until it is pushed *and* pulled there,
  and not live until whatever reads it restarts or re-reads.
- **`docs/claude/task-agent-brief.md` is live config for the server agent**, but only
  once it is pulled on futileassistant. Editing it here changes nothing until then.

## Things on shadow

- **The check-in agent runs on the server** (`futile-assistant.service`). A leftover
  local scheduled task `task-checkin` (`~/.claude/scheduled-tasks/task-checkin/`) still
  exists from October; verified **disabled** on 2026-11-11, last run 2026-10-27. Never
  re-enable it — two agents would ask Andre every question twice.
- **`~/developer/tmp/personal-context-mcp`** is a stale clone at `5c21332`
  (18 September). Ignore it.

## Things on shadow you must not touch

- **`~/.age/`** holds the age private key that decrypts every backup. A bundle also
  contains `.env`, so this key plus any archive is the credential to the whole system.
  Never read, copy, print or move it. Never decrypt a backup unless Andre asks in that
  conversation, in those words.
- **Never create a local copy of the context store.** It lives only on the server,
  reached through `get_context` / `set_context`. An unsynced copy of medical and
  financial facts gets read as current. (A stale one was deleted 2026-10-26.)
- **Stay in** `~/developer/futile-pa`, `~/developer/tmp` and clones you make yourself.
  `~/FinanceComm`, `~/Documents` and the rest are outside this project.

## Hard rules

Handoff §6 is the list, and it is copied into every brief. Two additions from this side:

- **Applied migrations are 001–005** (as of 2026-11-11). Check `pa.schema_migration`
  (keyed by filename) before assuming the next number.
- **Never create a recurrence**, and **in a Build session, never make a life write
  unless Andre asks** (working-agreement, *Three rules*). Noticing is not permission.

## The lesson that keeps recurring

**When you have said you cannot check something, that remains true three sentences
later.** On 10-27 a session said it couldn't read GitHub, then reasoned from directory
timestamps to "Andre never pushed". He had. *I can't verify this from here* is always an
available answer.
