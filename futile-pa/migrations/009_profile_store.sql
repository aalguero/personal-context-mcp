-- ============================================================================
--  futile-pa : Migration 009 — the personal context store moves into Postgres
-- ----------------------------------------------------------------------------
--  Until now the context store was ~/.personal-context/context.json, written by
--  four processes through two implementations, each with only a per-process
--  lock (task 223). Its .1-.3 rotations held about one second of history, and
--  keys like ira_holdings_apr2026 were history kept by hand in the key name.
--
--  Andre's decisions, 2026-11-13:
--    * its own schema, `profile`, walled off from pa_sql: pa_sql now runs as
--      pa_reader, which has SELECT on schema pa and nothing on profile, so
--      medical and financial facts are reachable only through the context
--      tools, never through ad-hoc SQL (which the Signal agent also holds);
--    * every version kept forever: profile.history is append-only and written
--      by triggers, so no code path can change an entry without leaving the
--      old value behind;
--    * scoping tiers: a category is 'normal' or 'sensitive'. The tier is data
--      here; what it means (left out of an unscoped get_context, never readable
--      by the check-in agent) is enforced in pa/profile.py. It is scoping, not
--      security: anyone holding PA_AUTH_TOKEN can still ask by name;
--    * the agents' own state (task_agent, task_agent_watchdog,
--      recurrence_runner) is not personal context and gets its own table,
--      pa.agent_state, with the code-owned keys of task 213 enforced by a
--      trigger, not only by two Python call sites.
--
--  Number 009 by Andre's choice; task 218's unbuilt task-gates spec moves to 010.
--
--  pa_sql's role is pa_reader, which migration 001 created (nologin, SELECT on
--  schema pa, default privileges for new pa tables) and nothing has used until
--  now. The app role is already a member of it on futileassistant (checked
--  2026-11-13), so no superuser step is needed. This migration refuses to run
--  if that membership is missing, rather than leave pa_sql unable to switch.
-- ============================================================================

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'pa_reader') then
    raise exception 'role pa_reader does not exist (migration 001 creates it): pa_sql would have no role to switch to';
  end if;
  if not pg_has_role(current_user, 'pa_reader', 'MEMBER') then
    raise exception 'role % is not a member of pa_reader, so pa_sql could not switch to it: as a superuser, grant pa_reader to %', current_user, current_user;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- profile: categories, entries, history
-- ---------------------------------------------------------------------------
create schema profile;
revoke all on schema profile from public;

create table profile.category (
  slug        text primary key
              check (slug ~ '^[a-z][a-z0-9_]{0,62}$'),
  description text,
  tier        text not null default 'normal'
              check (tier in ('normal', 'sensitive')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
comment on table profile.category is
  'One row per context category. Tier is scoping: sensitive categories are left out of an unscoped get_context and never reach the check-in agent (pa/profile.py).';

create table profile.entry (
  category   text not null references profile.category(slug) on delete cascade,
  key        text not null check (length(key) between 1 and 200),
  value      jsonb not null,
  updated_at timestamptz not null default now(),
  updated_by text not null default pa.current_actor(),
  primary key (category, key)
);

-- key null = an event about the category itself (tier/description, or its deletion).
create table profile.history (
  id        bigserial primary key,
  at        timestamptz not null default now(),
  actor     text not null default pa.current_actor(),
  category  text not null,
  key       text,
  action    text not null check (action in ('set', 'delete', 'category_set', 'category_delete')),
  old_value jsonb,
  new_value jsonb
);
create index history_category_key_at on profile.history (category, key, at desc);

create or replace function profile.log_entry() returns trigger
language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    insert into profile.history(category, key, action, new_value)
    values (new.category, new.key, 'set', new.value);
  elsif tg_op = 'UPDATE' then
    if new.value is distinct from old.value then
      insert into profile.history(category, key, action, old_value, new_value)
      values (new.category, new.key, 'set', old.value, new.value);
    end if;
  else
    insert into profile.history(category, key, action, old_value)
    values (old.category, old.key, 'delete', old.value);
  end if;
  return null;
end $$;

create trigger entry_history after insert or update or delete on profile.entry
  for each row execute function profile.log_entry();

create or replace function profile.log_category() returns trigger
language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    insert into profile.history(category, action, new_value)
    values (new.slug, 'category_set',
            jsonb_build_object('tier', new.tier, 'description', new.description));
  elsif tg_op = 'UPDATE' then
    if (new.tier, new.description) is distinct from (old.tier, old.description) then
      insert into profile.history(category, action, old_value, new_value)
      values (new.slug, 'category_set',
              jsonb_build_object('tier', old.tier, 'description', old.description),
              jsonb_build_object('tier', new.tier, 'description', new.description));
    end if;
  else
    insert into profile.history(category, action, old_value)
    values (old.slug, 'category_delete',
            jsonb_build_object('tier', old.tier, 'description', old.description));
  end if;
  return null;
end $$;

create trigger category_history after insert or update or delete on profile.category
  for each row execute function profile.log_category();

-- BEFORE, so it speaks before the foreign key from profile.entry does.
create or replace function profile.category_no_rename() returns trigger
language plpgsql as $$
begin
  if new.slug is distinct from old.slug then
    raise exception 'profile.category slugs are identities and cannot be renamed';
  end if;
  return new;
end $$;

create trigger category_no_rename before update on profile.category
  for each row execute function profile.category_no_rename();

-- Append-only. The owner could drop this trigger; it is a guard against a
-- wrong code path or a careless statement, not against the owner.
create or replace function profile.history_is_append_only() returns trigger
language plpgsql as $$
begin
  raise exception 'profile.history is append-only (% refused)', tg_op;
end $$;

create trigger history_no_update before update or delete on profile.history
  for each row execute function profile.history_is_append_only();
create trigger history_no_truncate before truncate on profile.history
  for each statement execute function profile.history_is_append_only();

-- ---------------------------------------------------------------------------
-- pa.agent_state: the agents' operational record, out of personal context
-- ---------------------------------------------------------------------------
create table pa.agent_state (
  namespace  text not null,
  key        text not null,
  value      jsonb not null,
  updated_at timestamptz not null default now(),
  updated_by text not null default pa.current_actor(),
  primary key (namespace, key)
);
comment on table pa.agent_state is
  'task_agent, task_agent_watchdog, recurrence_runner. Formerly categories of context.json. No history: written several times a day by code.';

-- Task 213's code-owned keys. assistant/agent_keys.py holds the same list for
-- the refusal message; tests/test_profile.py asserts the two agree.
create table pa.agent_state_code_key (
  namespace text not null,
  key       text not null,
  primary key (namespace, key)
);
insert into pa.agent_state_code_key(namespace, key) values
  ('task_agent', 'asked_recently'),
  ('task_agent', 'open_questions'),
  ('task_agent', 'last_reply_at'),
  ('task_agent', 'unanswered_runs');

-- A write to a code-owned key must come from a transaction that declared
-- itself code (set_config('pa.writer', 'code', true)), which only
-- pa/profile.py's code path does. Same caveat as above: anyone with SQL can set
-- it; this catches a wrong route (server.py's set_context, a model's tool
-- call), which is what task 213 was about.
create or replace function pa.agent_state_guard() returns trigger
language plpgsql as $$
declare ns text; k text;
begin
  if tg_op = 'DELETE' then ns := old.namespace; k := old.key;
  else ns := new.namespace; k := new.key; end if;
  if exists (select 1 from pa.agent_state_code_key c where c.namespace = ns and c.key = k)
     and coalesce(current_setting('pa.writer', true), '') <> 'code' then
    raise exception '%.% is code-owned (task 213): refused', ns, k;
  end if;
  if tg_op = 'DELETE' then return old; end if;
  return new;
end $$;

create trigger agent_state_code_owned before insert or update or delete on pa.agent_state
  for each row execute function pa.agent_state_guard();

-- ---------------------------------------------------------------------------
-- pa_sql's role: all of pa, none of profile. 001 granted these already; they
-- are repeated so the property does not rest on default privileges having
-- covered every table created since. profile gets no grant at all.
-- ---------------------------------------------------------------------------
grant usage on schema pa to pa_reader;
grant select on all tables in schema pa to pa_reader;
