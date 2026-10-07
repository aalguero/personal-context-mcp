-- ============================================================================
--  futile-pa : Migration 003 — situation fit, and a seeding correction
--
--  Two problems that only became visible once 78 real tasks were loaded. Both
--  were found by asking the same question of every situation and reading the
--  answers, which is not something an empty database can tell you.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. land_day should not provide @home
--
--    @home is defined as "At Brook House, indoors". A land day is outdoors, so
--    including it meant every indoor task matched a land day — the suggestion
--    list for a full day on the five acres came back with "Snuff boxes" and
--    "Gun safe" above "Wood pile". A seeding error, not a schema one.
-- ---------------------------------------------------------------------------
delete from pa.situation_context
 where situation_id = (select id from pa.situation where name = 'land_day')
   and context_id   = (select id from pa.context   where name = 'home');

-- ---------------------------------------------------------------------------
-- 2. Generality penalty in pa.suggest_tasks
--
--    THE PROBLEM. @phone tasks are satisfiable by six of the nine situations.
--    So they surfaced near the top of almost every list, crowding out the tasks
--    that genuinely needed the situation at hand. Asked what to do on a trip
--    into town, the system offered four phone calls before it offered the RMV
--    appointment — the one task that *cannot* be done any other way.
--
--    THE FIX. Add a bonus for how few situations can satisfy a task:
--
--        fit_bonus = (total situations − situations that can satisfy this task) × 4
--
--    The reasoning is about opportunity rather than preference. A task only one
--    situation can satisfy has exactly one chance to get done; if it is not
--    surfaced then, it is not surfaced at all. A task six situations can satisfy
--    has six chances, so passing it over now costs almost nothing. Weighting by
--    scarcity of opportunity is what makes a list feel like it understands where
--    you are.
--
--    WHY A BONUS AND NOT AN ORDERING. Sorting by specificity first would bury a
--    genuinely urgent generic task beneath a trivial specific one. With nine
--    situations the bonus tops out at 32, which is enough to lift a uniquely
--    situated task above a generic quick win of similar importance, and not
--    enough to outrank an overdue hard deadline (urgency alone reaches 90).
--
--    Note that a task with no contexts at all counts as satisfiable by every
--    situation, so it gets no bonus — which is correct. It is maximally generic.
-- ---------------------------------------------------------------------------

create or replace function pa.situation_count() returns int
language sql stable as $$
  select count(*)::int from pa.situation;
$$;

-- How many situations could satisfy each open task. Situation-independent, so it
-- is a property of the task rather than of the question being asked.
create or replace view pa.v_task_generality as
with sit_ctx as (
  select s.id as situation_id, array_agg(c.name) as provides
    from pa.situation s
    join pa.situation_context sc on sc.situation_id = s.id
    join pa.context c on c.id = sc.context_id
   group by s.id
)
select
  t.id as task_id,
  count(sc.situation_id)::int as satisfiable_by,
  greatest(pa.situation_count() - count(sc.situation_id), 0)::int * 4 as fit_bonus
from pa.task t
left join lateral (
  select array_agg(c.name) as ctxs
    from pa.task_context tc join pa.context c on c.id = tc.context_id
   where tc.task_id = t.id
) tc on true
left join sit_ctx sc on coalesce(tc.ctxs, '{}'::text[]) <@ sc.provides
where t.status not in ('done','dropped')
group by t.id;

comment on view pa.v_task_generality is
  'Per-task: how many named situations could satisfy its context requirements, '
  'and the resulting fit bonus used by pa.suggest_tasks. Fewer situations means '
  'fewer chances to ever do it, so surfacing it when the chance comes is worth more.';

-- CREATE OR REPLACE cannot change a function's OUT-parameter row type, and this
-- version adds fit_bonus / ranked_score / satisfiable_by to the result. So the
-- old signature has to be dropped explicitly. Safe: the whole migration runs in
-- one transaction, so there is no window where the function does not exist.
drop function if exists pa.suggest_tasks(text, int, text, text[], boolean, int);

create function pa.suggest_tasks(
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
  due_on date, contexts text[], score int, fit_bonus int, ranked_score int,
  satisfiable_by int, match_kind text, why text
)
language sql stable as $$
  with sit as (
    select s.* from pa.situation s where p_situation is not null and s.name = p_situation
  ),
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
    coalesce(g.fit_bonus, 0) as fit_bonus,
    (v.score + coalesce(g.fit_bonus, 0)) as ranked_score,
    coalesce(g.satisfiable_by, 0) as satisfiable_by,
    case when cardinality(v.contexts) = 0 then 'untagged' else 'context_match' end as match_kind,
    concat_ws('; ',
      case when v.effort_minutes is not null then v.effort_minutes || ' min' end,
      case when v.energy is not null then v.energy || ' energy' end,
      case when v.due_on is not null then 'due ' || v.due_on end,
      case when cardinality(v.contexts) > 0 then 'fits ' || array_to_string(v.contexts, '+') end,
      case when coalesce(g.satisfiable_by, 9) <= 2
           then 'hard to do any other time' end
    ) as why
  from pa.v_scored v
  left join pa.v_task_generality g on g.task_id = v.id
  cross join budget b
  cross join avail a
  where
    (b.minutes is null or v.effort_minutes is null or v.effort_minutes <= b.minutes)
    and (b.energy is null or v.energy is null
         or pa.energy_rank(v.energy) <= pa.energy_rank(b.energy))
    and (
      (cardinality(v.contexts) = 0 and p_include_untagged)
      or (a.names is not null and v.contexts <@ a.names)
      or (a.names is null and p_include_untagged)
    )
  order by
    case when cardinality(v.contexts) > 0 then 0 else 1 end,
    (v.score + coalesce(g.fit_bonus, 0)) desc,
    v.effort_minutes nulls last
  limit coalesce(p_limit, 10);
$$;
