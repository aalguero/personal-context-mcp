# Backlog — personal assistant

Last updated: 2026-10-06, end of session. **Triage of the Google Tasks import is
COMPLETE — the inbox is empty and nothing is overdue.** Phase 1 is deployed,
connected, cleaned up, loaded with real data and fully triaged. See
`architecture/deployment-status.md` for state and
`architecture/phase-1-task-backend.md` for design.

**Next action for a session picking this up cold: section H, the task-gates migration,
provisionally 010.** The triage is finished and the gate design now has its full evidence
base. **The number is not reserved.** 004–009 were taken by other work while this spec
was still called "004" (task 218). Read `pa.schema_migration`, which is keyed by
filename, and take the next unused number when you build it.

---

## A. Housekeeping — DONE 2026-09-21

Eight commits in `github.com/aalguero/personal-context-mcp`. Verification passed:
29 tools, 14 categories, 9 situations / 18 contexts, 63/0 on the task suite,
`/.well-known/` still 404.

- [x] Repo reviewed. Clean — no `.env`, no `.venv`, no `.bak` committed;
      `.gitignore` correct; the `mcp==1.27.0` pinning comment explains the hazard
      well enough that a future reader won't undo it.
- [x] **Legacy SSE transport removed.** `/sse` and `/messages/` gone; `--sse`
      survives as a hidden alias for `--http`. `starlette_app` is now an
      empty-route app that exists only to carry the `lifespan` — **commented as
      such, because deleting it breaks `/mcp` with "Task group is not initialized"**.
- [x] **`context.json` writes are atomic.** Temp file + fsync + `os.replace`, plus
      three-generation rolling backups. Was the largest risk in the codebase.
- [x] **`requirements.txt` completed.** psycopg, psycopg_pool, python-dateutil.
- [x] **Postgres decoupled from the context tools.** A missing DSN no longer kills
      the process before the four JSON-store tools can serve.
- [x] **`FUTILE_PA_PATH` env var** replaces the hardcoded `sys.path.insert`.
- [x] **Duplicate `db.py` removed.** Diffed first: byte identical.
- [x] Smaller: unused import; timezone-aware `datetime`; `hmac.compare_digest` on
      the path-prefix auth branch; `arguments` normalised once; README.
- [x] Disk cleanup: stale `.bak` files and the original broken `.venv` deleted.

`context.json` and its backups are now mode **600** (were 664), a side effect of
`tempfile.mkstemp`. Correct for a file holding medical and financial detail.

## B. NEEDS A DECISION — futile-pa packaging

Raised four times and still Andre's call. futile-pa is an **unversioned** sibling
checkout that `server.py` reaches into via `sys.path`. The repo describes half the
system: the 25 `pa_*` tools, the schema, the migrations and the 63-check suite are
in no repository at all.

Options: an installable package (`pip install -e`), a git submodule, its own repo
with a release process, or a subdirectory of the existing repo.

**This is now the largest risk on the project, not just the largest gap.** The
database holds a fully triaged task set plus several hundred lines of hand-written
reasoning in task notes that exists nowhere else. Migration 003 is applied, 004 is
specified. All of it sits in one directory with no version control and two
`pg_dump` files on the same disk as the database. `migrate.py` refuses to re-run a
file whose checksum changed, so the applied SQL is irreplaceable.

**Pair this with the backup item in F. Together they are the one thing that could
lose the work.**

## C. People and seeded data

- [x] Paolo (son, Cambridge MA), Edie (daughter, N. Kingstown RI).
- [x] Nate (nephew, Montgomery MA; partner Jennifer, six children).
- [x] Lance (friend) — possible metalwork for Seven Fires.
- [ ] **Gary.** The record called "Senior neighbor" (Huntington MA, 10-day cadence)
      is almost certainly Gary, who holds spare keys to Brook House under a mutual
      support arrangement. **Ask before renaming.**
- [ ] Sister and Father still carry role names. Sister is in New Mexico — relevant
      to the estate filing. Father is 87.
- [ ] **Contact cadences** (30/30/14/21/60/10 days) are still Claude's inferences.
- [ ] **Prune or rename seeded areas and projects.** Seeded rows carry
      `metadata->>'seeded' = 'true'`.
- [ ] **No person record for the chainsaw repair shop, the septic company (Linton),
      the chimney sweep or an electrician.** `pa_task_add` rejected `waiting_on:
      "shop"` for exactly this reason. Tradespeople are not family, but they are
      the people tasks wait on most often. Worth deciding whether `pa.person`
      should hold them or whether a separate `vendor` concept is wanted.

## D. Import Google Tasks — COMPLETE

- [x] 78 curated tasks imported 2026-09-23, all at `inbox`.
- [x] Migration 003 applied: `land_day` no longer provides `@home`;
      `pa.suggest_tasks` gained the generality/fit bonus.
- [x] **Triage complete 2026-10-06.** See G.
- [x] Seven recurrences created.
- [ ] Remaining recurrence candidates: gutters, septic (needs Linton's interval),
      water heater (becomes one on first completion of task 20), Rosco's
      medications.

## E. Cohesion — remaining seams

- [ ] **Two competing "context" concepts.** The freeform JSON store versus
      `pa.context` (18 situational tags). Same word, different meaning, same
      server. Most worth resolving — a trap for humans and models alike.
- [ ] **Logging.** The `pa` modules don't log, so `pa_*` failures are invisible to
      a server-side operator.
- [ ] **Tool description voice.** Legacy four are conversational; `pa_*` read like
      API docs.
- [ ] **Code organisation.** `context_tools.py` would leave `server.py` as
      transport wiring plus a tool-table merge.

## F. Phase 2 — capability

- [ ] **Encrypted off-machine backups.** Still none, and now urgent — see B.
- [ ] **Live dashboard.** Artifact `mcp` capability → futileMCP → Postgres.
      Unblocked, not started. Works from his phone.
- [ ] **Calendar joins.** Proved useful this session: the chimney sweep date was
      pulled straight out of Google Calendar rather than asked for. Worth doing
      properly — conflict detection ("four hours of land work on a day with two
      calls") needs calendar events alongside `scheduled_for`. Note that Calendar
      already carries recurring bill reminders (Sallie Mae, Eversource, car lease),
      which overlaps with the bills item below.
- [ ] **Bills / payables.** See above — some of this already lives in Calendar.
- [ ] **Meals and groceries.** Own tables, with a vitamin-K field on ingredients.
      Given the INR constraint that belongs in the schema, not in memory.
- [ ] **Finance.** Connected but separate. Real tables once Mercer settles.
- [ ] **A procedure skill** — morning triage, weekly review.
- [ ] **Situation vocabulary split.** `@phone` conflates things that differ: can
      you talk aloud, are you overheard, are your hands free, can you type.
      Proposed `@call` / `@private` / `@handsfree` / `@screen`. **Now cheaper to
      judge than before**, because every task is tagged and the suggestion lists
      can be inspected against real data. Not yet decided.

---

## G. TRIAGE — COMPLETE 2026-10-06

**Final state: 53 `next`, 1 `in_progress`, 6 `someday`, 4 `waiting`, 10 `done`,
27 `dropped`, 0 `inbox`, nothing overdue.** Seven recurrences, five new projects.
78 imported tasks became 101 rows.

Method: one task at a time, with clarifying questions, over seven rounds. Andre
chose this over rule-based triage and it was the right call — see "What the method
caught".

### Rounds

1. **Deadline-driven.** Networking list → recurrence 1. Task 15 split: closing
   Mom's Bank of America account (80) blocks the New Mexico final accounting (15),
   which is why it sat since 2025. HIPAA (39) and estate documents (35) into a new
   `estate-docs` project. "Contact Comp re. Papa's finances" → "Get all Papa's
   financial information together" at importance 5.
2. **Middle of the pile.** Snuff-box handover reframed as gated on Paolo visiting.
   "All trees" → parent with own survey (81) then budget-gated arborist (82).
   Seven Fires became a project targeting 2027. Stereo turned out to be Anthem ARC
   room correction, gated on Rosco being out of the house.
3. **Native garden / land.** Three nest-box tasks merged into recurrence 3.
   "House & Land" renamed **Brook House** per Andre's own framing; **Land
   Maintenance** added beneath it.
4. **Fly tying and Vehicles.** Seven material items collapsed into one standing
   order list (84). Leaders → recurrence 4. Jeep rack → parent 32 with chained
   subtasks 85/86/87. Winch → task 73 now plus recurrence 5 from 2027.
5. **Basement and moisture.** New project 32. The MassSave call (89) is the
   headline: they wanted to install the crawlspace barrier and were blocked by
   water that has since been fixed, so the programme may still cover it.
6. **Mechanical and interior.** Heat pump split three ways — 91 eligibility call,
   40 financial analysis, 92 installation (`someday`). Bathroom refurb → parent 68
   with chained 93/94/95.
7. **Barn.** Clear-out → parent 12 with chained 96/97/98/99. Barn exterior and
   picnic table deferred to May 2027 on cure-temperature grounds. Mower wheel (46)
   plus a sale task (100), because the repair exists to enable the sale.

### Closed out on 6 October, after triage

- **49 Order tree-length wood — DONE.** Delivered.
- **59 Chimney sweep follow-up — DONE.** Booked **Tuesday 24 November, 8:30am**,
  found in Google Calendar. Calendar reminders already set at 7/3/1 days.
- **66 Tractor filters — DONE.** Changed October 2026.
- **74 Split and stack wood — IN PROGRESS.** One cord done.
- **101 Collect the chainsaw from the shop — WAITING.** Dropped off 5 October.
  Importance 4, because tree-length delivery means bucking is Andre's job and
  bucking is chainsaw-only, so this blocks the rest of the winter's wood.

### New projects

- **Backup Power** (28) — Generator (33) and conduit (34), `someday`, budget-gated.
- **Estate Documents and Authorizations** (29) — HIPAA (39), document set (35).
- **Seven Fires Cooking Setup** (30) — target 2027-09-30. The Lance conversation
  (44) is the free step that unlocks it; the spec (83) depends on it.
- **Land Maintenance** (31) — firewood, tree survey, nest boxes, window
  deflectors, kayak shelter, chainsaw.
- **Basement and Moisture** (32) — target 2026-12-31. Ordering written into its
  description: **89 call MassSave → 24 rodent-seal walls + 29 flash and seal the
  door → 69 lay the barrier**, with 90 radon test before 69.

### Recurrences

| id | Title | Mode | Rule / interval | Lead |
|---|---|---|---|---|
| 1 | Update networking list and reach out | fixed | weekly Mon+Wed | 0 |
| 2 | Jagger's come-home day — buy him a treat | fixed | 8 July | 10 d |
| 3 | Clear and repair all nest boxes | fixed | 25 Oct | 14 d |
| 4 | Make up leaders for the season | fixed | 1 Feb | 21 d |
| 5 | Winch test before winter | fixed | 15 Oct, from 2027 | 14 d |
| 6 | Check tractor hours against filter interval | after_completion | 180 d | 0 |
| 7 | Check the wood supply and order if needed | fixed | 7 Aug, from 2027 | 14 d |

**Two of the seven are deliberately framed as checks rather than as the work,
and this is a pattern worth reusing.** Recurrence 6 exists because the tractor's
service interval is measured in engine hours while this system runs on dates — so
the series prompts a ten-minute reading of the hour meter, not a filter change.
Recurrence 7 exists because whether wood needs ordering depends on what is on the
stack, and Andre currently has deep stock — so it prompts a walk round the pile,
not a phone call. In both cases, recording the number each time (hours; cords
burned, supplier, price) converts a judgement call into arithmetic after two or
three years.

August for the wood, incidentally, is not about having spare time: green hardwood
needs six to twelve months to season, so an August order seasons through autumn
for the *following* winter. Ordering in October — which is what happened in 2026 —
buys into a season already underway, at a higher price, from suppliers already
booked.

### Dated commitments outstanding

| Due | id | What |
|---|---|---|
| 2026-10-13 | 17 | Get all Papa's financial information together |
| 2026-10-16 | 89 | Call MassSave re. crawlspace vapour barrier |
| 2026-10-23 | 91 | Ask MassSave about heat pump incentive window |
| 2026-10-24 | 28 | Get an outdoor metal ash can with a lid |
| 2026-10-31 | 24 | Seal flagstone wall openings against rodents |
| 2026-10-31 | 73 | Winch test — first annual run |
| 2026-10-31 | 20 | Drain the water heater |
| 2026-11-07 | 51 | Weatherproof parcel box |
| 2026-11-14 | 19 | Jagger's window wall and perch |
| 2026-11-15 | 90 | Radon test (before the barrier) |
| 2026-11-24 | — | **Chimney sweep visit, 8:30am** (Calendar, not a pa task) |
| 2026-11-30 | 52 | Build and mount barred owl house |
| 2026-12-31 | 69 | Crawlspace vapour barrier |
| 2026-12-31 | 63 | Hand off snuff boxes to Paolo |
| 2027-04-10 | 6 | Apple tree root barrier (deferred) |
| 2027-05-10 | 53, 58 | Barn exterior, picnic table (deferred — cure temperature) |

The October dates sequence usefully: the ash can and the rodent sealing both land
before the sweep arrives on the 24th, so the stove area is in order when he is on
site and can be asked about flue condition and draft given the increased burning.

### Duplicates and corrections

- **One cellar door had three task rows** — 16, 27 and 29. 29 survives.
- **`24` was rewritten three times** — moisture sealing, then structural
  repointing, then rodent exclusion — because the imported title was "Fill
  cracks", which records no purpose. Final: hardware cloth or packed copper mesh
  in the through-gaps of the flagstone foundation walls, lime mortar as finish
  only. **Never Portland on old stone**; it traps moisture in the stone, which
  freezes and spalls the face off.
- **`70` is not a septic problem.** Septic recently checked and cleaned (task 11
  closed as already done). The slow floor drain is likely a flow-rate mismatch
  rather than a fault — a washer pumps 15–20 gallons in a minute or two, more than
  a 2" line on a long shallow run can carry. The fix that works either way is a
  laundry sink or standpipe as a buffer.
- **`3` stalled on a wrong approach.** A fridge is levelled from the **front
  feet**, reachable after popping the toe grille — no lifting. The inflatable lift
  was solving a problem that does not exist.
- **`49`/`74` split** — a ten-minute phone call and a four-hour splitting session
  belong in different situations. The point of the context engine, and it paid off
  within two weeks: the call completed while the splitting is still in progress.
- Dropped as overtaken or unidentifiable: 1 (defensive stock limits — Mercer now
  manages), 54 (RMV), 78 (Edie's graduation), 55 ("Repair"), 22 (door frame,
  Andre believed it a duplicate — **the pair was never identified, so recapture if
  a side-door job turns out to be real**).

### Recommendations made unprompted

- **89, call MassSave first.** Twenty minutes may replace four hours in a
  crawlspace, on their programme's money.
- **90, radon test before the barrier.** Dirt floor plus open joints in stone walls
  is a direct soil-air path; western Massachusetts sits on granite bedrock; and
  weatherization makes a radon problem worse, because the tightness that keeps warm
  air in keeps soil gas in. Before-and-after is the only way to learn whether the
  barrier helped.
- **98, wet down before cleaning barn droppings.** Hantavirus and *Histoplasma*
  are inhalation hazards from dried droppings made airborne. Mask plus spray bottle
  removes the risk; dry sweeping or a shop-vac without true HEPA creates it.
- **No rodenticide**, for Rosco and Jagger and because Andre is building a barred
  owl box (52) — owls die of secondary poisoning from eating poisoned mice.
- **50, bundle the EV circuit with the barn feed.** Once a trench is open, adding
  capacity costs far less than it ever will again. Even empty conduit is cheap.
- **53, consider stain rather than paint on the barn.** Paint forms a film that
  peels on old dry boards, after which every repaint starts with scraping a whole
  barn. Stain penetrates and fades instead.
- **28 at importance 4.** Wood ash holds live embers for three to four days. With
  7 cords to burn, a lidded metal can away from the house is thirty dollars against
  a classic cause of house fires.
- **46 → 100.** Fixing the mower wheel without a sale task is how a mended
  implement sits in a barn for two years.
- **101, ask the shop for a spare chain.** A dull chain causes both kickback and
  the late-session fatigue that makes people careless; a second sharp chain turns
  "afternoon over" into a two-minute swap.

### What the method caught

Task-by-task questioning, rather than rules, found: three rows for one door; a task
whose purpose ("rodents") inverted the correct method; a septic investigation that
was unnecessary; a MassSave measure that is probably still free and available; a
rebate decision in tension with a brand-new propane tank and 7 cords of wood; a
fridge task stalled on a technique that was never needed; and five imported titles
("Repair", "Fill cracks", "All trees", "Gun safe", "Fire box") too thin to act on.
None of it was visible from the data.

The general lesson: **imported titles record what, never why, and why determines
method.** "Fill cracks" meant masonry conservation, floor prep, or pest exclusion
depending on an answer that existed only in Andre's head.

---

## H. NEXT — task gates, provisionally migration 010

Decided 2026-10-05, sequenced after triage, and triage is now done.

**Numbering (2026-11-13, task 218):** this spec was called "migration 004" until that
number was taken. So were 005–008: 004 shopping, 005 overdue-and-defer, 006
deterministic order, 007 hard-deadline guard, 008 shopping staples, and (Andre,
2026-11-13) 009 the context store in Postgres. **010 is where it lands if nothing else
does first.** Confirm against `pa.schema_migration` before
writing the file. Never edit an applied migration.

```sql
create table pa.task_gate (
  task_id    bigint references pa.task(id) on delete cascade,
  kind       text not null check (kind in ('budget','person_present','pet_absent')),
  detail     text,
  cleared_at timestamptz
);
alter table pa.task add column estimated_cost numeric;
```

Gated tasks excluded from `pa.suggest_tasks` until the gate clears. Plus
`pa.v_budget_queue` and a `pa_budget_queue` tool, ordered by importance then cost,
so when money appears the answer to "what should I spend it on" is already ranked.

**Narrowed from the original six kinds to three, on the triage evidence.** The
proposed `season`, `weather` and `permit` kinds turned out to be unnecessary:
seasonal and weather constraints are already handled well by `defer_until` combined
with the `@warm` / `@dry` / `@daylight` contexts, which is how 53, 58, 6, 24 and 52
ended up correctly parked. Adding gate kinds that duplicate existing mechanisms
would give two ways to express the same thing — the same mistake as the two
competing "context" concepts in section E. Keep the constraint narrow and widen it
when a real case appears.

**Tasks to migrate into `task_gate` on 004:**

| Task | Kind | Detail |
|---|---|---|
| 38 gun safe | budget | No budget as of Sept 2026 |
| 82 arborist assessment | budget | Also depends on 81 |
| 83 Seven Fires iron spec | budget | Produces the number |
| 33 generator | budget | Gated on budgeting |
| 34 generator conduit | budget | Gated on budgeting and on 33 |
| 92 heat pump | budget | Employment situation; not 2026 |
| 63 snuff boxes | person_present | Needs Paolo at Brook House |
| 9 ARC room correction | pet_absent | Needs Rosco out of the house |

Why a table rather than columns: a task can be gated more than one way (82 needs
budget *and* 81 first); `cleared_at` records when each gate lifted, which later
answers "how long does budget-gated work actually wait".

**Also fold into the same brief:** the `kind` fix for Vehicles
(`update pa.project set kind = 'program' where id = 26;`) and as many of the
section J tool gaps as are cheap, since several touch the same files.

## I. Brief-writing lessons

Seven of Claude's assumptions have been corrected by reports from the machine.

- `createdb pa_scratch` fails — the Postgres role is `musicbrainz`, not the OS
  user. A fresh scratch database also needs `scripts/migrate.py` before the smoke
  test. Derive from `.env`.
- `env -u PA_DATABASE_URL` does **not** produce an unset variable, because
  `load_dotenv()` defaults to `override=False`. Testing "unset" requires stubbing
  `load_dotenv`.
- The venv is at `/home/dremon/tools/personal-context-mcp/.venv-new/`, **not**
  under `futile-pa/`. Two briefs got this wrong.
- Always ask for "observed vs inferred" as separate lists.

## J. Tool-surface gaps found by using the tools

All found during triage and the first fortnight of real use.

- [ ] **`pa_task_update` has no `depends_on`.** `pa_task_add` does. So a dependency
      found during triage can only be prose. Hit repeatedly. Workaround used:
      `status='waiting'` plus `waiting_on_note` (task 69), honest but unenforced.
- [ ] **`pa_task_update` has no `parent_task_id`.** Collapsing existing tasks under
      a parent means creating fresh subtasks and dropping the originals — done for
      the Jeep rack (32), the tree survey (2), the bathroom (68) and the barn (12).
      It works but loses the original `source_id` linkage.
- [ ] **`pa_task_update` replaces `notes`** while `pa_task_complete`'s `note`
      appends. The asymmetry is a trap: an update meaning to add a line silently
      discards the imported note. Wants an `append_notes` flag.
- [ ] **`pa_task_defer` leaves `status` untouched.** Deferring an `inbox` task
      leaves it `inbox`, so when the defer lifts it is still untriaged.
- [ ] **`pa_project_update` has no `kind`.** See the SQL in H.
- [ ] **`waiting_on` only accepts a `pa.person`.** `pa_task_add` rejected
      `waiting_on: "shop"` for the chainsaw. Tradespeople and vendors are the
      things tasks most often wait on — see C.
- [ ] **A context no situation provides silently excludes a task from every
      suggestion.** Correct behaviour and a useful trick, but undocumented and
      indistinguishable from a tagging mistake. Worth a warning in
      `pa_task_update`'s description or a check in `pa_vocabulary`.
- [ ] **No way to record a sequence within a parent.** The bathroom and barn
      subtasks are ordered by `depends_on` chains, which works but means each step
      is invisible until its predecessor completes. Right for a four-stage job,
      wrong for a checklist. Consider a lightweight `sort_order` on subtasks.
- [ ] **Recurrence notes are static.** Recurrences 6 and 7 both ask Andre to record
      a number each time they run (engine hours; cords burned, supplier, price).
      There is nowhere structured to put it, so it would have to go in the
      completed occurrence's notes and be hunted for later. A small
      `recurrence_log` table, or a convention, would make the "turn judgement into
      arithmetic" idea actually work.

## Stream reorganisation, 2026-10-23 (moved from claude/working-agreement.md)

Created `Personal Assistant Operations` (id 37), kind **area**, under Learning & AI,
importance 4, review cadence 7 days. An area because it never completes, and a review
cadence because an ongoing responsibility without one is the thing that rots unnoticed
— which is the whole lesson of task 156.

Moved into Operations: **156** (backup failure alert), **162** (SQL data cleanup,
8 items), **169** (context retagging).

Moved into Build from Household Admin: **123** (narrow the B2 key), **124** (nothing
notices a backup that never runs), **125** (three alert refinements).

Left in Household Admin deliberately: **77** (enroll the backup YubiKey) — Andre's
call. It is his identity, not the application's.

Already in Build: **114** (PiTower case code into git), **161** (the recurrence engine
has no nightly timer), **177** (suggestions offer parent tasks alongside their children).
