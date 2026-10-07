-- ============================================================================
--  futile-pa : Migration 002 — reference data and initial structure
--
--  Two kinds of content here:
--    (a) Contexts and situations — the vocabulary the suggestion engine needs.
--        Without these, pa.suggest_tasks has nothing to match against.
--    (b) A starting skeleton of areas, programs, people and goals, derived from
--        the futileMCP context store as of September 2026.
--
--  Everything in (b) carries metadata {"seeded": true} so it is obvious what
--  was inferred rather than stated. Rename, reparent or delete freely — the
--  point is to have a real structure to react to instead of an empty database.
--
--  Idempotent: re-running inserts nothing new.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Contexts — the conditions a task requires
-- ---------------------------------------------------------------------------
insert into pa.context (name, label, kind, description) values
  ('home',        '@home',         'location', 'At Brook House, indoors'),
  ('barn',        '@barn',         'location', 'At or in the barn'),
  ('field',       '@field',        'location', 'Out on the land — field, woods, garden beds'),
  ('town',        '@town',         'location', 'In Huntington / Northampton / Westfield'),
  ('car',         '@car',          'location', 'In the car, driving or parked'),
  ('away',        '@away',         'location', 'Travelling or away overnight'),
  ('phone',       '@phone',        'tool',     'Needs only a phone — calls, texts, scheduling'),
  ('computer',    '@computer',     'tool',     'Needs a real keyboard and screen'),
  ('online',      '@online',       'tool',     'Needs network access'),
  ('handtools',   '@handtools',    'tool',     'Needs tools — repair, build, maintenance'),
  ('errand',      '@errand',       'location', 'Has to happen at a shop, office or counter'),
  ('lowenergy',   '@lowenergy',    'state',    'Can be done tired — sorting, filing, listening'),
  ('focus',       '@focus',        'state',    'Needs uninterrupted concentration'),
  ('dry',         '@dry',          'weather',  'Needs dry weather'),
  ('warm',        '@warm',         'weather',  'Needs the ground unfrozen / above freezing'),
  ('daylight',    '@daylight',     'time',     'Needs daylight'),
  ('kids',        '@kids',         'people',   'Needs one of the children present or on the line'),
  ('waiting_slot','@waiting_slot', 'time',     'Fits a short involuntary gap — queue, waiting room')
on conflict (name) do nothing;

-- ---------------------------------------------------------------------------
-- Situations — recurring circumstances, each supplying contexts + time + energy
--
-- These are the entry points for "what should I do right now". The EV charging
-- stop from the project brief is the canonical one: roughly 35 minutes, seated,
-- phone and laptop available, moderate energy, away from home.
-- ---------------------------------------------------------------------------
insert into pa.situation (name, label, description, typical_minutes, min_minutes, max_minutes, energy_available, notes) values
  ('ev_charging',    'EV charging stop',   'Plugged in at a charger. Seated, phone and laptop, network. Not at home.', 35, 15, 60, 'medium',
   'The original prompt for this whole feature: dead time that is perfect for calls and admin.'),
  ('dog_walk',       'Walk with Rosco',    'Daily 2–3 mile walk. Hands mostly free, phone in pocket, outdoors.', 45, 25, 75, 'medium',
   'Best standing slot for long phone calls — family, friends, anything that wants unhurried talk.'),
  ('morning_coffee', 'Morning coffee',     'First half hour of the day at the kitchen table. Low energy, phone or laptop.', 30, 15, 45, 'low', null),
  ('desk_block',     'Desk block',         'Deliberate uninterrupted stretch at the computer.', 90, 45, 180, 'high',
   'Reserve for the work that actually needs a brain: job search, financial modelling, writing.'),
  ('land_day',       'Land day',           'Half or full day outdoors on the 5 acres. Tools, physical work.', 300, 120, 480, 'high', null),
  ('town_trip',      'Trip into town',     'Already going in — batch everything that needs a counter or a shop.', 90, 30, 180, 'medium', null),
  ('waiting_room',   'Waiting room',       'Involuntary short gap, phone only.', 20, 5, 40, 'low', null),
  ('rainy_day',      'Rained off',         'Outdoor plans cancelled. Indoors, whole day available.', 240, 60, 480, 'medium',
   'The natural home for indoor backlog: LP cataloguing, filing, fly tying.'),
  ('evening_low',    'Evening, spent',     'End of day, no concentration left, but willing to do something easy.', 40, 15, 90, 'low', null)
on conflict (name) do nothing;

-- Which contexts each situation makes available.
insert into pa.situation_context (situation_id, context_id)
select s.id, c.id
from (values
  ('ev_charging',    array['phone','computer','online','lowenergy','waiting_slot']),
  ('dog_walk',       array['phone','field','dry','daylight','lowenergy']),
  ('morning_coffee', array['phone','computer','online','home','lowenergy']),
  ('desk_block',     array['computer','online','home','focus']),
  ('land_day',       array['field','barn','handtools','dry','warm','daylight','home']),
  ('town_trip',      array['errand','town','car','phone']),
  ('waiting_room',   array['phone','online','lowenergy','waiting_slot']),
  ('rainy_day',      array['home','computer','online','handtools','barn','lowenergy','focus']),
  ('evening_low',    array['home','phone','online','lowenergy'])
) as m(sit, ctxs)
join pa.situation s on s.name = m.sit
join pa.context   c on c.name = any(m.ctxs)
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- People
--
--   Names for family are placeholders — the context store records relationships
--   but not first names. Rename with person_update; the slugs can stay.
--   Cadences are proposals based on the stated rhythm ("kids monthly to
--   bi-monthly", "close with sister", "very close with a senior neighbor"),
--   not instructions you gave. Adjust them and the connection view follows.
-- ---------------------------------------------------------------------------
insert into pa.person (name, slug, relationship, household, location, contact_cadence_days, has_plan, notes, metadata) values
  ('Daughter',       'daughter',  'daughter',     'N. Kingstown, RI', 'N. Kingstown, RI', 30, true,
   'Reliable caregiver for Rosco, available any time. Lives with ex-wife.', '{"seeded": true}'),
  ('Son',            'son',       'son',          'Cambridge, MA',    'Cambridge, MA',    30, true,
   'Emergency-only caregiver for Rosco (girlfriend less comfortable with him).', '{"seeded": true}'),
  ('Sister',         'sister',    'sister',       'New Mexico',       'New Mexico',       14, true,
   'Close. Co-owner of the joint taxable brokerage — must consent to custodian change.', '{"seeded": true}'),
  ('Father',         'father',    'father',       'n/a',              null,               21, true,
   '87. Relationship is personally challenging; cadence set deliberately rather than left to drift.', '{"seeded": true}'),
  ('Ex-wife',        'ex-wife',   'ex_wife',      'N. Kingstown, RI', 'N. Kingstown, RI', 60, false,
   'Friendly. Included in family travel. Reliable for Rosco; cannot take Jagger (has 2 cats).', '{"seeded": true}'),
  ('Senior neighbor','neighbor',  'neighbor',     'Huntington, MA',   'Huntington, MA',   10, false,
   'Very close. Short cadence because checking in matters more than it seems.', '{"seeded": true}')
on conflict (slug) do nothing;

-- ---------------------------------------------------------------------------
-- Areas — ongoing responsibilities. These never complete.
-- ---------------------------------------------------------------------------
insert into pa.project (name, slug, kind, status, importance, description, review_cadence_days, metadata) values
  ('House & Land',        'house-land',  'area', 'active', 5,
   'Brook House: 1759 farmhouse on 5 acres in Huntington. Buildings, systems, grounds, woods.', 7,  '{"seeded": true}'),
  ('Finances',            'finances',    'area', 'active', 5,
   'Portfolio, tax planning, retirement runway, advisor relationship. Connected to everything, managed separately.', 14, '{"seeded": true}'),
  ('Health',              'health',      'area', 'active', 5,
   'Hypertension and APS management, INR stability, activity level, weight.', 30, '{"seeded": true}'),
  ('Career',              'career',      'area', 'active', 5,
   'Post-Fidelity: severance runway, search, positioning, network.', 7,  '{"seeded": true}'),
  ('Family & Connection', 'family',      'area', 'active', 5,
   'Children, sister, father, ex-wife, neighbours, old friends. Staying in touch on purpose rather than by accident.', 14, '{"seeded": true}'),
  ('Food',                'food',        'area', 'active', 3,
   'Meal planning, groceries, cooking. Constraint: consistent vitamin K for INR stability; reducing red meat.', 7,  '{"seeded": true}'),
  ('Admin',               'admin',       'area', 'active', 3,
   'Bills, insurance, COBRA, registrations, filings, subscriptions. Tedious and unforgiving.', 14, '{"seeded": true}'),
  ('Learning & AI',       'learning-ai', 'area', 'active', 4,
   'Agentic AI track, LLM fundamentals, practical personal applications.', 14, '{"seeded": true}'),
  ('Music',               'music',       'area', 'active', 3,
   'Playing and writing, LP collection, discovery, modular and generative work.', 30, '{"seeded": true}'),
  ('Outdoors',            'outdoors',    'area', 'active', 3,
   'Fly fishing, birding, the gun club. The things the land and the season make possible.', 30, '{"seeded": true}')
on conflict (slug) do nothing;

-- ---------------------------------------------------------------------------
-- Programs and projects
-- ---------------------------------------------------------------------------
insert into pa.project (name, slug, kind, parent_id, status, horizon, importance, description, target_date, review_cadence_days, metadata)
select v.name, v.slug, v.kind,
       (select id from pa.project p where p.slug = v.parent),
       v.status, v.horizon, v.importance, v.description, v.target_date::date, v.review, v.meta::jsonb
from (values
  ('Native Plant Garden',        'native-garden',      'program', 'house-land', 'active',   'long',   5,
   'Converting 5 acres toward wild native character. 1/4 acre lawn conversion is the current front: grass killed, beds ready. Wing and a Prayer order (est. 26009, $2,956.92) staged for May and June pickup.',
   null, 14, '{"seeded": true}'),
  ('Farmhouse Projects',         'farmhouse',          'program', 'house-land', 'active',   'long',   4,
   'Home improvements to finish before retirement (~8 year horizon). Multi-year, sequenced, mostly weather- and contractor-bound.',
   '2034-06-30', 30, '{"seeded": true}'),
  ('Wealth Transition',          'wealth-transition',  'program', 'finances',   'active',   'short',  5,
   'Mercer Advisors onboarding and moving rollover IRA, inherited IRA and joint taxable off Fidelity. 401(k) stays put to preserve the rule-of-55 exemption. Sister must consent on the joint account.',
   '2026-12-31', 7, '{"seeded": true}'),
  ('Tax Planning 2026–2027',     'tax-2026-27',        'project', 'finances',   'active',   'short',  5,
   'Realize nothing in 2026 (~43.8% combined marginal). 2027 fork depends on re-employment: basis isolation via a new 401(k), or large low-bracket conversions. Inherited IRA distributions get first claim on cheap bracket room.',
   '2027-04-15', 14, '{"seeded": true}'),
  ('Job Search',                 'job-search',         'program', 'career',     'active',   'short',  5,
   'Senior technology executive roles. Severance continuation ends Feb 2027, extendable to May 2027 — that is the real clock.',
   '2027-02-28', 7, '{"seeded": true}'),
  ('LP Collection Catalog',      'lp-catalog',         'project', 'music',      'active',   'medium', 2,
   'Full digital catalog of 1000+ LPs. Perfect rainy-day and low-energy work, which is why it never gets done.',
   null, 30, '{"seeded": true}'),
  ('Agentic AI Track',           'agentic-ai',         'project', 'learning-ai','active',   'medium', 4,
   'Structured LLM fundamentals and agent building, in Python. Hands-on, real problems with concrete outputs.',
   null, 14, '{"seeded": true}'),
  ('Personal Assistant Build',   'pa-build',           'project', 'learning-ai','active',   'short',  4,
   'This system: Postgres backend on the personal MCP server, tools, daily brief, dashboard.',
   null, 7, '{"seeded": true}'),
  ('Rosco — Senior Care',        'rosco',              'project', 'family',     'active',   'short',  5,
   'Shiba Inu, 13. Senior-stage care, vet cadence, and honest planning. Deeply attached; this deserves to be explicit rather than carried around unspoken.',
   null, 30, '{"seeded": true}'),
  ('Household Admin 2026',       'admin-2026',         'project', 'admin',      'active',   'short',  3,
   'COBRA ($347/mo, ~Aug 2026–Feb 2028), Providence lease closeout and $2,300 deposit, insurance, registrations, filings.',
   null, 14, '{"seeded": true}')
) as v(name, slug, kind, parent, status, horizon, importance, description, target_date, review, meta)
where not exists (select 1 from pa.project p where p.slug = v.slug);

-- Person plans — one container per family member, per the project brief.
insert into pa.project (name, slug, kind, parent_id, person_id, status, horizon, importance, description, review_cadence_days, metadata)
select v.name, v.slug, 'person_plan',
       (select id from pa.project p where p.slug = 'family'),
       (select id from pa.person pe where pe.slug = v.person),
       'active', v.horizon, v.importance, v.description, 30, '{"seeded": true}'::jsonb
from (values
  ('Plan — Daughter', 'plan-daughter', 'daughter', 'medium', 4, 'College loan support, time together, her trajectory.'),
  ('Plan — Son',      'plan-son',      'son',      'medium', 4, 'College loan support, time together, his trajectory.'),
  ('Plan — Sister',   'plan-sister',   'sister',   'medium', 4, 'Shared joint account, trips and experiences together, staying close at distance.'),
  ('Plan — Father',   'plan-father',   'father',   'short',  4, '87. Contact rhythm, his affairs, and what to do before it is too late to do it.')
) as v(name, slug, person, horizon, importance, description)
where not exists (select 1 from pa.project p where p.slug = v.slug);

-- ---------------------------------------------------------------------------
-- Goals — outcomes, separate from the projects that deliver them
-- ---------------------------------------------------------------------------
insert into pa.goal (name, description, horizon, target_date, measure, status, importance, metadata)
select v.name, v.description, v.horizon, v.target_date::date, v.measure, 'active', v.importance, '{"seeded": true}'::jsonb
from (values
  ('Retire in ~8 years', 'Retire around 2034 from Brook House, without moving.', 'long', '2034-12-31',
   'Portfolio and paid-off mortgage support target spend without employment income', 5),
  ('Mortgage paid off by retirement', 'Pay off the Huntington mortgage by retirement, or hold a clear plan to do so shortly after.',
   'long', '2034-12-31', 'Mortgage principal = 0', 5),
  ('Help the children clear college loans', 'Contribute to paying down both children''s college loans.',
   'medium', null, 'Loan balances reduced on an agreed schedule', 4),
  ('Add no major new debt', 'No new major debt beyond the existing mortgage.', 'long', null,
   'No new liability above a set threshold', 4),
  ('Re-employ at senior executive level', 'Land a senior technology executive role before severance continuation ends.',
   'short', '2027-05-31', 'Signed offer', 5),
  ('Quarter acre converted to native planting', 'Complete the lawn-to-native conversion: beds planted and established.',
   'medium', '2027-06-30', 'All beds planted and surviving a full season', 4),
  ('Hold weight and activity', 'Maintain ~190lb and the daily walk plus physical land work.', 'long', null,
   'Weight stable; walks continue daily', 4),
  ('Reduce red meat', 'Cut red meat intake without disturbing vitamin K consistency.', 'short', null,
   'Red meat meals per week down, INR stable', 3),
  ('LP collection fully catalogued', 'Every record in the 1000+ LP collection in a digital catalog.', 'medium', null,
   'Catalog count matches shelf count', 2)
) as v(name, description, horizon, target_date, measure, importance)
where not exists (select 1 from pa.goal g where g.name = v.name);

-- Which projects serve which goals.
insert into pa.goal_project (goal_id, project_id, contribution)
select g.id, p.id, v.contribution
from (values
  ('Quarter acre converted to native planting', 'native-garden',     'The delivery vehicle'),
  ('Re-employ at senior executive level',       'job-search',        'The delivery vehicle'),
  ('Mortgage paid off by retirement',           'wealth-transition', 'Portfolio structure determines what is available to pay it down'),
  ('Retire in ~8 years',                        'wealth-transition', 'Drawdown sequencing and advisor relationship'),
  ('Retire in ~8 years',                        'tax-2026-27',       'Low-bracket years are scarce; using 2027 well compounds for a decade'),
  ('Retire in ~8 years',                        'farmhouse',         'Finish the house while there is still income to do it with'),
  ('LP collection fully catalogued',            'lp-catalog',        'The delivery vehicle'),
  ('Help the children clear college loans',     'plan-daughter',     'Her share'),
  ('Help the children clear college loans',     'plan-son',          'His share')
) as v(goal, proj, contribution)
join pa.goal g    on g.name = v.goal
join pa.project p on p.slug = v.proj
on conflict do nothing;
