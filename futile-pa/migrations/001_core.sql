-- ============================================================================
--  futile-pa : personal assistant core schema
--  Migration 001 — core entities, triggers, views, query functions
--
--  Target: PostgreSQL 13+
--  Idempotent: safe to re-run (create if not exists / or replace throughout).
--  Transactional: wrap in BEGIN/COMMIT via scripts/migrate.py.
--
--  DESIGN NOTES (why things are the way they are)
--
--  1. Dedicated schema `pa`, not the public schema, so this can live in an
--     existing database without colliding with anything already there.
--
--  2. Status/kind columns are text + CHECK constraint rather than real ENUM
--     types. A personal ontology churns: you will want to add 'someday' or
--     rename 'waiting' six months from now. Altering a CHECK constraint is one
--     statement; native enum values cannot be removed or reordered at all.
--
--  3. bigint identity keys, not UUIDs. You and Claude both refer to these in
--     conversation ("close task 142"). Short integers are legible; UUIDs are
--     not, and there is no distributed-write problem here to justify them.
--
--  4. Hierarchy lives in one self-referencing table (pa.project) with a `kind`
--     discriminator, rather than separate program/project/area tables. The
--     structural queries you actually want — "everything under the garden
--     program", "which active thing has no next action" — are the same query
--     regardless of level, and a single recursive CTE serves them all.
--
--  5. Append-only pa.event log, written by trigger. This is what lets the
--     assistant reason about behaviour over time rather than just state:
--     "you have deferred this five times", "nothing has moved on the barn in
--     three weeks". State tables answer what is true; the log answers what has
--     been happening, which is usually the more useful question.
-- ============================================================================

create schema if not exists pa;

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- Generic updated_at maintenance.
create or replace function pa.tg_set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- Energy as an ordered scale so "what can I do with low energy" is a
-- comparison rather than a set membership test.
create or replace function pa.energy_rank(p text) returns int
language sql immutable as $$
  select case p when 'low' then 1 when 'medium' then 2 when 'high' then 3 else 2 end;
$$;

-- ---------------------------------------------------------------------------
-- People
--   Family members, professionals, friends. Two jobs: (a) tasks and projects
--   point at them, (b) contact cadence — the spine of "maintaining
--   correspondence and connection".
-- ---------------------------------------------------------------------------
create table if not exists pa.person (
  id                   bigint generated always as identity primary key,
  name                 text not null,
  slug                 text unique,
  relationship         text,          -- son | daughter | sister | father | ex_wife | neighbor | friend | professional | other
  household            text,          -- free text: "N. Kingstown RI", "Cambridge MA"
  location             text,
  email                text,
  phone                text,
  birthday             date,
  -- Target rhythm of contact. NULL = no cadence expectation (most people).
  contact_cadence_days int check (contact_cadence_days is null or contact_cadence_days > 0),
  last_contact_on      date,
  -- Someone whose life gets its own planning container (each family member as
  -- a sub-project, per the project brief).
  has_plan             boolean not null default false,
  notes                text,
  metadata             jsonb not null default '{}'::jsonb,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Projects / programs / areas
--
--   kind semantics — this distinction is load-bearing:
--     area        never completes. An ongoing standard to uphold.
--                 ("House & Land", "Health", "Finances", "Career")
--                 Areas are reviewed, not finished. They should never appear
--                 in "what's overdue".
--     program     multi-year, multi-project, but does eventually end.
--                 ("Native Plant Garden Conversion", "Farmhouse Renovation")
--     project     has a definable done state, weeks to months.
--     person_plan container for one person's sub-project.
--
--   horizon — planning distance, defined so queries mean something:
--     now    : active this week
--     short  : within 3 months
--     medium : 3–12 months
--     long   : beyond 12 months
-- ---------------------------------------------------------------------------
create table if not exists pa.project (
  id                  bigint generated always as identity primary key,
  parent_id           bigint references pa.project(id) on delete set null,
  kind                text not null default 'project'
                        check (kind in ('area','program','project','person_plan')),
  name                text not null,
  slug                text unique,
  description         text,
  status              text not null default 'active'
                        check (status in ('planning','active','paused','blocked','someday','done','abandoned')),
  horizon             text check (horizon is null or horizon in ('now','short','medium','long')),
  importance          smallint not null default 3 check (importance between 1 and 5),
  person_id           bigint references pa.person(id) on delete set null,
  target_date         date,
  started_on          date,
  completed_on        date,
  -- Periodic review. Areas in particular rot silently without this.
  review_cadence_days int check (review_cadence_days is null or review_cadence_days > 0),
  last_reviewed_on    date,
  notes               text,
  metadata            jsonb not null default '{}'::jsonb,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  -- An area has no end, so it must not carry completion semantics.
  constraint project_area_never_completes
    check (kind <> 'area' or (completed_on is null and status not in ('done','abandoned')))
);

create index if not exists project_parent_idx on pa.project(parent_id);
create index if not exists project_status_idx on pa.project(status);
create index if not exists project_person_idx on pa.project(person_id);

-- ---------------------------------------------------------------------------
-- Goals
--   Outcomes, distinct from the projects that deliver them. A goal can be
--   served by several projects; a project can serve several goals.
-- ---------------------------------------------------------------------------
create table if not exists pa.goal (
  id            bigint generated always as identity primary key,
  name          text not null,
  description   text,
  person_id     bigint references pa.person(id) on delete set null,  -- whose goal (NULL = yours)
  horizon       text check (horizon is null or horizon in ('short','medium','long')),
  target_date   date,
  measure       text,        -- how you will know: "mortgage principal = 0"
  baseline      text,
  current_value text,
  target_value  text,
  status        text not null default 'active'
                  check (status in ('active','paused','achieved','abandoned')),
  importance    smallint not null default 3 check (importance between 1 and 5),
  notes         text,
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists pa.goal_project (
  goal_id      bigint not null references pa.goal(id) on delete cascade,
  project_id   bigint not null references pa.project(id) on delete cascade,
  contribution text,
  primary key (goal_id, project_id)
);

-- ---------------------------------------------------------------------------
-- Contexts
--   The conditions a task requires. Classic GTD contexts, widened: place,
--   tool, bodily state, weather.
-- ---------------------------------------------------------------------------
create table if not exists pa.context (
  id          bigint generated always as identity primary key,
  name        text not null unique,          -- slug: 'phone', 'barn', 'computer'
  label       text not null,
  kind        text not null default 'other'
                check (kind in ('location','tool','state','people','weather','time','other')),
  description text,
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Situations
--   Named recurring circumstances, with the contexts they make available and
--   the time and energy they come with. This is the piece that answers
--   "I'm plugged in at the charger for 40 minutes, what should I do" —
--   the situation supplies contexts + minutes + energy, and the suggestion
--   query intersects that against the task list.
-- ---------------------------------------------------------------------------
create table if not exists pa.situation (
  id               bigint generated always as identity primary key,
  name             text not null unique,
  label            text not null,
  description      text,
  typical_minutes  int,
  min_minutes      int,
  max_minutes      int,
  energy_available text check (energy_available is null or energy_available in ('low','medium','high')),
  notes            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create table if not exists pa.situation_context (
  situation_id bigint not null references pa.situation(id) on delete cascade,
  context_id   bigint not null references pa.context(id) on delete cascade,
  primary key (situation_id, context_id)
);

-- ---------------------------------------------------------------------------
-- Recurrence series
--
--   mode is the thing Google Tasks gets wrong:
--     fixed           — occurrences fall on calendar dates regardless of when
--                       you did the last one. Bills, quarterly taxes, trash day.
--                       Missing one does not shift the next.
--     after_completion— the next occurrence is interval_days after you actually
--                       finished the last one. Furnace filter, oil change,
--                       gutter clearing. Doing it late legitimately shifts the
--                       whole future series, and pretending otherwise generates
--                       a pile of false overdue items.
--
--   RRULE expansion happens in Python (dateutil), not SQL. Reimplementing
--   RFC 5545 in plpgsql would be a bad trade.
-- ---------------------------------------------------------------------------
create table if not exists pa.recurrence (
  id                bigint generated always as identity primary key,
  title             text not null,
  notes             text,
  project_id        bigint references pa.project(id) on delete set null,
  person_id         bigint references pa.person(id) on delete set null,
  mode              text not null default 'fixed' check (mode in ('fixed','after_completion')),
  rrule             text,                -- RFC 5545 RRULE body, e.g. 'FREQ=MONTHLY;BYMONTHDAY=1'
  interval_days     int check (interval_days is null or interval_days > 0),
  dtstart           date not null default current_date,
  until_date        date,
  -- Materialise the task this many days before it is due, so it can be
  -- planned rather than appearing on the morning it is due.
  lead_time_days    int not null default 0 check (lead_time_days >= 0),
  effort_minutes    int,
  energy            text check (energy is null or energy in ('low','medium','high')),
  importance        smallint not null default 3 check (importance between 1 and 5),
  hard_deadline     boolean not null default false,
  active            boolean not null default true,
  last_spawned_for  date,     -- the occurrence date most recently materialised
  last_completed_on date,
  metadata          jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint recurrence_needs_a_rule
    check ((mode = 'fixed' and rrule is not null)
        or (mode = 'after_completion' and interval_days is not null))
);

create table if not exists pa.recurrence_context (
  recurrence_id bigint not null references pa.recurrence(id) on delete cascade,
  context_id    bigint not null references pa.context(id) on delete cascade,
  primary key (recurrence_id, context_id)
);

-- ---------------------------------------------------------------------------
-- Tasks
--
--   Four different date columns, because they answer four different questions
--   and collapsing them is the single most common reason task systems stop
--   being trusted:
--     due_on        the deadline day
--     due_at        deadline with a time of day, when that matters
--     defer_until   do not show me this before this date (it is not yet possible
--                   or not yet worth thinking about)
--     scheduled_for the day you *intend* to do it — an intention, not a deadline
--
--   hard_deadline separates a real constraint (tax filing, nursery pickup
--   window) from a wish (would like to prune by March). Without this flag every
--   date drifts into the same shade of pink and overdue stops meaning anything.
-- ---------------------------------------------------------------------------
create table if not exists pa.task (
  id                   bigint generated always as identity primary key,
  project_id           bigint references pa.project(id) on delete set null,
  parent_task_id       bigint references pa.task(id) on delete cascade,
  person_id            bigint references pa.person(id) on delete set null,
  title                text not null,
  notes                text,
  status               text not null default 'inbox'
                         check (status in ('inbox','next','scheduled','in_progress','waiting','someday','done','dropped')),
  importance           smallint not null default 3 check (importance between 1 and 5),
  effort_minutes       int check (effort_minutes is null or effort_minutes > 0),
  energy               text check (energy is null or energy in ('low','medium','high')),
  due_on               date,
  due_at               timestamptz,
  hard_deadline        boolean not null default false,
  defer_until          date,
  scheduled_for        date,
  waiting_on_person_id bigint references pa.person(id) on delete set null,
  waiting_on_note      text,
  waiting_since        date,
  recurrence_id        bigint references pa.recurrence(id) on delete set null,
  occurrence_date      date,       -- which occurrence of the series this row is
  completed_at         timestamptz,
  dropped_reason       text,
  -- Maintained by trigger: how many times this has been pushed into the future.
  -- High values mean either "this matters and keeps losing" or "admit it is
  -- never happening". Both are worth surfacing.
  defer_count          int not null default 0,
  source               text not null default 'manual'
                         check (source in ('manual','claude','google_tasks','gmail','calendar','recurrence','import')),
  source_id            text,
  external_url         text,
  metadata             jsonb not null default '{}'::jsonb,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  constraint task_no_self_parent check (parent_task_id is distinct from id)
);

-- Idempotent imports: re-running the Google Tasks importer must not duplicate.
create unique index if not exists task_source_uniq
  on pa.task(source, source_id) where source_id is not null;

-- One row per occurrence of a series. Deliberately NOT a partial index: the
-- scheduler relies on ON CONFLICT against it, and conflict inference cannot
-- match a partial index unless the statement repeats the predicate. Since
-- Postgres treats NULLs as distinct by default, ordinary non-recurring tasks
-- (both columns NULL) are unconstrained either way.
create unique index if not exists task_occurrence_uniq
  on pa.task(recurrence_id, occurrence_date);

create index if not exists task_status_idx      on pa.task(status);
create index if not exists task_project_idx     on pa.task(project_id);
create index if not exists task_due_idx         on pa.task(due_on) where status not in ('done','dropped');
create index if not exists task_scheduled_idx   on pa.task(scheduled_for) where status not in ('done','dropped');
create index if not exists task_open_idx        on pa.task(id) where status not in ('done','dropped');
create index if not exists task_parent_idx      on pa.task(parent_task_id);
create index if not exists task_person_idx      on pa.task(person_id);

create table if not exists pa.task_context (
  task_id    bigint not null references pa.task(id) on delete cascade,
  context_id bigint not null references pa.context(id) on delete cascade,
  primary key (task_id, context_id)
);
create index if not exists task_context_ctx_idx on pa.task_context(context_id);

-- Dependencies. Needed for the multi-year programs, where sequencing is the
-- whole problem: you cannot plant before the beds are sheet-mulched.
create table if not exists pa.task_dependency (
  task_id            bigint not null references pa.task(id) on delete cascade,
  depends_on_task_id bigint not null references pa.task(id) on delete cascade,
  primary key (task_id, depends_on_task_id),
  constraint dependency_not_self check (task_id <> depends_on_task_id)
);
create index if not exists task_dependency_rev_idx on pa.task_dependency(depends_on_task_id);

-- ---------------------------------------------------------------------------
-- Event log (append-only)
-- ---------------------------------------------------------------------------
create table if not exists pa.event (
  id          bigint generated always as identity primary key,
  at          timestamptz not null default now(),
  actor       text not null default 'system',   -- 'andre' | 'claude' | 'system'
  entity_type text not null,                    -- task | project | goal | person | recurrence
  entity_id   bigint,
  action      text not null,                    -- created | status_changed | deferred | edited | completed | dropped | contacted | reviewed | note
  from_value  text,
  to_value    text,
  note        text,
  payload     jsonb not null default '{}'::jsonb
);

create index if not exists event_entity_idx on pa.event(entity_type, entity_id, at desc);
create index if not exists event_at_idx     on pa.event(at desc);
create index if not exists event_action_idx on pa.event(action, at desc);

-- Set by the application (SET LOCAL pa.actor = 'andre') so log rows record who
-- did it. Falls back to 'system'.
create or replace function pa.current_actor() returns text
language plpgsql stable as $$
declare a text;
begin
  a := current_setting('pa.actor', true);
  if a is null or a = '' then
    return 'system';
  end if;
  return a;
end;
$$;

-- ---------------------------------------------------------------------------
-- Brief runs — one row per generated daily brief, so successive days can be
-- diffed and the assistant can avoid repeating itself.
-- ---------------------------------------------------------------------------
create table if not exists pa.brief_run (
  id            bigint generated always as identity primary key,
  for_date      date not null,
  ran_at        timestamptz not null default now(),
  delivered_via text,
  payload       jsonb not null default '{}'::jsonb
);
create index if not exists brief_run_date_idx on pa.brief_run(for_date desc);

-- ============================================================================
-- Triggers
-- ============================================================================

drop trigger if exists person_updated_at on pa.person;
create trigger person_updated_at before update on pa.person
  for each row execute function pa.tg_set_updated_at();
drop trigger if exists project_updated_at on pa.project;
create trigger project_updated_at before update on pa.project
  for each row execute function pa.tg_set_updated_at();
drop trigger if exists goal_updated_at on pa.goal;
create trigger goal_updated_at before update on pa.goal
  for each row execute function pa.tg_set_updated_at();
drop trigger if exists situation_updated_at on pa.situation;
create trigger situation_updated_at before update on pa.situation
  for each row execute function pa.tg_set_updated_at();
drop trigger if exists recurrence_updated_at on pa.recurrence;
create trigger recurrence_updated_at before update on pa.recurrence
  for each row execute function pa.tg_set_updated_at();

-- Task: keep completed_at consistent with status, and count deferrals.
create or replace function pa.tg_task_normalise() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();

  -- status and completed_at must agree; the caller only has to set status.
  if new.status = 'done' and new.completed_at is null then
    new.completed_at := now();
  elsif new.status <> 'done' and new.completed_at is not null then
    new.completed_at := null;
  end if;

  if tg_op = 'UPDATE' then
    -- A deferral is any push of defer_until or scheduled_for into the future.
    if (new.defer_until is not null
        and (old.defer_until is null or new.defer_until > old.defer_until))
       or (new.scheduled_for is not null and old.scheduled_for is not null
        and new.scheduled_for > old.scheduled_for) then
      new.defer_count := old.defer_count + 1;
    end if;

    -- Entering 'waiting' stamps the clock automatically.
    if new.status = 'waiting' and old.status <> 'waiting' and new.waiting_since is null then
      new.waiting_since := current_date;
    elsif new.status <> 'waiting' and old.status = 'waiting' then
      new.waiting_since := null;
    end if;
  end if;

  return new;
end;
$$;

drop trigger if exists task_normalise on pa.task;
create trigger task_normalise before insert or update on pa.task
  for each row execute function pa.tg_task_normalise();

-- Task: write the event log.
create or replace function pa.tg_task_log() returns trigger
language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    insert into pa.event(actor, entity_type, entity_id, action, to_value, note, payload)
    values (pa.current_actor(), 'task', new.id, 'created', new.status, new.title,
            jsonb_build_object('project_id', new.project_id, 'source', new.source));
    return new;
  end if;

  if new.status is distinct from old.status then
    insert into pa.event(actor, entity_type, entity_id, action, from_value, to_value, note)
    values (pa.current_actor(), 'task', new.id,
            case new.status when 'done' then 'completed'
                            when 'dropped' then 'dropped'
                            else 'status_changed' end,
            old.status, new.status,
            coalesce(new.dropped_reason, new.title));
  end if;

  if new.defer_count > old.defer_count then
    insert into pa.event(actor, entity_type, entity_id, action, from_value, to_value, note)
    values (pa.current_actor(), 'task', new.id, 'deferred',
            coalesce(old.defer_until::text, old.scheduled_for::text),
            coalesce(new.defer_until::text, new.scheduled_for::text),
            new.title);
  end if;

  -- Substantive edits, excluding the churn columns handled above.
  if (new.title, new.notes, new.due_on, new.importance, new.effort_minutes, new.energy, new.project_id)
     is distinct from
     (old.title, old.notes, old.due_on, old.importance, old.effort_minutes, old.energy, old.project_id) then
    insert into pa.event(actor, entity_type, entity_id, action, note, payload)
    values (pa.current_actor(), 'task', new.id, 'edited', new.title,
            jsonb_strip_nulls(jsonb_build_object(
              'due_on',         case when new.due_on is distinct from old.due_on then new.due_on end,
              'importance',     case when new.importance is distinct from old.importance then new.importance end,
              'project_id',     case when new.project_id is distinct from old.project_id then new.project_id end,
              'effort_minutes', case when new.effort_minutes is distinct from old.effort_minutes then new.effort_minutes end)));
  end if;

  return new;
end;
$$;

drop trigger if exists task_log on pa.task;
create trigger task_log after insert or update on pa.task
  for each row execute function pa.tg_task_log();

-- Dependency cycle guard. A cycle would make the "is it blocked" test
-- non-terminating and, worse, silently hide everything in the loop.
create or replace function pa.tg_dependency_no_cycle() returns trigger
language plpgsql as $$
declare found boolean;
begin
  with recursive chain(id) as (
    select new.task_id
    union
    select d.task_id
      from pa.task_dependency d
      join chain c on c.id = d.depends_on_task_id
  )
  select exists (select 1 from chain where id = new.depends_on_task_id) into found;

  if found then
    raise exception 'dependency cycle: task % cannot depend on task %',
      new.task_id, new.depends_on_task_id;
  end if;
  return new;
end;
$$;

drop trigger if exists dependency_no_cycle on pa.task_dependency;
create trigger dependency_no_cycle before insert or update on pa.task_dependency
  for each row execute function pa.tg_dependency_no_cycle();

-- Project and person logging (lighter touch than tasks).
create or replace function pa.tg_project_log() returns trigger
language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    insert into pa.event(actor, entity_type, entity_id, action, to_value, note)
    values (pa.current_actor(), 'project', new.id, 'created', new.status, new.name);
  elsif new.status is distinct from old.status then
    insert into pa.event(actor, entity_type, entity_id, action, from_value, to_value, note)
    values (pa.current_actor(), 'project', new.id, 'status_changed', old.status, new.status, new.name);
  elsif new.last_reviewed_on is distinct from old.last_reviewed_on then
    insert into pa.event(actor, entity_type, entity_id, action, to_value, note)
    values (pa.current_actor(), 'project', new.id, 'reviewed', new.last_reviewed_on::text, new.name);
  end if;
  return new;
end;
$$;

drop trigger if exists project_log on pa.project;
create trigger project_log after insert or update on pa.project
  for each row execute function pa.tg_project_log();

create or replace function pa.tg_person_log() returns trigger
language plpgsql as $$
begin
  if tg_op = 'UPDATE' and new.last_contact_on is distinct from old.last_contact_on then
    insert into pa.event(actor, entity_type, entity_id, action, from_value, to_value, note)
    values (pa.current_actor(), 'person', new.id, 'contacted',
            old.last_contact_on::text, new.last_contact_on::text, new.name);
  end if;
  return new;
end;
$$;

drop trigger if exists person_log on pa.person;
create trigger person_log after update on pa.person
  for each row execute function pa.tg_person_log();

-- ============================================================================
-- Query surface
-- ============================================================================

-- Human-readable ancestry: "House & Land / Farmhouse Renovation / Barn roof"
create or replace function pa.project_path(p_id bigint) returns text
language sql stable as $$
  with recursive up(id, parent_id, name, depth) as (
    select id, parent_id, name, 0 from pa.project where id = p_id
    union all
    select p.id, p.parent_id, p.name, up.depth + 1
      from pa.project p join up on p.id = up.parent_id
  )
  select string_agg(name, ' / ' order by depth desc) from up;
$$;

-- All descendants of a project, inclusive. Powers "everything under the garden".
create or replace function pa.project_subtree(p_id bigint)
returns table (id bigint, depth int)
language sql stable as $$
  with recursive down(id, depth) as (
    select p.id, 0 from pa.project p where p.id = p_id
    union all
    select c.id, d.depth + 1
      from pa.project c join down d on c.parent_id = d.id
  )
  select id, depth from down;
$$;

-- ---------------------------------------------------------------------------
-- pa.v_task — the enriched base view everything else builds on.
-- ---------------------------------------------------------------------------
create or replace view pa.v_task as
select
  t.*,
  p.name  as project_name,
  p.kind  as project_kind,
  pa.project_path(t.project_id) as project_path,
  per.name as person_name,
  w.name   as waiting_on_name,
  coalesce((
    select array_agg(c.name order by c.name)
      from pa.task_context tc join pa.context c on c.id = tc.context_id
     where tc.task_id = t.id
  ), '{}'::text[]) as contexts,
  exists (
    select 1 from pa.task_dependency d
      join pa.task b on b.id = d.depends_on_task_id
     where d.task_id = t.id and b.status not in ('done','dropped')
  ) as is_blocked,
  (select count(*) from pa.task_dependency d
     join pa.task b on b.id = d.depends_on_task_id
    where d.task_id = t.id and b.status not in ('done','dropped')) as blocker_count,
  (select count(*) from pa.task s
    where s.parent_task_id = t.id and s.status not in ('done','dropped')) as open_subtasks,
  -- Deferred tasks are invisible until their date arrives.
  (t.defer_until is not null and t.defer_until > current_date) as is_deferred,
  case when t.due_on is not null then (t.due_on - current_date) end as days_until_due,
  (current_date - t.created_at::date) as age_days
from pa.task t
left join pa.project p  on p.id = t.project_id
left join pa.person per on per.id = t.person_id
left join pa.person w   on w.id = t.waiting_on_person_id;

-- ---------------------------------------------------------------------------
-- pa.v_actionable — could be worked on right now.
--   Excludes: finished, dropped, someday, waiting on someone else, blocked by
--   an unfinished dependency, and anything deferred into the future.
-- ---------------------------------------------------------------------------
create or replace view pa.v_actionable as
select * from pa.v_task
where status in ('inbox','next','scheduled','in_progress')
  and not is_blocked
  and not is_deferred;

-- ---------------------------------------------------------------------------
-- pa.v_scored — ranking, with the arithmetic exposed rather than hidden.
--
--   score = importance + urgency + neglect + age + quick-win
--
--   importance  10..50   the stated 1–5 rating, times ten. Deliberately the
--                        largest single term: what matters should outrank what
--                        merely has a date attached.
--   urgency      0..90   a step function of days-to-due, multiplied by 1.5 when
--                        hard_deadline. Steps rather than a smooth curve because
--                        "due in 9 days" and "due in 11 days" genuinely feel the
--                        same, while "today" and "tomorrow" do not.
--   neglect      0..15   3 points per deferral, capped. Surfaces the things
--                        that keep losing, so they get either done or dropped.
--   age          0..5    one point per fortnight sitting in the system.
--   quick-win    0..6    ≤10 min: 6, ≤20 min: 3. Small enough to just do.
--
--   Cap on urgency matters: without it a task six months overdue outranks
--   everything forever and the list becomes a monument to one failure.
-- ---------------------------------------------------------------------------
create or replace view pa.v_scored as
select
  v.*,
  (v.importance * 10) as score_importance,
  round(
    case
      when v.due_on is null then 0
      when v.due_on <  current_date then 40 + least(current_date - v.due_on, 20)
      when v.due_on =  current_date then 35
      when v.due_on <= current_date + 3  then 25
      when v.due_on <= current_date + 7  then 15
      when v.due_on <= current_date + 30 then 6
      else 2
    end * case when v.hard_deadline then 1.5 else 1.0 end
  )::int as score_urgency,
  least(v.defer_count * 3, 15) as score_neglect,
  least(v.age_days / 14, 5) as score_age,
  case
    when v.effort_minutes is null then 0
    when v.effort_minutes <= 10 then 6
    when v.effort_minutes <= 20 then 3
    else 0
  end as score_quickwin,
  (
    (v.importance * 10)
    + round(
        case
          when v.due_on is null then 0
          when v.due_on <  current_date then 40 + least(current_date - v.due_on, 20)
          when v.due_on =  current_date then 35
          when v.due_on <= current_date + 3  then 25
          when v.due_on <= current_date + 7  then 15
          when v.due_on <= current_date + 30 then 6
          else 2
        end * case when v.hard_deadline then 1.5 else 1.0 end
      )::int
    + least(v.defer_count * 3, 15)
    + least(v.age_days / 14, 5)
    + case
        when v.effort_minutes is null then 0
        when v.effort_minutes <= 10 then 6
        when v.effort_minutes <= 20 then 3
        else 0
      end
  ) as score
from pa.v_actionable v;

-- Today: due, overdue, or intended for today.
create or replace view pa.v_today as
select * from pa.v_scored
where due_on <= current_date
   or scheduled_for = current_date
   or due_at::date = current_date
order by score desc;

create or replace view pa.v_overdue as
select * from pa.v_task
where status not in ('done','dropped')
  and due_on is not null
  and due_on < current_date
order by due_on;

create or replace view pa.v_waiting as
select * from pa.v_task
where status = 'waiting'
order by waiting_since nulls last;

-- ---------------------------------------------------------------------------
-- pa.v_project_health
--   The most useful row here is next_action_count = 0 on an active project:
--   the classic silent failure, where something you care about has no
--   physically-next step and therefore quietly stops moving.
-- ---------------------------------------------------------------------------
create or replace view pa.v_project_health as
select
  p.id,
  p.name,
  p.kind,
  p.status,
  p.horizon,
  p.importance,
  pa.project_path(p.id) as path,
  p.parent_id,
  p.target_date,
  p.review_cadence_days,
  p.last_reviewed_on,
  (select count(*) from pa.task t where t.project_id = p.id and t.status not in ('done','dropped')) as open_tasks,
  (select count(*) from pa.v_actionable a where a.project_id = p.id) as next_action_count,
  (select count(*) from pa.task t where t.project_id = p.id and t.status not in ('done','dropped')
     and t.due_on is not null and t.due_on < current_date) as overdue_tasks,
  (select count(*) from pa.task t where t.project_id = p.id and t.status = 'done') as done_tasks,
  (select max(e.at) from pa.event e
     where (e.entity_type = 'project' and e.entity_id = p.id)
        or (e.entity_type = 'task' and e.entity_id in (select id from pa.task where project_id = p.id))
  ) as last_activity_at,
  -- NULL rather than a sentinel like 9999 when never reviewed. A sentinel would
  -- be reported verbatim ("overdue by 9999 days"), so the fact is carried by a
  -- separate boolean instead and the number stays honest.
  case
    when p.review_cadence_days is null or p.last_reviewed_on is null then null
    else current_date - (p.last_reviewed_on + p.review_cadence_days)
  end as review_overdue_days,
  (p.review_cadence_days is not null and p.last_reviewed_on is null) as never_reviewed
from pa.project p;

-- Active things with nowhere to start.
create or replace view pa.v_stalled_projects as
select * from pa.v_project_health
where status = 'active'
  and kind <> 'area'
  and next_action_count = 0
order by importance desc, coalesce(last_activity_at, '-infinity'::timestamptz);

create or replace view pa.v_review_due as
select * from pa.v_project_health
where status in ('active','planning')
  and (never_reviewed or review_overdue_days >= 0)
order by never_reviewed desc, review_overdue_days desc nulls last;

-- Connection: who has gone past their intended cadence.
create or replace view pa.v_connection_due as
select
  pe.id, pe.name, pe.relationship, pe.household, pe.contact_cadence_days,
  pe.last_contact_on, pe.phone, pe.email,
  -- NULL, not a sentinel, when there is no recorded contact at all: "we have
  -- never logged a conversation" is a different statement from "it has been
  -- 9999 days", and only one of them is true.
  case when pe.last_contact_on is null then null
       else current_date - (pe.last_contact_on + pe.contact_cadence_days) end as overdue_days,
  (pe.last_contact_on is null) as never_logged
from pa.person pe
where pe.contact_cadence_days is not null
  and (pe.last_contact_on is null
       or pe.last_contact_on + pe.contact_cadence_days <= current_date)
order by never_logged desc, overdue_days desc nulls last;

-- ---------------------------------------------------------------------------
-- pa.suggest_tasks — "I have 40 minutes at the charger, what should I do?"
--
--   A task qualifies when the situation supplies every context it needs
--   (not merely one of them — a task tagged @phone AND @quiet is no good in a
--   noisy waiting room), it fits the time available, and it does not demand
--   more energy than is on offer.
--
--   p_include_untagged: tasks with no context requirements are location-neutral
--   and generally eligible, but ranked behind tagged matches since the tagged
--   ones were specifically earmarked for circumstances like this.
-- ---------------------------------------------------------------------------
create or replace function pa.suggest_tasks(
  p_situation        text default null,
  p_minutes          int  default null,
  p_energy           text default null,
  p_contexts         text[] default null,
  p_include_untagged boolean default true,
  p_limit            int default 10
)
returns table (
  id int8, title text, project_name text, project_path text,
  status text, importance smallint, effort_minutes int, energy text,
  due_on date, contexts text[], score int, match_kind text, why text
)
language sql stable as $$
  with sit as (
    select s.* from pa.situation s where p_situation is not null and s.name = p_situation
  ),
  -- Contexts available: explicit argument wins, else the situation's set.
  avail as (
    select coalesce(
      p_contexts,
      (select array_agg(c.name) from sit
         join pa.situation_context sc on sc.situation_id = sit.id
         join pa.context c on c.id = sc.context_id)
    ) as names
  ),
  budget as (
    select coalesce(p_minutes, (select typical_minutes from sit)) as minutes,
           coalesce(p_energy,  (select energy_available from sit)) as energy
  )
  select
    v.id, v.title, v.project_name, v.project_path,
    v.status, v.importance, v.effort_minutes, v.energy,
    v.due_on, v.contexts, v.score,
    case when cardinality(v.contexts) = 0 then 'untagged' else 'context_match' end as match_kind,
    concat_ws('; ',
      case when v.effort_minutes is not null then v.effort_minutes || ' min' end,
      case when v.energy is not null then v.energy || ' energy' end,
      case when v.due_on is not null then 'due ' || v.due_on end,
      case when cardinality(v.contexts) > 0 then 'fits ' || array_to_string(v.contexts, '+') end
    ) as why
  from pa.v_scored v, budget b, avail a
  where
    -- time: unknown effort is allowed through, since refusing to suggest
    -- anything unestimated would hide most of a real task list
    (b.minutes is null or v.effort_minutes is null or v.effort_minutes <= b.minutes)
    -- energy: never suggest something needing more than is available
    and (b.energy is null or v.energy is null
         or pa.energy_rank(v.energy) <= pa.energy_rank(b.energy))
    -- context: the situation must supply *all* required contexts
    and (
      (cardinality(v.contexts) = 0 and p_include_untagged)
      or (a.names is not null and v.contexts <@ a.names)
      or (a.names is null and p_include_untagged)
    )
  order by
    case when cardinality(v.contexts) > 0 then 0 else 1 end,  -- earmarked first
    v.score desc,
    v.effort_minutes nulls last
  limit coalesce(p_limit, 10);
$$;

-- ---------------------------------------------------------------------------
-- pa.complete_task — completion with recurrence rollover in one place, so
-- after_completion series re-anchor to the actual finish date.
-- ---------------------------------------------------------------------------
create or replace function pa.complete_task(p_id bigint, p_note text default null)
returns table (task_id bigint, next_task_id bigint, next_due date)
language plpgsql as $$
declare
  r           pa.recurrence;
  v_rec_id    bigint;
  v_next_due  date;
  v_next_id   bigint;
begin
  update pa.task set status = 'done', completed_at = now(),
         notes = case when p_note is null then notes
                      else coalesce(notes || E'\n', '') || p_note end
   where id = p_id
   returning recurrence_id into v_rec_id;

  if not found then
    raise exception 'no task with id %', p_id;
  end if;

  if p_note is not null then
    insert into pa.event(actor, entity_type, entity_id, action, note)
    values (pa.current_actor(), 'task', p_id, 'note', p_note);
  end if;

  if v_rec_id is not null then
    select * into r from pa.recurrence where id = v_rec_id;
    update pa.recurrence set last_completed_on = current_date where id = v_rec_id;

    -- Only after_completion series roll forward here; fixed series are
    -- materialised by the scheduler on calendar dates, independent of this.
    if r.active and r.mode = 'after_completion' then
      v_next_due := current_date + r.interval_days;
      if r.until_date is null or v_next_due <= r.until_date then
        insert into pa.task(project_id, person_id, title, notes, status, importance,
                            effort_minutes, energy, due_on, hard_deadline,
                            defer_until, recurrence_id, occurrence_date, source)
        values (r.project_id, r.person_id, r.title, r.notes, 'next', r.importance,
                r.effort_minutes, r.energy, v_next_due, r.hard_deadline,
                case when r.lead_time_days > 0 then v_next_due - r.lead_time_days end,
                r.id, v_next_due, 'recurrence')
        returning id into v_next_id;

        insert into pa.task_context(task_id, context_id)
        select v_next_id, context_id from pa.recurrence_context where recurrence_id = r.id;

        update pa.recurrence set last_spawned_for = v_next_due where id = r.id;
      end if;
    end if;
  end if;

  return query select p_id, v_next_id, v_next_due;
end;
$$;

-- ---------------------------------------------------------------------------
-- pa.defer_task — a deferral is a decision worth recording, not a silent edit.
-- ---------------------------------------------------------------------------
create or replace function pa.defer_task(p_id bigint, p_until date, p_reason text default null)
returns bigint
language plpgsql as $$
begin
  update pa.task
     set defer_until = p_until,
         scheduled_for = case when scheduled_for is not null and scheduled_for < p_until
                              then p_until else scheduled_for end
   where id = p_id;
  if not found then
    raise exception 'no task with id %', p_id;
  end if;
  if p_reason is not null then
    insert into pa.event(actor, entity_type, entity_id, action, to_value, note)
    values (pa.current_actor(), 'task', p_id, 'note', p_until::text, p_reason);
  end if;
  return p_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- pa.log_contact — records reaching out and resets the cadence clock.
-- ---------------------------------------------------------------------------
create or replace function pa.log_contact(
  p_person_id bigint, p_on date default current_date,
  p_how text default null, p_note text default null
) returns bigint
language plpgsql as $$
begin
  update pa.person set last_contact_on = p_on where id = p_person_id;
  if not found then
    raise exception 'no person with id %', p_person_id;
  end if;
  insert into pa.event(actor, entity_type, entity_id, action, to_value, note, payload)
  values (pa.current_actor(), 'person', p_person_id, 'contacted', p_on::text, p_note,
          jsonb_strip_nulls(jsonb_build_object('how', p_how)));
  return p_person_id;
end;
$$;

-- A read-only role, so the SQL escape-hatch tool and the dashboard cannot
-- write. Created without a password; grant LOGIN separately if it needs one.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'pa_reader') then
    create role pa_reader nologin;
  end if;
end $$;

grant usage on schema pa to pa_reader;
grant select on all tables in schema pa to pa_reader;
alter default privileges in schema pa grant select on tables to pa_reader;

comment on schema pa is 'Personal assistant: tasks, projects, goals, people, contexts, event log.';
