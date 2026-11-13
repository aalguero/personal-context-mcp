-- ============================================================================
--  futile-pa : Migration 008 — shopping staples (task 212, phase 3)
-- ----------------------------------------------------------------------------
--  Staples are asked about, never auto-added (design decision 6): the check-in
--  may ask "coffee — usually every three weeks, it's been five; add it?" and
--  adds nothing itself. This migration is the memory that makes the question
--  possible and keeps it from being asked four times a day.
--
--  Andre's rule, 2026-11-13:
--    * a staple is anything BOUGHT on at least 3 distinct days;
--    * it is due once the days since the last purchase reach 1.5 x its median
--      gap between purchases, and it is not already on a list;
--    * after it is asked about, it is not asked again for one full median gap,
--      whatever the answer.
--  A removal after the last purchase ("changed my mind", "shop was out") counts
--  as an answer too: he has just made a decision about it.
--
--  Why a table and not the context store (Andre's choice): context.json has
--  three writers through two implementations (task 223). This record is written
--  by checkin.py after a delivered message and read by the view below; it wants
--  to be in the same transactional store as the purchases it is about.
--
--  Dates are America/New_York calendar dates. Gaps are counted between distinct
--  purchase DAYS, so two receipts in one shop do not make a zero-day gap that
--  would make every staple permanently due.
--
--  This takes number 008. Task 218's unbuilt spec, once called 004 and then
--  007, moves to 009.
-- ============================================================================

create table pa.shopping_staple_prompt (
    id              bigserial primary key,
    category        text not null,
    normalized_name text not null,
    asked_at        timestamptz not null default now()
);

create index shopping_staple_prompt_key
    on pa.shopping_staple_prompt (category, normalized_name, asked_at desc);

comment on table pa.shopping_staple_prompt is
  'One row per time the check-in asked whether to re-add a staple. Written by '
  'code (assistant/checkin.py) after a delivered message that named the item, '
  'never by the model. Read by pa.v_shopping_staple for the ask cooldown.';

create view pa.v_shopping_staple as
with today as (
    select (now() at time zone 'America/New_York')::date as d
), buys as (
    select distinct i.category, i.normalized_name,
           (i.bought_at at time zone 'America/New_York')::date as d
      from pa.shopping_item i
     where i.bought_at is not null
), gaps as (
    select b.category, b.normalized_name, b.d,
           b.d - lag(b.d) over (partition by b.category, b.normalized_name order by b.d) as gap
      from buys b
), stats as (
    select g.category, g.normalized_name,
           count(*)::int                                          as buy_count,
           max(g.d)                                               as last_bought,
           percentile_cont(0.5) within group (order by g.gap)     as median_gap
      from gaps g
     group by g.category, g.normalized_name
), enriched as (
    select s.*,
           last_row.name, last_row.subcategory, last_row.store, last_row.bought_at as last_bought_at,
           (select max(p.asked_at) from pa.shopping_staple_prompt p
             where p.category = s.category and p.normalized_name = s.normalized_name) as last_asked,
           (select max(r.removed_at) from pa.shopping_item r
             where r.category = s.category and r.normalized_name = s.normalized_name
               and r.removed_at > last_row.bought_at)                                 as removed_since,
           exists (select 1 from pa.shopping_item a
                    where a.category = s.category and a.normalized_name = s.normalized_name
                      and a.bought_at is null and a.removed_at is null)              as on_a_list
      from stats s
      cross join lateral (
            select i.name, i.subcategory, i.store, i.bought_at
              from pa.shopping_item i
             where i.category = s.category and i.normalized_name = s.normalized_name
               and i.bought_at is not null
             order by i.bought_at desc, i.id desc
             limit 1) last_row
)
select e.category, e.normalized_name, e.name, e.subcategory, e.store,
       e.buy_count, e.last_bought,
       round(e.median_gap::numeric, 1)                          as median_gap_days,
       (t.d - e.last_bought)                                    as days_since,
       e.last_asked, e.removed_since, e.on_a_list,
       (    e.buy_count >= 3
        and e.median_gap >= 1
        and not e.on_a_list
        and (t.d - e.last_bought) >= 1.5 * e.median_gap
        and (greatest(e.last_asked, e.removed_since) is null
             or (t.d - (greatest(e.last_asked, e.removed_since) at time zone 'America/New_York')::date)
                >= e.median_gap)
       )                                                        as due
  from enriched e cross join today t;

comment on view pa.v_shopping_staple is
  'Every item bought at least once, keyed by (category, normalized_name), with '
  'its purchase rhythm and whether the check-in should ask about it now (due). '
  'Rule (Andre, 2026-11-13): >= 3 distinct purchase days; days since last >= '
  '1.5 x median gap; not on a list; not asked about, or removed, within one '
  'median gap. Task 212 phase 3.';
