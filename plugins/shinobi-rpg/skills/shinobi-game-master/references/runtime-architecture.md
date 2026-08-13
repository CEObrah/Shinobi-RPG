# Runtime Architecture and Authority

## Contents

1. Authority layers
2. Public ChatGPT/MCP boundary
3. Canonical transaction contract
4. Time and causal scheduling
5. Autonomous world
6. People and representation
7. Teams, forces, and formations
8. Missions, travel, information, and commitments
9. Health, relationships, family, assets, and history
10. Canon and static content
11. Narration boundary
12. Maintenance and repair

## Authority layers

Keep three authoritative repository layers:

- `runtime/`: deterministic execution, semantic commands, reducers, transactions, scheduling, APIs, validation, and persistence machinery.
- `game/`: static rules, schemas, mechanics data, world definitions, canon/reference content, and setting parameters.
- `state/`: mutable truth for the current campaign.

Do not create duplicate writable authority across layers.

`game/` must not depend on current mutable campaign state. `state/` should reference stable game IDs instead of copying static definitions. Chat memory, narration, Skill references, indexes, caches, tests, examples, and model inference never override committed state.

The Skill is authoritative for ChatGPT operating procedure and presentation, not for mechanical truth.

## Public ChatGPT/MCP boundary

Expose semantic player-safe operations through the Shinobi RPG Runtime MCP service. Do not expose arbitrary file patching, shell access, direct Git commands, or autonomous-mode impersonation as gameplay tools.

The public gameplay caller is the authenticated player. Autonomous faction/NPC actions are runtime-internal.

For live play, the public interface includes bounded reads, preview, exact execution, and audit. Current tool schemas and the live command catalog are dynamic capability authority.

A client must not gain authority merely by supplying an actor, issuer, grantor, commander, or owner ID in a payload.

## Canonical transaction contract

Every consequential persistent mutation should preserve this sequence:

1. capture current campaign revision and world time;
2. authenticate actor and resolve one semantic command;
3. load the smallest causal read set;
4. validate authority, membership, location, knowledge, ownership, custody, resources, and other prerequisites;
5. settle due causal dependencies that must precede the command;
6. execute deterministic reducers and registered bounded RNG where required;
7. build exact read/write/event/RNG manifests;
8. prepare WAL/staged after-images;
9. validate schemas, templates, references, chronology, conservation, secrecy, and domain invariants;
10. atomically persist declared owners;
11. commit repository transaction when Git durability is configured;
12. read back and verify persisted result;
13. write transaction receipt;
14. return a player-safe result packet;
15. narrate only the committed result.

Duplicate request IDs with identical commands are idempotent. Reused IDs with changed commands fail. Stale revisions fail. A failed write is never narrated as completed.

## Time and causal scheduling

Treat `state/time/causal-scheduler.json` as production scheduling authority unless runtime contracts explicitly migrate that ownership.

Use bounded causal hosts and wakes. Do not globally tick every person or faction as time advances.

Material boundaries may include:

- mission deadlines;
- commitments and appointments;
- travel arrivals;
- recovery;
- faction/team/institution/formation reviews;
- projects;
- canon fronts;
- other registered causal events.

Cost should scale with due causal work, not with the total number of world identities or elapsed months.

Offscreen does not mean frozen. Routine progression belongs to cohort, population, organization, team, formation, institution, faction, or continuity hosts. Wake exact people only when individual causality requires divergence.

## Autonomous world

Use the same domain mechanics for autonomous actors as for equivalent player-visible world changes. Do not create a permissive second simulation path.

Autonomous reviews may generate bounded internal semantic intents from persisted goals, authority, knowledge, resources, doctrine, commitments, relationships, opposition, and risk policy.

Prefer deterministic policy for routine decisions. Allow bounded qualitative reasoning only inside Python-defined lawful option spaces with runtime validation and persistence.

Autonomous systems may recruit, materialize lawful identities, form/release formations, create/progress missions, train, adopt doctrine, deliver information, run projects, travel, fight, recover, or create successor commitments only when current mechanics and authority permit them.

## People and representation

Treat "dormant" as resolution state, not a separate kind of person.

Use representation levels:

- aggregate population or cohort;
- rostered persistent identity core;
- exact persistent person with individual components when causality requires them.

A logical person sheet may combine identity core, host/cohort baseline, sparse individual divergence, material events, and components such as health, knowledge, relationships, equipment, techniques, career, family, and commitments.

Derive age from birth time. Do not write routine people monthly merely to make time pass.

Materialization identifies a human already represented in population. It must not increase physical population or grant free capability, equipment, history, relationships, or authority.

## Teams, forces, and formations

Use one exact-team system for named socially coherent teams. Team differences come from members, roles, leadership, authority, doctrine, training, equipment, readiness, commitments, missions, relationships, and history, not bespoke team-name Python.

Doctrine can affect coordination, priorities, communication, fallback behavior, and training emphasis. It cannot directly grant physical strength, techniques, equipment, or authority.

Treat a force as persistent manpower/ownership authority. Treat a formation as an aggregate operational organization lawfully drawn from force manpower.

Keep ownership, command authority, operational attachment, temporary assignment, location, and equipment custody distinct.

Large battles resolve bounded formations/sectors. Wake named exact specialists only when individual causality can change the result, and reconcile exact consequences into the aggregate once.

Conserve deaths, captures, missing personnel, wounded/recovering personnel, equipment losses, and returned survivors against the same force/population truth.

## Missions, travel, information, and commitments

Mission creation must prove issuer and participant tasking authority. Player requests cannot impersonate NPC/faction issuers.

Missions begin and progress through current state and later causal events rather than appearing completed merely because they were created.

Treat travel through the game location graph and current party/custody/mission basis. Validate every traveler's participation.

Treat promises, orders, obligations, deadlines, recoveries, meetings, and similar future boundaries as persistent commitments/events when mechanically represented. Prose never substitutes for them.

Keep world truth, claims, knowledge holders, evidence, delivery, classification, confidence, and audience separate. Loading hidden truth does not grant player knowledge.

## Health, relationships, family, assets, and history

Use one persistent health model regardless of whether harm came from combat, training, travel, disease, surgery, or another cause. Injuries, disabilities, treatment, recovery, and death persist.

Use typed relationship edges for relationships. Keep membership and office/role assignment as organizational facts rather than overloading relationship prose.

Keep asset owner, custodian, current holder, location, assignment, and condition distinct where the asset system represents them.

Persist material history separately from current state and narrative prose. Material history may include death, major injury, capture, promotion, relationship change, mission result, information delivery, major asset transfer, battle, institution change, promise/obligation, family event, and canon divergence.

## Canon and static content

Store static canon/reference definitions under `game/`. Store current campaign truth under `state/`.

Past canon already true at the campaign anchor may seed baseline state. Future canon is conditional pressure, not destiny.

Do not force future teams, teachers, ranks, techniques, loyalties, deaths, achievements, relationships, or knowledge states merely because they exist in reference canon.

A large cold catalog may contain many people, teams, clans, settlements, institutions, criminal groups, merchants, medical sites, training grounds, military facilities, borders, and historical places without scheduling them all as active exact state.

## Narration boundary

Resolve mechanics first. Return only player-safe context/results. Then let ChatGPT narrate through the Shinobi Game Master Skill and the selected cold narration module.

Narration may add bounded sensory and stylistic detail that does not change mechanical truth. It may never invent outcomes, knowledge, resources, relationships, injuries, authority, or history.

Canonical GM documentation lives in:

- `plugins/shinobi-rpg/skills/shinobi-game-master/SKILL.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/`

Do not create a second root-level GM manual that can drift from the installed Skill.

## Maintenance and repair

Treat runtime/game-rule maintenance as OOC DEV. Source changes do not advance campaign time.

Before structural writes or migrations, resolve registered schemas, templates, blank owner skeletons, and system update contracts rather than inferring structure from neighboring examples.

Do not directly rewrite live campaign state as a casual bug fix. Repair confirmed corrupted campaign truth through an explicit migration or campaign-repair transaction with provenance.

Do not recreate retired dual authorities for compatibility. Temporary transition bridges should be one-directional, tested, and removable.
