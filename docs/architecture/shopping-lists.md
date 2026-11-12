# Shopping lists — design, settled 2026-11-10

Andre, 2026-11-10: *"I want to add a shopping list feature. It should allow the
maintenance of several named lists, organized by category of place of purchase like
hardware store, grocery with specialty sub-categories like butcher,
technology/computing, outdoors/camping, etc. I want to be able to add, update and delete
to/from the lists from any surface including signal and claude. I should be able to call
up a list like 'show me my grocery list'."*

Task **212**. Seven design decisions were put to him and settled in the same session;
each one below records the choice **and the reasoning**, because the reasoning is what a
later session needs in order not to relitigate it.

**Phase 1 shipped 2026-11-10** (commit `80abf0b`, migration `004_shopping.sql`): the
tables, five `pa_shop_*` tools, and the Claude surface. **Phase 2 deployed 2026-11-12
17:14** (`0facde4`, Signal, see below, `shop-phase2-report.md`). It needs no migration.
Andre's live Signal test passed the same day, adding and removing a probe item with no
task created. Phases 3 and 4 are open.

---

## The one-line summary

**The trip is a task. The list is data the trip reads.**

## Decisions

### 1. Items live in their own tables, not in `pa.task`

There were ~121 open tasks at the time. A standing grocery list of 30 items would nearly
double that and pour noise into `v_actionable`, `pa_daily_brief`, `pa_suggest_now` and
the backlog dashboard. The "what should I do right now" signal is the most valuable
thing in this system and milk would dilute it.

Items also want fields tasks do not have — quantity, unit, brand preference, last bought
— and lack the ones tasks need: effort, energy, importance, a due date. Forcing them
into one table would mean teaching a dozen views to exclude them, and getting that filter
right in a dozen places is a worse bet than one migration.

**Verified, not assumed.** Phase 1 proved it with a JSON fingerprint of the task counts,
the full `v_actionable` and `v_scored` id lists, the complete daily brief and two suggest
calls — compared byte for byte before the migration, after it, with 21 shopping items in
the tables, and after cleanup. All identical. A determinism check confirmed the
fingerprint was stable and a positive control (one `task_add`) made it fail.

### 2. Categories and named lists are orthogonal, not alternatives

Every item carries a **category** (grocery, hardware, technology, outdoors) and an
optional **sub-category** (butcher, produce, plumbing). Named lists exist separately for
scoped projects — "bathroom reno", "camping trip" — which may span several stores.

**`list_id` is nullable, and the two views cross:**

- *"Show me my grocery list"* returns **every** unbought grocery item regardless of list
  membership, each annotated with its list name if it has one. Standing in the hardware
  store he wants everything he needs there, including the pipe wrench that belongs to
  "bathroom reno".
- *"Show me the camping list"* returns everything in that list, across categories.

One item table, two axes over it.

### 3. Categories organize; the specific store is a detail on the item

"Butcher" and "Sutter's Meats" are different granularities and Andre uses both — task 210
names the store. So `subcategory` carries the counter and an optional free-text `store`
carries the shop. **No store table**: it is a lookup nobody will maintain.

### 4. Quantity is free text

"2 lb", "a dozen", "enough for 8". Structured units are a trap for groceries — he would
spend longer choosing a unit than typing the word.

### 5. Bought items are remembered, with history

`bought_at` is set and the item leaves the active list. History is what makes the system
able to notice "coffee, usually every three weeks, it's been four", and lets regulars be
re-added without retyping. Deleting outright would make that impossible forever.

**Two closing timestamps, not one.** `bought_at` means he bought it; `removed_at` means
it came off the list without being bought — he changed his mind, or the shop was out.
Active means **both are null**. Conflating them would corrupt the only thing this
decision exists to enable: "out of stock twice running" and "bought three weeks ago" are
opposite facts about a staple.

### 6. Staples are asked about, never auto-added

The check-in may ask *"coffee — it's been four weeks, add it to the grocery list?"* and
adds only on a yes. His standing instruction is to be pushed gently, and the
five-question cap still applies. **An item that appears on the list without him asking is
an item he starts second-guessing at the store**, which costs more than the forgotten
coffee. Phase 3.

### 7. Single-user for now, extensible by design

The Signal allowlist stays exactly one number — it exists so that a stranger who finds
the bot's number gets nothing. But `added_by` is in the schema from day one, so a
household grocery list is a config and security change later rather than a migration.

### 8. Items never surface in the backlog — and therefore there is no date column

Andre's choice, overriding a recommendation. Items are purely a store-time tool;
anything genuinely time-bound becomes a real task alongside the item.

**The consequence is that `needed_by` must not exist.** A date column nothing reads would
look like a deadline, behave like a comment, and eventually be trusted. "Before
Thanksgiving" goes in the item's free-text note, which does appear in the read-back, and
a real deadline becomes a real task. That is what happened with the turkey (208 → 210)
and it worked.

## Schema as built — migration `004_shopping.sql`

```sql
pa.shop_normalize(text)          immutable; lower, collapse whitespace,
                                 strip trailing . , ; : ! ?

pa.shopping_list   id, name, category, notes, created_at, archived_at
                   unique index on lower(btrim(name)) where archived_at is null

pa.shopping_item   id, list_id (nullable FK), category, subcategory, name,
                   normalized_name  generated always as (pa.shop_normalize(name)) stored,
                   quantity, store, notes, added_by, added_at,
                   bought_at, removed_at, removed_reason
                   check (bought_at is null or removed_at is null)
                   check (removed_reason is null or removed_at is not null)
                   unique index on (coalesce(list_id,0), category, normalized_name)
                     where bought_at is null and removed_at is null
```

**Dedupe is a database guarantee, not application manners.** `normalized_name` is a
stored generated column calling the same function the tool's matcher calls, so the key
and the matcher cannot drift between the five tools that write items. Proven by a raw SQL
insert of a duplicate being rejected.

**Normalization is deliberately conservative.** It catches case, spacing and trailing
punctuation. It does **not** fold plurals: `potato` and `potatoes` stay two rows. The
asymmetry decides it — a wrong merge silently loses an item he asked for, while a missed
merge is visible in the read-back and takes a second to fix.

**Category is case-folded** and enforced by a CHECK, because it is part of the dedupe
key. `Grocery ` and `grocery` must be one place to keep milk.

## The tools

| Tool | Notes |
|---|---|
| `pa_shop_add` | `INSERT … ON CONFLICT` on the dedupe index, so it is race-free. Returns `added` or `updated_existing`. Notes are **appended** on a duplicate; a new list is announced in the message so a typo is visible |
| `pa_shop_list` | by `category` **or** `list`. Returns `items` (with ids) and `rendered` (text for Andre, no ids) |
| `pa_shop_update` | one item by id. Notes are **replaced** here — the same split `pa_task_update`/`pa_task_complete` already has, and a documented trap |
| `pa_shop_bought` | returns `marked`, `ambiguous`, `not_found` with `did_you_mean` |
| `pa_shop_remove` | sets `removed_at`. **Never deletes a row** |

Lists resolve by id or exact case-insensitive name, **with no substring step**, so a typo
creates a visibly new list rather than quietly landing in a similar one.

## Three constraints that are easy to miss

### The outbound filter and parentheses

`filter_outbound` extracts task ids from `(\d+)` **and** `task \d+`, and a hit on a task
in a sensitive project **redacts the whole line**. So a read-back rendering
`coffee (2 lb)` risks being mangled. **The format must avoid parenthesised numbers** —
`coffee — 2 lb`. There is a test asserting the rendered output contains no `(digits)`,
run against the filter's real compiled pattern, with a positive control proving the test
can fail.

### Check-off cannot use the task-id discipline

§4 of `task-agent-brief.md` requires every question to carry its task id, which is what
makes a reply resolvable. **Numbering groceries is absurd**, so that safeguard is not
available here. The replacement is **echo-back**: `pa_shop_bought` returns exactly what
it matched, so the caller states it back. It is mandatory, not a nicety — fuzzy matching
is doing a job ids do everywhere else.

**And ambiguity marks nothing.** "coffee" against *coffee* and *coffee filters* returns
both candidates and writes nothing. An exact match deliberately does not beat a
containing match; the cost is that "milk" against *milk chocolate* also needs an id or a
scope, which is the right side to err on.

### Dedupe on normalized name within a category

"Add milk" when milk is already listed must do nothing or bump the quantity, never create
a second row. The failure mode is a list that slowly accumulates three kinds of milk and
stops being trusted — the same way a backlog full of stale rows stops being read.

## Read-back format

Plain text, grouped by sub-category in the order he walks the store, compact lines, no
parenthesised numbers. Long but scannable, and it works in an aisle with bad signal —
which is the deciding factor, because **Signal is the surface he will have in the store.**

**Walk order lives in one dict** (`WALK_ORDER` at the top of `pa/shop.py`), marked as a
placeholder to tune with Andre. Grocery runs produce, bakery, deli, butcher, seafood,
dairy, frozen, pantry, snacks, drinks, household; there is a hardware sequence too.
Unlisted sub-categories sort after the listed ones alphabetically; items with none come
last as "Other". It stays in code until there is a second store or a second person with a
different walk — one person's walk order changes perhaps twice a year, and a table would
need tools to edit it.

## Where this joins what already exists

The payoff is not the list; it is not having to remember to open it.

- **`town_trip` (situation 6)** already exists, as do the `errand` and `town` contexts.
  Heading to town should surface the lists for the stores on that trip.
- **`pa_suggest_now`** should treat *an errand task plus a non-empty list for that store*
  as a stronger suggestion than either alone.
- Task **210** (turkey pickup at Sutter's Meats, 25 Nov) is exactly the shape of errand a
  store-keyed list groups.

## Phasing

| Phase | What | Why this order | State |
|---|---|---|---|
| 1 | Migration, MCP tools, the Claude surface | Testable end to end with no Signal risk | **done, `80abf0b`** |
| 2 | Signal add / read / check-off with echo-back | Depends on phase 1 being right | **done 2026-11-12**, `0facde4`, live-tested over Signal |
| 3 | `town_trip` surfacing and staple prompting | Depends on having history to reason from | open |
| 4 | Voice capture on the Pi | Highest-value capture surface, least reliable host | open |

**Voice is last on purpose.** Noticing you are out of coffee happens in the kitchen with
your hands full, so `vocalassistant` is the natural capture point — but the Pi was found
switched off on 11-08, its backup leg failed the same night, and it was off again during
the 11-10 power cut. It should not be in the critical path of a feature Andre is trying to
come to rely on.

## Phase 2 — Signal, settled with Andre 2026-11-12

**Signal replies are not the check-in.** An inbound message goes
`signal_bridge._handle_incoming` → `quickask.ask_with_history`, which reads
`docs/claude/quick-ask-brief.md`. The four-a-day check-in reads `task-agent-brief.md`.
Before phase 2 both used one registry, `toolset.ALL_TOOLS`. Task 217's proposed fix (a
line in `task-agent-brief.md`) would have gone into the brief that never sees "add milk".

Three decisions:

1. **Only the Signal path gets the tools.** `toolset.QUICKASK_TOOLS` is `ALL_TOOLS`
   plus the five, and `quickask_dispatch` routes them to `pa.shop.dispatch`. The
   check-in keeps `ALL_TOOLS` and a `dispatch()` that does not route shop names, so
   even a hallucinated `pa_shop_add` there is an unknown tool, not a write. Phase 3's
   staple prompting is when the check-in gets read access, deliberately, again.
2. **All five tools**, matching the original ask to add, update and delete from any
   surface.
3. **The 217 rule stays as a backstop.** Both briefs say a shopping item is never a
   task. The check-in's says it has no shop tools and should point him at the bot.

The read-back rule moved into the brief because the model, not the renderer, now
writes the Signal reply: send `rendered` as it comes, never number items.
`tests/test_toolset.py` (30 checks) proves the wiring, with a positive control for each
property. Three code-level breaks were each confirmed to turn it red: quickask back on
`ALL_TOOLS`, shop tools leaked into `ALL_TOOLS`, and the check-in `dispatch()` routing
shop names.

## The live gap until phase 2 — task 217 (closed by phase 2 once deployed)

**The agent has no shop tools.** That isolation is what made phase 1 safe:
`assistant/toolset.py` does `from pa.tools import TOOLS` wholesale, so registering the
shopping tools there would have handed them to the check-in agent on its next restart.
They live in `pa/shop.py`, imported only by `server.py`.

But it means that if Andre texts the bot *"add milk to the grocery list"*, the only write
tool it holds that fits is **`pa_task_add`** — producing a task called "milk" in
`pa.task`, which is exactly the pollution decision 1 exists to prevent. Until phase 2,
shopping goes through claude.ai. The cheap mitigation is one line in the agent brief,
which is live config read fresh on every run.
