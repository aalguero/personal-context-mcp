-- 004_shopping.sql — shopping lists, phase 1 of task 212.
--
-- The trip is a task. The list is data the trip reads. Items live here and
-- never in pa.task, so nothing in this file is referenced by v_task, v_scored,
-- v_actionable, v_today, suggest_tasks() or the daily brief, and nothing here
-- writes to pa.event. Purely additive: two new tables and their indexes, no
-- change to any existing object.
--
-- Deliberately absent: a needed_by column. Items never surface in the backlog
-- (Andre's decision), so a date column would look like a deadline and behave
-- like a comment. "Before Thanksgiving" goes in notes; a real deadline becomes
-- a real task.

-- Conservative on purpose: lower-case, collapse internal whitespace, strip
-- trailing . , ; : ! ? and surrounding space. No stemming, no plural folding —
-- "potato" and "potatoes" stay two rows. A wrong merge silently loses an item;
-- a missed merge is visible in the read-back. One function, used by the
-- generated column and by the matcher in pa/shop.py, so the dedupe key and
-- the lookup cannot disagree. Changing it later means a new migration that
-- also rewrites the stored column.
create function pa.shop_normalize(p text) returns text
language sql immutable strict parallel safe
as $$ select btrim(regexp_replace(regexp_replace(lower(p), '\s+', ' ', 'g'), '[\s.,;:!?]+$', '')) $$;

create table pa.shopping_list (
  id           bigserial primary key,
  name         text not null check (btrim(name) <> ''),
  category     text check (category = lower(btrim(category)) and category <> ''),  -- default for items added to this list
  notes        text,
  created_at   timestamptz not null default now(),
  archived_at  timestamptz
);

-- Lists are created implicitly by name, so two live lists must not share one,
-- or "camping" would resolve to whichever was created first.
create unique index shopping_list_live_name
  on pa.shopping_list (lower(btrim(name))) where archived_at is null;

create table pa.shopping_item (
  id              bigserial primary key,
  list_id         bigint references pa.shopping_list(id),   -- null: a standing item, on no named list
  -- Lower-cased because it is part of the dedupe key below: "Grocery" and
  -- "grocery" must not be two places to keep milk.
  category        text not null check (category = lower(btrim(category)) and category <> ''),
  subcategory     text,
  name            text not null,
  -- See pa.shop_normalize. Computed in the database, not in Python, so no
  -- writer can disagree about it.
  normalized_name text generated always as (pa.shop_normalize(name)) stored
                  check (normalized_name <> ''),
  quantity        text,                    -- free text: "2 lb", "a dozen", "enough for 8"
  store           text,                    -- free text: "Sutter's Meats"; no store table
  notes           text,
  added_by        text not null default 'andre',
  added_at        timestamptz not null default now(),
  -- Two closing timestamps, not one. bought_at: he bought it. removed_at: it
  -- came off without being bought (changed his mind, shop was out). A staple's
  -- cadence computed from a column mixing the two would be worse than none.
  -- Active means both are null.
  bought_at       timestamptz,
  removed_at      timestamptz,
  removed_reason  text,
  check (bought_at is null or removed_at is null),
  check (removed_reason is null or removed_at is not null)
);

-- Dedupe as a database guarantee, scoped per list: milk may sit on the
-- standing grocery list and in "camping trip" at once, but not twice in either.
create unique index shopping_item_active_dedupe
  on pa.shopping_item (coalesce(list_id, 0), category, normalized_name)
  where bought_at is null and removed_at is null;

-- The two read shapes: "my grocery list" (by category, across lists) and
-- "the camping list" (by list, across categories).
create index shopping_item_active_by_category
  on pa.shopping_item (category, subcategory)
  where bought_at is null and removed_at is null;
create index shopping_item_active_by_list
  on pa.shopping_item (list_id)
  where bought_at is null and removed_at is null and list_id is not null;

comment on table pa.shopping_list is
  'Named shopping lists (bathroom reno, camping trip). Orthogonal to category. Never part of the task backlog.';
comment on table pa.shopping_item is
  'Shopping items. Never part of the task backlog. Active = bought_at and removed_at both null.';
