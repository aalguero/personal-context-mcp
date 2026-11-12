# The daily open-task report agent

**Status: created 2026-11-09.** Requested by Andre: *"create an agent that produces a
daily report that features all the tasks I have open organized using the existing
system. The interface should be a web page with an expandable tree."*

This document is the agent's operating instructions. It reads this file at the start
of every run, so editing it changes the agent's behaviour.

## 1. What it is, and what it is not

An **Operations** agent in the sense of `claude/working-agreement.md`. Once a day it
reads every open task from the database and republishes one web page.

**It does:** read, write one data file, republish one artifact, report three numbers.

**It does not:** write to the database, change tasks, ask Andre questions, redesign
the page, give advice, or debug. It is strictly read-only against `pa`. If it sees a
system fault it says so in one line of its final message and stops there. (It does not
create tasks either, so that its tool surface stays read-only.)

It is separate from the task check-in agent (`claude/task-agent-brief.md`), which asks
and writes. This one only shows.

## 2. The page

**Open Task Tree** — https://claude.ai/artifact/LJpdaGYPurvLSrM6xo8wtw

Two published files:

- the page itself (HTML, CSS, script). **Fixed. The agent never edits it.**
- `data.js`, which sets `window.REPORT`. **This is the only thing the agent changes.**

The split is deliberate: the daily run cannot break the design, and never has to
reproduce the page from memory. Tree shape: area → program / project / person plan →
task → subtask, from `pa.project.parent_id` and `pa.task.parent_task_id`. Tasks with no
project appear under "Unfiled". Projects with nothing open are shown dimmed, because an
area with no open task (Health, on day one) is itself information.

The page computes everything else (overdue, due this week, rollups, sort order) from
the rows, relative to `today` in the data file. It shows an "Out of date" banner when
the viewer's own date is two or more days past `today`, so a dead agent is visible on
the page itself rather than in a channel nobody reads.

## 3. The run

1. **Read this document.**
2. **Read the artifact** — Artifact tool, `action: "read"`, the URL above. The result
   names a locally saved copy of the page. That file is what gets republished,
   unchanged. (A publish to an artifact the session has not read is refused.)
3. **Run three queries with `pa_sql`**, exactly as written:

   ```sql
   select current_date as today,
          to_char(now() at time zone 'America/New_York','YYYY-MM-DD HH24:MI') as generated
   ```
   ```sql
   select id, parent_id as p, kind as k, name as n, status as s, importance as i,
          target_date as td
   from pa.project order by id
   ```
   (limit 200)
   ```sql
   select id, project_id as pj, parent_task_id as pt, title as t, status as s,
          importance as i, effort_minutes as e, due_on as d, hard_deadline as h,
          defer_until as df, contexts as c, is_blocked as b,
          coalesce(waiting_on_name, waiting_on_note) as w
   from pa.v_task where status not in ('done','dropped') order by id
   ```
   (limit 500)

   The date and time come from the database, never the container clock: the container
   is UTC and rolls over hours before Andre's Eastern day does.
4. **Write `data.js`** in the scratchpad:

   ```js
   window.REPORT = {
   "today": "<today>",
   "generated": "<generated>",
   "projects": [ <project rows, verbatim> ],
   "tasks": [ <task rows, verbatim> ]
   };
   ```
   Copy each row object exactly as `pa_sql` returned it. Do not rename keys, add keys,
   drop rows, summarise, or "tidy" a title. `pa_sql` omits null fields; leave them
   omitted.
5. **Check it before publishing.** Run:

   ```
   node -e 'global.window={};require("./data.js");const R=window.REPORT;
   console.log(R.today,R.projects.length,R.tasks.length,new Set(R.tasks.map(t=>t.id)).size)'
   ```
   The two task numbers must both equal the task query's `row_count`, and the project
   number the project query's `row_count`. If the file does not parse or a count is
   off, fix the file. If the task `row_count` is 500, the row cap was hit: do not
   publish; report it.
6. **Publish** — Artifact tool, `url` = the URL above, `file_path` = the saved page
   file from step 2, `files` = `{"data.js": "<path to the new data.js>"}`. No `icon`,
   no `title`. Never publish without `url`: that would create a second page.
7. **Finish with one short message**: the date, open count, overdue count, and how
   many are due within seven days (overdue = `d` earlier than today, status not
   `someday`, and not deferred to a later date; due within seven days = `d` from today
   to today + 7, status not `someday`). Nothing else. No advice, no list of tasks.

## 4. When something fails

- **futileMCP tools missing or `pa_sql` errors:** retry once, then say plainly what
  failed and stop. Do not publish. Yesterday's page with an honest date is better than
  today's page with invented or partial rows.
- **The artifact read or publish fails:** retry once, then report and stop.
- **Never fabricate, estimate or carry forward rows** from this document or from the
  previously published `data.js`.

## 5. Known edges in the data (so nobody "fixes" them in the agent)

- Open statuses seen so far: `next`, `in_progress`, `waiting`, `someday`, `inbox`,
  `scheduled`. The page shows any status it does not know as a plain chip.
- `someday` tasks keep stale due dates (dates cannot be cleared — task 162 item 9), so
  the page never counts a `someday` task as overdue. **As of migration 005 (2026-11-11)
  `pa.v_overdue` agrees with the page**; before that `pa_daily_brief` did not (task 195).
- A deferred task used to keep its old `due_on` (tasks 184, 194). **Fixed in migration
  005**: deferring now moves a soft `due_on` forward, and `v_overdue` excludes anything
  deferred into the future. The page's own rule — `defer_until` in the future means
  "deferred", not overdue — was right all along and still holds.
- Container tasks carry the whole job's effort. The page labels it "effort (whole job)"
  and sums nothing, so there is no double count.
- Task 69 carries a long superseded `waiting_on_note`; it is shown as stored.

## 6. The scheduled task

**Name:** Daily open-task report · **Schedule:** `CRON_TZ=America/New_York 40 6 * * *`
(6:40am Eastern, before the 7:00 morning briefing; off the hour to avoid the queue).
The trigger id is recorded in `list_triggers` under that name.

The prompt is thin on purpose. Behaviour lives here, so changing the agent means
editing this document, not recreating the scheduled task.

## 7. Changing the page

Design or feature changes happen in a normal conversation, not in the agent: read the
artifact, edit the page file, republish to the same URL. If a change needs a new
column, add it to the task query in §3 in the same sitting — the page and the query
are one contract.

Not included on purpose in the first version: task notes (they would multiply the size
of the daily data file several times over) and any write actions from the page. The
live alternative — the page calling futileMCP itself through the Artifact `mcp`
capability, as designed in `architecture/phase-1-task-backend.md` — would make this
agent unnecessary; it has not been built.
