-- 005_overdue_and_defer.sql — one definition of "overdue", and a defer that
-- acts on the decision it records. Tasks 194, 184, 195.
--
-- Replaces three objects from 001 by create-or-replace; 001 itself is not
-- edited (migrate.py checksums it). No table, column or row changes.
--
--   pa.v_overdue    the single definition. Same column list, new WHERE.
--                   repo.daily_brief() now selects from it rather than
--                   restating the predicate.
--   pa.defer_task   moves a soft due_on forward; refuses a hard deadline and
--                   a date that is not in the future.
--   pa.tg_task_log  one event per deferral: the due_on move a deferral now
--                   makes is carried in the 'deferred' event's payload, not
--                   logged a second time as an 'edited' event.

-- ---------------------------------------------------------------------------
-- pa.v_overdue
--
-- Overdue = has a past due date, is still open, Andre has not parked it, and
-- he has not deferred it into the future.
--
--   someday  out: parked deliberately, and the agent is told never to raise
--            it. Before this, two of the three overdue rows were someday.
--   waiting  in:  past its date means the other person has not come through,
--            which is exactly what should be seen.
--   inbox    in:  a dated item still untriaged past its date needs triage now.
--   deferred out, via v_task.is_deferred rather than a fourth copy of that
--            test -- EXCEPT when hard_deadline. defer_task now refuses a hard
--            deadline, but defer_until can still be set by pa_task_add or a
--            direct update, and a passed external deadline must never be
--            hidden by a decision to look at it later.
-- ---------------------------------------------------------------------------
create or replace view pa.v_overdue as
select * from pa.v_task
where status not in ('done','dropped','someday')
  and due_on is not null
  and due_on < current_date
  and (not is_deferred or hard_deadline)
order by due_on;

-- ---------------------------------------------------------------------------
-- pa.defer_task
--
-- Andre, 2026-11-11: deferring moves due_on, unless the task has a hard
-- deadline. A soft date is a pace line he set himself, and moving it is what
-- deferring means; a hard deadline is external and cannot be moved by
-- deciding to, so a defer is refused rather than half-applied (setting
-- defer_until while due_on stays put is exactly the bug in 194).
--
-- due_on only ever moves forward, the same rule scheduled_for already
-- follows: deferring to the 20th a task due on the 1st of next month must not
-- pull its date earlier, and an undated task stays undated rather than
-- acquiring a due date it never had.
--
-- Refusals use SQLSTATE 22023 (invalid_parameter_value) so repo.task_defer
-- can return them as {"error": ...}; "no task with id" keeps its old code and
-- behaviour.
-- ---------------------------------------------------------------------------
create or replace function pa.defer_task(p_id bigint, p_until date, p_reason text default null)
returns bigint
language plpgsql as $$
declare
  t pa.task;
begin
  select * into t from pa.task where id = p_id for update;
  if not found then
    raise exception 'no task with id %', p_id;
  end if;
  if p_until is null or p_until <= current_date then
    raise exception 'cannot defer task % to %: the date must be after today (%)',
                    p_id, coalesce(p_until::text, 'no date'), current_date
      using errcode = 'invalid_parameter_value';
  end if;
  if t.hard_deadline then
    raise exception 'task % has a hard deadline (%) and deferring cannot move it. If the deadline itself has changed, set the new date with pa_task_update (due_on); if it no longer applies, drop the task with pa_task_drop. Nothing was changed.',
                    p_id, coalesce(t.due_on::text, 'no date set')
      using errcode = 'invalid_parameter_value';
  end if;

  update pa.task
     set defer_until = p_until,
         due_on = case when due_on is not null and due_on < p_until
                       then p_until else due_on end,
         scheduled_for = case when scheduled_for is not null and scheduled_for < p_until
                              then p_until else scheduled_for end
   where id = p_id;
  if p_reason is not null then
    insert into pa.event(actor, entity_type, entity_id, action, to_value, note)
    values (pa.current_actor(), 'task', p_id, 'note', p_until::text, p_reason);
  end if;
  return p_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- pa.tg_task_log — as in 001, with two changes, both in the deferral path:
--   * the 'deferred' event's payload records the due_on move, if any;
--   * the 'edited' check ignores due_on on an update that is a deferral, so a
--     defer that moves due_on logs one event, not two.
-- An update that is a deferral AND changes title, notes, importance and so on
-- still logs 'edited' for those.
-- ---------------------------------------------------------------------------
create or replace function pa.tg_task_log() returns trigger
language plpgsql as $$
declare
  v_deferred boolean;
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

  v_deferred := new.defer_count > old.defer_count;
  if v_deferred then
    insert into pa.event(actor, entity_type, entity_id, action, from_value, to_value, note, payload)
    values (pa.current_actor(), 'task', new.id, 'deferred',
            coalesce(old.defer_until::text, old.scheduled_for::text),
            coalesce(new.defer_until::text, new.scheduled_for::text),
            new.title,
            case when new.due_on is distinct from old.due_on
                 then jsonb_build_object('due_on_from', old.due_on, 'due_on_to', new.due_on)
                 else '{}'::jsonb end);   -- payload is NOT NULL
  end if;

  -- Substantive edits, excluding the churn columns handled above.
  if (new.title, new.notes, case when v_deferred then old.due_on else new.due_on end,
      new.importance, new.effort_minutes, new.energy, new.project_id)
     is distinct from
     (old.title, old.notes, old.due_on, old.importance, old.effort_minutes, old.energy, old.project_id) then
    insert into pa.event(actor, entity_type, entity_id, action, note, payload)
    values (pa.current_actor(), 'task', new.id, 'edited', new.title,
            jsonb_strip_nulls(jsonb_build_object(
              'due_on',         case when new.due_on is distinct from old.due_on and not v_deferred then new.due_on end,
              'importance',     case when new.importance is distinct from old.importance then new.importance end,
              'project_id',     case when new.project_id is distinct from old.project_id then new.project_id end,
              'effort_minutes', case when new.effort_minutes is distinct from old.effort_minutes then new.effort_minutes end)));
  end if;

  return new;
end;
$$;
