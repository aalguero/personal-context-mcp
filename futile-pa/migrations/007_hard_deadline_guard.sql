-- 007_hard_deadline_guard.sql — a hard deadline cannot be hidden by any path.
-- Task 227.
--
-- Migration 005 made pa.defer_task refuse a hard deadline. That enforced the
-- flag in one write path out of several. Two ways remained for a hard-deadline
-- task to leave the lists Andre looks at without anyone deciding it could:
--
--   1. status 'someday'. v_overdue excludes someday (deliberately: parked tasks
--      are never raised), with no exception for hard_deadline. A filing date
--      parked as someday vanished from overdue.
--   2. defer_until written outside defer_task: pa_task_add takes it,
--      pa_task_update accepted it (TASK_WRITABLE), and direct SQL can always
--      write it. v_overdue keeps a hard deadline visible once it has passed
--      (005's "or hard_deadline"), but v_actionable and suggest_tasks hide
--      it until defer_until, so it goes quiet BEFORE the deadline, which is
--      exactly when it matters.
--
-- Andre, 2026-11-12: refuse both, in the database, so every path obeys:
--
--   * An open hard-deadline task may not be 'someday'. Parking and a real
--     external date are contradictory: if the deadline no longer applies,
--     clear hard_deadline or drop the task. If it applies, the task stays visible.
--   * An open hard-deadline task's defer_until must be strictly before its
--     due_on. Hiding a hard deadline until shortly before it is legitimate.
--     That is what recurrence lead time does (task 159: due 11-15, hidden
--     until 11-01). Hiding it until on or after the deadline is not.
--
-- The rule never consults current_date, so a row that satisfies it today
-- cannot start violating it tomorrow. Unrelated edits to a compliant row can
-- never be refused by the mere passage of time. done and dropped rows are
-- exempt: a closed task hides nothing, and completing must never be refused.
-- A hard deadline with no due_on has nothing to compare defer_until against,
-- so only the someday rule applies to it.
--
-- Refusals use SQLSTATE 22023 (invalid_parameter_value), the same as
-- defer_task, so repo.py turns them into {"error": ...} the caller can act on.
--
-- The defer_until half of the TASK_WRITABLE side door is closed in pa/repo.py
-- in the same commit (task_update may now only clear defer_until, never set
-- it). This trigger is what holds for pa_task_add, the recurrence engine,
-- complete_task's rollover, and direct SQL.

-- Refuse to install over data that already breaks the rule: the trigger would
-- then refuse every later edit to those rows, including the fix. Verified
-- 2026-11-12 that no such row exists; this makes the migration prove it again.
do $$
declare n int;
begin
  select count(*) into n from pa.task
   where hard_deadline and status not in ('done','dropped')
     and (status = 'someday'
          or (defer_until is not null and due_on is not null and defer_until >= due_on));
  if n > 0 then
    raise exception '007: % open hard-deadline task(s) already violate the guard; fix them first', n;
  end if;
end $$;

create or replace function pa.tg_task_hard_deadline_guard() returns trigger
language plpgsql as $$
begin
  if not new.hard_deadline or new.status in ('done','dropped') then
    return new;
  end if;

  if new.status = 'someday' then
    raise exception 'task % has a hard deadline (%) and cannot be parked as someday. If the deadline no longer applies, clear hard_deadline or drop the task with pa_task_drop. Nothing was changed.',
                    coalesce(new.id::text, '(new)'), coalesce(new.due_on::text, 'no date set')
      using errcode = 'invalid_parameter_value';
  end if;

  if new.defer_until is not null and new.due_on is not null
     and new.defer_until >= new.due_on then
    raise exception 'task % has a hard deadline (%) and cannot be hidden until % (on or after the deadline). Hide it until a date before the deadline, or not at all. Nothing was changed.',
                    coalesce(new.id::text, '(new)'), new.due_on, new.defer_until
      using errcode = 'invalid_parameter_value';
  end if;

  return new;
end;
$$;

-- Fires on every insert and update. An "update of <columns>" list would be
-- narrower, but a column list is one more thing to keep in step with the rule.
-- The check is a few comparisons on the row already in hand.
create trigger task_hard_deadline_guard
  before insert or update on pa.task
  for each row execute function pa.tg_task_hard_deadline_guard();
