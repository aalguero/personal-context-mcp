# Standalone assistant — a real agent, not a scheduled task

Started 2026-10-27/28, in design. Supersedes the "just schedule it" framing of task
181 — Andre's correction: *"this is a scheduled task in claude desktop, not an
independent agent that can work independent of the state of an another application.
I need to be able to ask this agent — and only this agent, not a general chat
session — for a task when I want to work on something quick."* Plus a stated future
need: real-time shared state (a grocery list edited live at the store), which a
periodic job can never provide no matter how reliable it is.

This doc is the design to review before any of it is built. Nothing described here
is deployed yet.

## The two problems, and why they need different homes

1. **Autonomous background work** — the 4x/day check-in. Needs to run whether or not
   any app is open. Doesn't need a UI; it already writes to the task system and pushes
   notifications.
2. **On-demand, addressable interaction** — ask it something quick, edit a live list.
   Needs a standing front door: one identity you can reach any time, not a Claude
   Desktop session you have to remember to open.

Direction chosen (over a fully custom build and a fully Claude-hosted build): a
**hybrid**. One backend service, on futileassistant, that both runs the periodic job
and answers on-demand requests. Two front doors into it — Signal and a published
Artifact — rather than one, because "ask it directly" should feel like texting
someone, and "glance at the list while standing in an aisle" should feel like opening
an app, and those are different interactions.

## What "same backend" means here — DECIDED 2026-10-28

Same prompt, same tools, same data — **two thin clients, not one shared process.**
Signal talks to the new backend process directly (it has to; Signal has no other way
in). The Artifact talks to the **existing** futileMCP server directly via its `mcp`
capability — no new endpoint — and carries its own copy of the on-demand system
prompt via the Artifact platform's own "ask Claude" capability. Same rules, same
task data, same personality, because both read the same brief document and call the
same `pa_*` tools. **Confirmed by Andre 2026-10-28** over the literal alternative (one
process both front doors call into, which would need a new exposed endpoint). No new
attack surface beyond the Signal bridge itself, which is unavoidable — Signal is the
only way in for that front door.

## Component 1 — the backend service (futileassistant)

New directory in the repo: `futile-pa/assistant/`. Python, reusing what already
exists rather than re-implementing it:

- `pa.tools.dispatch()` is already "framework-agnostic" per its own docstring —
  built to be called directly, not just through the MCP transport. The backend
  imports it and calls it in-process. No new HTTP hop to the MCP server; same
  machine, same venv (`/home/dremon/tools/personal-context-mcp/.venv-new`).
- The agent loop itself is the same ReAct pattern `agentic_lab` already has working
  examples of (`agent_production.py`'s shape: call the Messages API with tool
  definitions, dispatch tool_use blocks, loop to a final text response) — just
  pointed at `pa_*` tool schemas instead of toy tools. Worth building it as a small,
  shared `agent_loop.py` so the check-in job and the Signal handler both call the
  same function with a different system prompt, rather than duplicating the loop.
- **Two system prompts, not one:**
  - *Check-in prompt* — unchanged, still `docs/claude/task-agent-brief.md`, still the
    narrow task-maintenance-only remit, still the hard limits in its §5 (no
    recurrences, no importance changes on its own judgement, no restructuring).
  - *On-demand prompt* — new document (see below), broader latitude **because Andre
    is live and can catch a mistake immediately**, which the check-in agent's
    unattended 7:48am run never gets the benefit of.
- **Tool surface is identical and narrow by construction either way**: both prompts
  only get the `pa_*` / context tools. Neither gets Bash, file access, or anything
  that could touch `server.py`, a migration, or the test suite — so most of
  `CLAUDE.md`'s hard rules are structurally unreachable here, not just
  instructed-against. The ones that still apply (never print `PA_AUTH_TOKEN` /
  `PGPASSWORD` / `.env` contents; never touch `context.json` other than through
  `set_context`) go in **both** prompts regardless of latitude.
- **Scheduling:** one internal scheduler (e.g. checking wall-clock time each loop
  tick, or `APScheduler`) replicates `CRON_TZ=America/New_York 48 7,11,15,19 * * *`
  exactly — this needs the server's own timezone confirmed, or hardcode
  `America/New_York` via `zoneinfo` rather than trusting the OS default.
- **Deployment:** one systemd **user** service on futileassistant (own unit, not
  folded into `personal-context-mcp.service` — keeps blast radius separate),
  `Restart=on-failure`, same pattern as the existing MCP service.

## Component 2 — Signal bridge — DECIDED 2026-10-28: Signal, not Telegram or SMS

**Why Signal.** True end-to-end encryption, audited protocol — the message content
is unreadable in transit to anyone but the two endpoints, which neither Telegram
(TLS to Telegram's servers, not E2E for bots) nor SMS (unencrypted over SS7 between
carriers, plus a third-party gateway provider in the loop, plus per-message cost,
plus SIM-swap exposure) can offer. SMS and Telegram are dropped from this design.

**How it connects — `signal-cli-rest-api` in Docker — DECIDED 2026-10-28.** There is
no official Signal bot API, so the backend talks to the community
`bbernhard/signal-cli-rest-api` container, which wraps `signal-cli` in a plain HTTP
interface. Chosen over the raw `signal-cli` JSON-RPC daemon now that installing
Docker on futileassistant is on the table (Andre, 2026-10-28) — the same reasoning
already used for `vocalassistant`'s voice stack applies here: a CI-built image
freezes the dependency set and avoids hand-rolling the integration, and the REST
wrapper also ships a **built-in QR-code linking endpoint**, so no manual `signal-cli`
CLI dance to pair the device. **Docker install should follow the same pattern as
`vocalassistant`**: Debian's packaged `docker.io` + `docker-compose` from the
already-trusted apt sources, not Docker's own repo/signing key — one fewer new
trust root on a machine that also holds the task database. `signal-cli` still has to
be **registered or linked** before any of this works:

- **Linked device** (simplest): link it to Andre's own Signal account via QR code,
  the way Signal Desktop links. Free, no new phone number. The one real quirk: the
  conversation then looks like Andre messaging *himself* — a "Note to Self"-style
  thread — which is a fine UX pattern but worth knowing going in.
- **Separate registered number**: its own identity, needs a real number (a cost and
  a setup step, same category of friction as a Twilio number would have been for
  SMS) but reads as a distinct contact rather than a self-conversation.

**Proposing the linked-device route** as the default — no recurring cost, no second
phone number to manage — unless the self-conversation UX is a dealbreaker.

- **No inbound port, no Funnel/Tailscale change.** The container maintains its own
  outbound connection to Signal's servers; the backend talks to it over
  `127.0.0.1` only, container port not published beyond localhost. Respects hard
  rule 3 outright.
- **The linked-device credentials** (the container's persisted `signal-cli` data
  volume) are the equivalent of a bearer token — same treatment as `.env`: host
  directory mode 600, never read or printed by Claude, never copied off the server.
- **A sender allowlist is not optional**, same reasoning as it would have been for
  Telegram: restrict to Andre's own number as the only sender the backend will act
  on, checked before any tool call.
- **Optional defense-in-depth, now lower priority since transit is already E2E**: a
  content filter that genericizes outbound messages for tasks under `Health`,
  `Finances`, or `Estate Documents and Authorizations` — e.g. "Task 146 needs an
  update" rather than naming the oncology appointment. Mainly useful against a
  compromised or stolen phone rather than transit interception, which Signal already
  solves. Worth keeping as cheap insurance; not a blocker for Phase 1.
- Short in-process conversation memory per chat (so "add milk too" after "add eggs"
  makes sense), separate from the check-in job's `task_agent` context-store state —
  these are two different kinds of memory and should not share a key.

## Component 3 — the periodic check-in's new home

Moves from the Claude Desktop local scheduled task (`task-checkin`) into this same
backend's internal scheduler, reading the same `docs/claude/task-agent-brief.md` off
the server's checkout. **Retire `task-checkin` the same way `trig_01LP3NydyjH9kb7VsfbjsLXY`
was retired** — task 181's own closure notes already wrote this playbook: disable
first, prove the replacement over several real cycles with no overlap, delete (if at
all) only after. Same context-store category (`task_agent`) carries across so the
new job doesn't re-ask everything the old one already asked.

## Component 4 — the Artifact (Phase 3, built after 1–3 are proven)

A published, bookmarkable page declaring the `mcp` capability against the existing
futileMCP connector — no new backend dependency, per the "same backend" decision
above. Needs the `artifact-capabilities` skill loaded before it's built (not done
yet). Carries its own copy of the on-demand prompt text for Claude-in-the-artifact
calls, kept in sync with the backend's copy by having both read the same doc rather
than each hardcoding the prompt.

## Draft: the on-demand brief (first cut — review and amend)

Lives at `docs/claude/quick-ask-brief.md` once written. Differences from the check-in
brief:

**May additionally do** (beyond everything the check-in agent may already do):
- Change importance directly, when Andre states it in the exchange.
- Restructure — move a task between projects, re-parent it, rename a project.
- Reopen a `someday` task, or create a recurrence — **with a confirm-back step**
  first ("daily, starting tomorrow, right?"), because `pa_recurrence_add` is
  create-only at the tool level regardless of how much prompt latitude exists; a
  mis-specified series is still permanent. Latitude is about what it's allowed to
  attempt, not a reason to skip the one confirmation that actually matters here.

**Still must not, same as the check-in agent:**
- Print or echo any secret.
- Touch `context.json` other than through `set_context`.
- Drop or delete a task unless Andre says so in that exchange, in those words.
- Pretend a write happened when it didn't (the `contexts`-on-`pa_task_update`
  no-op trap applies here too).

**Reporting:** unlike the check-in agent's batch report, on-demand is conversational
— it should just say what it did, in the moment, the way a competent assistant would
confirm an action back to you.

## Sequencing

1. Build the backend service + its own system prompt pair. Write a brief, deploy via
   the usual loop, verify with real cycles before touching anything live.
2. Signal bridge (`signal-cli`, linked-device route unless you say otherwise), tested
   manually — message it, and separately confirm the sender allowlist actually
   blocks a number that isn't yours.
3. Once Component 3 has run cleanly for a few real cycles with no overlap, disable
   `task-checkin` (the Claude Desktop scheduled task) the same way the cowork trigger
   was retired.
4. The Artifact.
5. Lists (Phase 4) — deliberately not designed here. Needs its own schema decision
   (tasks-in-a-project vs. a new `pa.list`/`pa.list_item` table) once the foundation
   above is real and in daily use.

## Open questions for Andre

- **Resolved 2026-10-28:** "same backend" = same prompt/tools/data, two thin clients.
  Transport = Signal, not Telegram or SMS, via the `signal-cli-rest-api` Docker
  container (Debian's `docker.io` package, same install pattern as `vocalassistant`).
- Linked-device vs. separate registered number for `signal-cli` (default proposed:
  linked device — free, but reads as a self-conversation).
- Anything in the on-demand brief draft above that should be tighter or looser.
