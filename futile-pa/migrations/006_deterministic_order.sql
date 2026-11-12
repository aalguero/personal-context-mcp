-- 006_deterministic_order.sql — break ties, so the same data gives the same answer.
--
-- pa.suggest_tasks ordered by (tagged first, ranked score desc, effort) and
-- then LIMITed. Rows tied on all three straddle the limit on live data
-- (2026-11-11: one tie group crosses suggest's 25-row cut), and SQL does not
-- define which tied rows survive a LIMIT. So two calls on unchanged data may
-- return different lists. test_shopping.py's "backlog unchanged: suggest" check
-- failed once that way on 2026-11-11 (ledger-213-report.md §5.2), alongside
-- "brief", whose top_next list also has a tie at its cut (scores 59/59 at row 8).
-- The brief's ORDER BYs live in pa/repo.py and get the same fix in the same commit.
--
-- The fix is a final tie-break on id: oldest task first, which is also
-- the order a person would expect between equals. It changes no score and no
-- filter. It only settles what was undefined.
--
-- Same signature and return type as 003, so create or replace suffices and
-- grants are kept. 003 itself is not edited (migrate.py checksums it). The
-- body below is 003's, copied programmatically, with one line added: v.id.

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
    v.effort_minutes nulls last,
    v.id
  limit coalesce(p_limit, 10);
$$;
