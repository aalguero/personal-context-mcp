# Backlog dashboard — a standing feature

Andre, 2026-10-21: *"I want to see a graphical overview of my current backlog.
That should be a regular feature of our conversations."*

So this is not a one-off artifact. It is a recurring deliverable, and this doc
exists so a future session with no memory of this one can regenerate it
correctly rather than reinventing it.

**The artifact:** `Backlog Overview` — https://claude.ai/artifact/VcsKJ3WRJkQ4qZ85fCYiSf
Private to Andre. **Republish to the same URL** (pass `url` when publishing from a
new conversation); do not create a second one, or he ends up with a gallery of
stale backlogs and no way to tell which is current.

Source file in the session workspace: `backlog-overview.html`. If that file is gone
(a new session), read the artifact first and rebuild from what comes back.

---

## What the page contains, and why each panel is there

| Panel | Answers | Why it earns its place |
|---|---|---|
| Stat tiles | How big is this, and what is on fire | Overdue tile carries the status colour; everything else neutral |
| **Cord tracker** | How far through the wood is he | Seven cells, filled as cords complete. Added 2026-10-22 at his request |
| **Committed hours by week** | When does the pressure actually land | The hero. Only dated tasks, bucketed Mon–Sun, ▲ on hard deadlines |
| Dated-task table | The table view the chart owes you | Scrollable, overdue rows in critical colour, wood rows tinted |
| Areas | Where the backlog sits | Count + hours, single blue series, direct labels |
| Importance | How much of it matters | The 5/4/3/2 diamond |
| **Contexts** | What can he actually start right now | The most actionable chart: thin contexts are situations the engine cannot answer |
| Findings | What the picture says that a list does not | 3–5 items, each with a status icon. Never more than five |

## The queries that feed it

All against `pa.v_task`, statuses `next`, `in_progress`, `waiting`, `someday`.

1. **Totals** — counts, overdue/due-today/hard/undated/blocked, importance bands,
   `count(*) FILTER (WHERE open_subtasks > 0)` for containers, and
   `sum(effort_minutes) FILTER (WHERE open_subtasks = 0)`.
2. **Per-area rollup** — `split_part(project_path,' / ',1)` as the area.
3. **Dated tasks** — `WHERE due_on IS NOT NULL ORDER BY due_on, id`, carrying
   `open_subtasks` so containers can be shown as deadlines without their hours.
4. **Contexts** — `unnest(contexts)` and group; a task carries several, so these
   sum to more than the task count.

### Four traps, all of which bit on the first builds

- **`current_date` in the database is Andre's local date (US Eastern); the container
  clock is UTC and rolls over first.** Late evening Eastern, the container says
  tomorrow. Date the page from the database.
- **Effort double-counts containers.** Sum effort over `open_subtasks = 0` only.
  There are now six containers (the garlic parent and the wood parent among them).
- **`effort_minutes` was serving two masters, and this is the important one.**
  Task 74's 240 minutes was *deliberate* — a session size, set so the suggestion
  engine would offer the job whenever a half-day appeared. The dashboard read it as
  a job size and reported seven cords as four hours. **Neither reading was wrong;
  the field was.** Resolved 2026-10-22 by splitting into per-cord subtasks: the
  cords are offerable units at 360 min each, the parent carries the honest 2160.
  *Before calling any field an error, read the note on the task.* I did not, and
  said so on the page.
- **Suggestions offer containers alongside their own children.** Neither
  `v_actionable` nor `suggest_tasks` filters `open_subtasks > 0`. Logged as task 177.

## Design constraints (the `dataviz` skill, already applied)

- **One categorical series**, blue `#2a78d6` light / `#3987e5` dark — validated, all
  checks pass in both modes. Single series needs no legend; every bar directly labelled.
- **Status colours fixed and never themed**: critical `#d03b3b`, warning `#fab219`,
  serious `#ec835a`, good `#0ca30c`. On the light surface warning and serious fall
  below 3:1 **by design**, so each always ships with an icon *and* a word. Do not feed
  status colours to the palette validator as categorical slots; it fails them,
  correctly, for being out of the lightness band.
- Bars must be geometrically proportional to the gridlines. Each column has a
  `.colplot` that exactly matches the gridline box, with the bar absolutely
  positioned inside it. (The first build sized bars against the whole column box,
  labels and axis included, so every bar was ~14% off its own scale.)
- Phone width: second-line x-axis labels and cell captions are wrapped in
  `<span class="sx">`, hidden below 560px where they collide. The annotations
  survive in the prose note under each chart.
- Render and look at it before shipping. Headless Chromium in the container
  (`/opt/npm-tools/node_modules/playwright`, browsers at `/opt/pw-browsers`) renders a
  local copy wrapped in a doctype; check `scrollWidth === clientWidth` at 400px and
  1100px and eyeball the result. The artifact URL itself needs browser-pane site
  approval, not worth interrupting him for.

## The wood split — the pattern to reuse

Andre, 2026-10-22: *"lets split progress by cord to track it better."*

Task 74 became a container; tasks 170–175 are cords 2 through 7. Cord 1 was done
6 October, so progress reads 1 of 7 rather than 0 of 6 — the denominator is the job,
not the remainder.

- **Six tasks, not one, because a single task is only ever 0% or 100%.** Three
  weekends of real work showing as no progress is how a big job becomes an avoided one.
- **Pace lines, not deadlines.** One cord per Sunday (25 Oct, 1/8/15/22/29 Nov), all
  `hard_deadline: false`. The single hard date stays on the parent, 30 November.
  A soft date going red reads as "a cord behind"; six hard dates would devalue the
  flag, which `pa_task_add`'s own description warns about.
- **`scheduled_for` was considered and rejected.** `v_today` matches it with
  `scheduled_for = CURRENT_DATE` — an exact match, so a missed intention silently
  vanishes the next day. `due_on` persists into `v_overdue`, which is what makes
  slippage visible. Verified against the view definition rather than assumed.
- **Importance 4 per cord, 5 on the parent.** Six new 5s would have grown the
  critical band from 11 to 17 and made the scale mean less.
- **`@daylight` added to every cord**, which the parent lacked. Six hours of saw work
  is daylight-gated and mid-November sunset is near 4:30pm.
- **A pre-req was inferred and made a task** (176: bar oil, two-stroke mix, PPE —
  check first, buy if short, timed to Saturday's dump run with Gary). This is the
  shape Andre asked for explicitly on the straw: *"Need you to be vigilant and infer
  potential pre-reqs like this."* A pre-req that lives only in a note is not tracked.

## Snapshot 2026-10-22 (so drift is visible next time)

119 open · 189 h leaf effort · 6 overdue (2.75 h total) · 6 hard deadlines ·
9 blocked · 6 containers · 79 undated (66%).
Areas: Brook House 58 (141 h), Career 21, Admin 11, Finances 8, Food 8,
Learning & AI 5, Family 4, Outdoors 3, Music 1.
Importance: 5→11, 4→40, 3→45, 2→23.
Contexts: computer 43, handtools 42 (104 h), phone 25, home 23, dry 22 (88 h),
barn 18, focus 17, field 16, daylight 15 (70 h), online 12, errand 6, warm 4,
town 3, lowenergy 2; `@kids`, `@car`, `@away`, `@waiting_slot` still empty.
Weekly dated hours: overdue 2.8 · to 25 Oct 9.8 · 26 Oct 17.0 · 2 Nov 12.5 ·
9 Nov 11.3 · 16 Nov 6.0 · 23 Nov 6.0 · 30 Nov 3.8 · later 6.0.

*Previous snapshot 2026-10-21: 111 open, 156 h, 3 overdue, 78 undated.*

## Still owed

**Task 156, "Backup is failing — investigate"**, importance 5, written automatically
at 03:22 on 21 October with `metadata->>'backup_alert' = 'failure'`, was still
untouched a day and a half later. The alarm fired exactly as designed and nothing
escalates it — it took an ad-hoc backlog query to notice, twice.

That is the project's defining pattern again: a mechanism firing correctly into a
channel nobody reads. **The dashboard is not the fix.** The daily brief must lead
with importance-5 and alert-flagged tasks. That work is still not scheduled.
