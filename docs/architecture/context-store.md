# The personal context store in Postgres (migration 009)

Written 2026-11-13. Decisions are Andre's from that day. The code is
`futile-pa/pa/profile.py`, the schema is `futile-pa/migrations/009_profile_store.sql`,
and the server brief is `~/developer/tmp/CONTEXT_STORE_BRIEF.md`.

## Why

Andre: *"I should be able to add categories at will and update the information. It
should be better managed and protected than the json file architecture currently in use."*

What the file actually was, measured 2026-11-13:

| Property | context.json |
|---|---|
| Writers | four processes (MCP server, the agent's scheduler and Signal-bridge threads, `recurrence_runner.py`) through **two** implementations, each with only a per-process lock (task 223) |
| History | `.1–.3` rotations written milliseconds apart by one run, so about one second |
| History kept by hand | keys like `ira_holdings_apr2026`, `transition_status_oct2026`, `plan_status_sep2026` |
| Size | 18 categories, ~60k characters; `investments` alone ~22k |
| Who reads everything | any caller of `get_context()` with no category: every claude.ai chat, Claude Code, and the Signal agent |
| ad-hoc SQL | not applicable (a file), but it would have been readable by `pa_sql` had it simply moved into `pa` |

## What it is now

```
profile.category   slug (pk, ^[a-z][a-z0-9_]{0,62}$), description, tier normal|sensitive
profile.entry      (category, key) pk, value jsonb, updated_at, updated_by
profile.history    append-only; one row per change, old and new value, actor
pa.agent_state     (namespace, key) pk, value jsonb — task_agent, task_agent_watchdog,
                   recurrence_runner; no history
pa.agent_state_code_key   task 213's four code-owned keys, enforced by trigger
```

The same four tools (`get_context`, `set_context`, `list_categories`, `delete_context`)
with the same arguments, plus `context_history` and `context_category`. One
implementation, `pa.profile.ContextService`, used by `server.py` (scope `CHAT`) and by
`assistant/context_store.py` (scope `AGENT`).

## Decisions, with what was rejected

1. **Its own schema, walled off from `pa_sql`.** `pa_sql` now runs `set local role
   pa_reader` inside its read-only transaction. `pa_reader` was created by migration 001,
   never used, and already has the app role as a member, so no superuser step. It has
   SELECT on `pa` and nothing on `profile`. *Rejected:* tables inside `pa` (the agent's
   `pa_sql` could then query medical and financial facts directly); a second database
   (a cross-database query is impossible, but backups and restores double). `pa.context`
   was already taken by the situational contexts, which is one more reason for a new name.

2. **Every version, forever.** Written by triggers, so no code path can skip it; append-only
   by trigger (UPDATE, DELETE and TRUNCATE refused). The trigger guards against a wrong
   code path, not against the table's owner, who could drop it. *Rejected:* pruned
   history (an old figure vanishes after the window) and `updated_at` only.

3. **Tiers are scoping, not security.** A sensitive category is left out of an unscoped
   `get_context`, which lists it in an index instead, and the agent scope can never read
   it. Both agent paths (the check-in and Signal replies) use the agent scope, because
   both write into Signal history, which cannot be recalled. The agent may still *write*
   to a sensitive category: that exposes nothing, and history keeps what it replaced.
   **Andre's question, answered:** the server has one bearer token on Funnel and no
   per-client identity, so anyone holding `PA_AUTH_TOKEN` can ask for any category by
   name, and a lost phone with the Claude app signed in is roughly the same exposure
   as a lost phone with Signal. Tiers keep the unattended agent from loading what it
   doesn't need, and stop an unscoped read from pulling 20k characters of investments
   into a conversation about firewood. Protection against theft is the **kill switch**,
   `scripts/rotate_token.py`. Per-client tokens are possible on 639cf6c's
   one-server-per-scope pattern; they are not built.
   Starting tiers: `health`, `investments`, `employment_situation` sensitive. Change with
   `context_category`; each change is in the history.

4. **Agent state in its own table.** Written several times a day by code, it would flood
   the history, and it isn't personal context. The four code-owned keys are now refused
   by the database (`pa.agent_state_guard`) unless the transaction declares itself code
   (`set_config('pa.writer','code',true)`), which only `ContextService.set(code=True)`
   does, and only `ledger.py` calls it that way. `agent_keys.py` keeps the refusal
   *message*; `tests/test_profile.py` asserts the two lists agree.

5. **Deleting a category's last key keeps the category.** context.json pruned it. The row
   carries the tier, and a sensitive category that quietly came back as normal on
   re-creation would be the wrong default. Deleting the whole category is explicit.

6. **Compatible tool surface.** Briefs, the agent and the claude.ai connector keep working.
   One visible change: an unscoped `get_context` no longer includes sensitive content or
   agent state. It lists them. Tool count 34 → 36.

7. **Migration 009.** Task 218's unbuilt task-gates spec moves to 010.

## Concurrency (task 223)

Every write is one row upsert in its own transaction, so two writers to different keys
can no longer lose each other's update. Same-key writes are last-writer-wins, and both
are in the history. `tests/test_profile.py` shows the old algorithm (whole-blob
read-modify-write) losing keys under four threads, and the new one losing none from
four connections. `ledger.py` still read-modify-writes several keys under an in-process
lock. That is enough because only the agent process may write those keys: `server.py`
and the database both refuse everyone else.

## What reads it

| Reader | Path |
|---|---|
| claude.ai, Claude Code | `server.py` context tools, scope CHAT |
| Check-in agent, Signal replies | `toolset.dispatch` → `context_store` → ContextService, scope AGENT |
| `ledger.py`, `signal_bridge.py`, `checkin.py`, `recurrence_runner.py` | `context_store.get/set` (agent namespaces) |
| `external_watchdog.py` | `pa.agent_state` directly. An unreadable database is `STORE_UNREADABLE`, which alerts, and the alert still goes straight to the Signal container |
| backups | the nightly `pg_dump` (the whole database). `backup.sh` copies context.json only while it still exists |

## Not done, and why

- **Per-client tokens.** The mechanism exists (639cf6c). Not asked for yet.
- **`as_of` dates on entries.** Several values are snapshots ("holdings, April 2026").
  History now records *when a value was written*; when the fact was *true* is still in the
  text. Worth a column if the interview shows it matters.
- **The path-token form (`/t/<token>/`).** A URL ends up in more logs than a header. Whether
  the claude.ai connector needs it is a question in the brief.
