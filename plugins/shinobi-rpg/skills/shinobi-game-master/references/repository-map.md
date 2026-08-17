# Repository Map and Update Cookbook

Use this reference for OOC development and source-location questions. For live gameplay, use the connected runtime tools instead of reading raw repository state.

## 1. Authority layers

The repository has three mechanical authorities:

```text
runtime/   deterministic execution, semantic commands, transactions, scheduling, APIs

game/      static rules, schemas, mechanics, canon/reference content, cold world data

state/     mutable committed truth for the current campaign
```

Supporting areas:

```text
plugins/shinobi-rpg/skills/shinobi-game-master/   ChatGPT GM procedure and narration craft
runtime/contracts/                                machine routing, schemas/templates, update contracts
runtime/contracts/repository-map.json             machine-readable source router
runtime/contracts/rule-router.json                rule/mechanic router
runtime/contracts/system-contract-index.json      mutation authority/invariant router
runtime/contracts/template-index.json             structural template router
runtime/contracts/blank-owner-index.json          fact-free new-owner skeletons
docs/                                              deployment and human operations
tests/                                             regression evidence, never campaign truth
tools/                                             fast checks, targeted testing, maintenance utilities
```

Never treat chat history, Project memory, Skill prose, docs, tests, caches, indexes, narration, or model memory as mutable campaign authority.


## 2. Live play retrieval

Do not browse the repository to narrate a normal turn.

Use this progressive route:

1. `get_play_context` for current revision, world time, scene, player state, compact cast, recent player-known information, read hints, and command index.
2. `get_person_sheet` only for a person who materially matters to the current interaction.
3. `inspect_game_object` for one exact team, force, formation, mission, place, project, contract, custody record, inventory, finance object, or other permitted object.
4. `search_world_reference` for cold public setting/history facts when they materially improve the scene.
5. `get_command_contract` for exactly one selected semantic command.
6. Preview and execute only after intent is clear.

Stop as soon as enough player-safe authority exists. More files do not automatically produce better narration.

### Scene and cast semantics

`scene_cast.present_people` / `visible_people` are immediate-scene presence. `nearby_people` are at the site but not necessarily in the same room or conversation. `referenced_people` are relevant context only. Never infer physical presence from `loaded_owner_ids`, permitted read IDs, Project memory, or a cold reference hit. Load one person sheet when a present NPC needs richer voice/relationship/authority context.

### Compact projections are routing, not truth

These exist to make live reads cheap:

- `state/team/player-membership.json`: bounded player team-membership projection.
- `state/reg/membership-routes.json` plus `state/reg/membership-routes/<hash>.json`: authority:false deterministic routing from exact person/parent/service/assignment keys to active exact teams, and from person to House membership. Exact team/House owners remain authority.
- `state/reg/player-social-access.json`: bounded recent player social routing.
- `state/reg/player-social-access-shards/`: deterministic relationship-access shards for older exact peers.
- `state/reg/relationship-routes/`: 16 deterministic direct-edge route buckets for rare exact edge inspection; exact relationship truth remains in source shards.
- `state/formation/index.json`: bounded formation registry routing plus `formation_routes` from formation ID to source force, avoiding all-force scans.
- `state/mission/context-index.json`: mission routing for current participants/context.
- `state/reg/information-deliveries.json`: bounded information counts/routing only.
- `state/reg/information/claims/`: exact epistemic claims.
- `state/reg/information/deliveries/`: exact deliveries.
- `state/reg/information/knowledge/`: deterministic holder-hash directories containing a bounded holder index plus exact claim-membership hash buckets and exact sender/recipient delivery-membership buckets.

A projection never grants authority, knowledge, membership, or ownership. Exact owners must still verify the fact before a write.

## 3. OOC development retrieval rule

When changing source, find the smallest authority that owns the behavior.

1. Classify the problem as runtime behavior, static rule/content, mutable campaign truth, GM presentation, deployment, or explicit campaign repair.
2. Read `runtime/contracts/repository-map.json` if the exact owner is not obvious.
3. For a rule/mechanic, use `runtime/contracts/rule-router.json` rather than reading every rule file.
4. For a mutable system, use `runtime/contracts/system-contract-index.json` before writing.
5. For JSON structure, use the schema/template/blank-owner indexes instead of copying a neighboring file.
6. Search by symbol/function/command name before opening a large runtime file.
7. Stop reading once the reducer, owner, contract, and relevant regression are identified.

Never bulk-read the repository to gain confidence. Confidence comes from one authoritative route plus targeted verification.

## 4. Core campaign anchors

Use these only for OOC diagnosis or routed runtime code, not as a substitute for live MCP reads:

- `state/meta.json`: campaign ID, revision, world time, player ID.
- `state/player.json`: Wei Tang's exact player owner.
- `state/scene.json`: player-facing scene projection. Runtime commands that rely on it keep its world time synchronized with committed time; loaded/relevant owner IDs are never physical-presence evidence.
- `state/time/causal-scheduler.json`: tiny causal temporal root containing world time, counts, next-due and shard routing metadata.
- `state/time/causal-scheduler/hosts/<hash>.json`: deterministic exact scheduler-host shards.
- `state/time/causal-scheduler/event-index/<year>.json`: per-year due-day routing.
- `state/time/causal-scheduler/events/<year>/<date>.json`: exact due-event shards. Normal `advance_time` loads only the due window and referenced hosts; never rebuild the whole scheduler just to advance a bounded horizon.
- `state/index/owners.json`: prefix-sharded mutable owner router. Load only the matching shard for a known owner ID.

## 5. NPCs and people

First decide the representation level.

### Cold/reference person

Use cold world/reference data when a person only needs identity, canon/public history, role association, or possible future materialization.

Primary routes include:

- `game/world/canon-catalog.json`
- `game/data/content/`
- behavior/profile static data routed by `runtime/contracts/repository-map.json`

Cold reference must not carry current wounds, money, exact location, exact relationship values, current private knowledge, or daily schedules.

### Rostered identity core

Use a persistent identity core when the person should continue to exist by name but does not need heavyweight exact-person components every turn. A bounded registry may also carry that person's persistent lightweight individual capability/progression profile. House Tang uses this individual-lite form: training sections/cohorts are organizational summaries only and never reroll a member. Resolve via the owner index and person-sheet repository code under:

- `runtime/shinobi_runtime/people/`
- person-core owners routed from `state/index/owners.json`
- `state/reg/person-continuity.json`: tiny global continuity cursor/count only
- `state/reg/person-continuity/<0-f>.json`: deterministic exact-person continuity buckets; route by person ID, never scan all buckets

### Exact mutable person

Exact character owners usually live under `state/char/` or another owner-index-routed person path. Current mutable facts can include:

- health/injury/recovery;
- location;
- career/rank/office;
- exact equipment exceptions;
- knowledge packages/claims;
- relationships;
- commitments;
- family divergence;
- individual training and technique state;
- behavior evidence that has become campaign-specific.

Do not make an NPC exact merely to improve prose.

### Improving an NPC safely

For a cold enrichment:

1. update the static identity/canon/reference owner;
2. preserve provenance and knowledge classification;
3. do not create mutable state.

For a current exact-person change:

1. resolve the person through the owner index;
2. identify the owning mechanic for the fact being changed;
3. use the corresponding semantic command/reducer or explicit migration;
4. update schemas/templates/contracts if structure changes;
5. update targeted tests.

Materializing a person from aggregate population consumes or reclassifies an already represented human. Never create a free person, capability, equipment set, history, office, or relationship.

### Characterization

The GM may receive bounded `interaction_cues` on a targeted person sheet. These may shape voice and visible manner. They are not proof of hidden motive, allegiance, knowledge, fear, attraction, or future action.

Private behavioral fields stay private.

## 6. Teams

Named socially coherent teams use the exact-team system. Relevant paths are routed from:

- exact team owners under `state/team/`;
- team doctrine/training owners under `state/team/`;
- team operational histories under `state/team/history/`;
- `state/team/player-membership.json` for the player's bounded routing projection;
- `state/reg/membership-routes.json` and deterministic shards for direct person→team, parent→team, service-village→team, operational-assignment→team, and person→House routing without global active-team/House scans.

To change a team:

1. load the exact team;
2. load only affected member person refs;
3. prove membership/leadership/tasking authority;
4. keep doctrine, training, equipment, readiness, relationships, and commitments distinct;
5. reconcile deterministic membership/parent/service/assignment routes atomically with exact-team changes, plus any player-facing hot projection;
6. emit material history when consequential.

Do not create team-name-specific Python logic unless the team genuinely uses a different domain mechanic.

## 7. Forces, formations, and command

Think in this order:

```text
population/cohort -> force/manpower authority -> formation -> operational attachment
```

Key mutable routes include:

- `state/force/`: force/manpower authorities.
- `state/formation/`: formation registries and military organization state.
- `state/formation/index.json`: formation-to-force routing for direct lookup; do not scan every force registry to find one known formation ID.
- `game/data/manpower-capability/`: static capability/support profiles referenced by forces; this is reference data, not mutable manpower state.
- command/assignment owners routed by the owner index and system contracts.

For recruitment, creation, split, merge, casualty, demobilization, or materialization:

1. load the source population/force;
2. load the exact destination formation/team only;
3. prove authority;
4. conserve people and equipment;
5. keep ownership, command authority, temporary assignment, location, doctrine, training, readiness, and custody distinct;
6. persist casualties against the same population/force truth;
7. never regenerate lost manpower automatically.

Doctrine changes coordination, priorities, communication, fallback behavior, and training emphasis. It never grants free techniques, stats, equipment, or authority.

Formation registries are intentionally per-force hot owners. Do not materialize hundreds of formations merely because cold military organizations exist in setting data. If one force registry approaches the fast-gate size threshold, perform an explicit deterministic formation-sharding migration and update `state/formation/index.json`; do not create a parallel formation authority.

## 8. Missions and commitments

Mission owners live under `state/mission/` and are routed through `state/mission/context-index.json` for hot participant/context lookup.

A mission should separate:

- issuer/tasking authority;
- participants;
- objectives and deadlines;
- current state/progress;
- escrow/reward if relevant;
- information produced;
- material consequences;
- later settlement/history.

Do not complete a mission merely because it was created.

The active-mission list is a hot routing projection. A known exact `mission.*` ID remains inspectable after completion or projection eviction when the exact mission owner still records Wei as a participant. Do not treat omission from active context as forgetting.

Promises, appointments, recoveries, obligations, and other future boundaries must use persistent commitments/events when the mechanic exists. Prose is not persistence.

## 9. Information and knowledge

Do not recreate the old global information blob.

Current architecture:

```text
state/reg/information-deliveries.json
    bounded counts/routing only

state/reg/information/claims/<hash>/...
    exact claim authority

state/reg/information/deliveries/<hash>/...
    exact delivery authority

state/reg/information/knowledge/<holder>/index.json
    bounded recent-knowledge projection/count

state/reg/information/knowledge/<holder>/<bucket>.json
    exact holder knowledge membership

state/reg/information/knowledge/<holder>/delivery-<bucket>.json
    exact sender/recipient delivery participation
```

When adding information:

1. create one exact claim with source/provenance, epistemic kind, confidence, evidence, and subject;
2. add holder knowledge through deterministic shards;
3. deliver through an exact delivery when knowledge moves between people;
4. update only affected holder shards and bounded root counts;
5. surface a player-delivered report in scene context without granting world truth.

Observation, report, rumor, inference, and verified world truth are different things.

The compact play context carries only recent player-known claims. That is a recency window, not forgetting. If an older exact `claim.*` ID becomes material, `inspect_game_object` routes directly to the claim plus the player's deterministic knowledge shard and returns it only if Wei actually knows it. Never scan the full claim archive during an ordinary turn.

## 10. Relationships, reputation, and family

Relationships are exact edges, not prose summaries. Player hot access is routed through `state/reg/player-social-access.json`; older known peers remain reachable through deterministic access/source shards. Exact relationship truth lives in `state/reg/relationship-edges/<source>.json`. `state/reg/relationship-edge-index.json` is only a bounded counter, and `state/reg/relationship-routes/<0-f>.json` exists only for rare direct-edge routing.

Keep these separate:

- affiliation and membership;
- personal relationship;
- reputation by audience;
- factual knowledge;
- kinship;
- office/role;
- consent.

Do not turn House membership into affection or relationship score into command authority.

Family changes should use family/kinship contracts. Never create Wei's consent through a migration or narrative assumption.

## 11. Economy, inventory, and assets

Primary routes are selected through the economy rule/system contracts. Current inventory authority remains `state/inventory/registry.json`; it is not a narration read and should be touched only by economy/equipment/mission reducers.

Economy policy:

- exact player/House/organization/village money when conservation matters;
- exact mission escrow and major projects;
- exact genuinely scarce stock;
- ordinary retail cash may settle into aggregate local/private economy;
- ordinary merchants do not need permanent personal bank accounts or monthly accounting.

Keep owner, custodian, current holder, location, assignment, condition, stock, and valuation separate where mechanically represented.

`state/inventory/registry.json` is a remaining consolidated authority. Do not add decorative holders. If it approaches a material size/performance threshold, migrate holder storage through an explicit inventory-sharding change rather than adding a second writable inventory system.

## 12. Places and world content

Cold place/reference data belongs under `game/`, especially `game/world/canon-catalog.json` and routed world data.

A location needs mechanics only when its advertised function can change a consequential choice.

Usually mechanical:

- hospital/treatment site;
- training ground;
- prison/interrogation/custody facility;
- intelligence/ANBU/Root facility;
- market when stock/prices matter;
- military fort/checkpoint/depot;
- route/pass/port when movement or control matters.

Usually reference-only until causality changes:

- restaurant;
- shrine;
- ordinary street;
- memorial;
- historical battlefield;
- scenic landmark.

Flavor places do not need wallets, scheduler hosts, payroll, or monthly ticks.

## 13. History and canon

Past completed setting history belongs in cold game/reference data. Current campaign material events belong in segmented semantic history.

Hot event authority:

- `state/reg/world-events.json`: bounded current event head plus constant-size archive counters; it does not retain a growing list of archive paths.
- `state/history/events/segment-*.json`: archived exact history segments, addressed deterministically by sequence.
- `state/history/events/routes/<0-f>.json`: 16 deterministic routing buckets from a known event ID to its archive segment for direct historical lookup.
- `state/history/events/by-actor/<hash>.json`: small `authority:false` actor route index containing only a recent cue plus page metadata.
- `state/history/events/by-actor/<hash>/page-*.json`: deterministic fixed-size pages preserving the actor's complete routed semantic-event history. Resolve every event ID back through exact hot/archive event authority before using it.

Do not put future canon into committed outcome history. Future canon is conditional pressure and may diverge.

When adding history, record who/what/where/when, consequence, provenance, and knowledge classification where relevant. History should explain current institutions, relationships, routes, doctrines, or conflicts rather than serve as trivia.

## 14. Time and autonomous world

The causal scheduler root plus its deterministic host and due-event shards are one scheduling authority. The root is routing/count metadata, not a monolithic copy of every host/event. Resolve exact hosts by hash and time advancement by due-year/day indexes so work cost follows causal work due before the requested horizon.

Do not globally tick every person, team, faction, or place. Exact identity does not imply an exact periodic host.

Routine offscreen scheduling belongs to bounded cohort/organization/team/faction/world hosts. When a small personal roster such as House Tang stores persistent individual-lite profiles, the bounded House review may settle those individuals in one host transaction and then recompute cohort summaries. Wake a heavyweight exact person only when individual causality requires deeper components, such as:

- travel arrival;
- injury recovery;
- appointment/deadline;
- mission boundary;
- command decision;
- training completion;
- family event;
- protected player-facing contact.

Cost should scale with due causal work, not world population or elapsed calendar length.

## 15. Combat

For exact combat, route through combat mechanics and exact participants. For aggregate war, load the participating conflict/front plus `game/data/mechanics/battlefield-operations.json` and `runtime/shinobi_runtime/commands/domains/battlefield.py` when a persistent multi-sector battlefield is active. That layer owns sectors, assignments, redeployment, pressure, and report delay only; exact combat still owns wounds/casualties/outcomes. Load terrain, logistics, intelligence, command, reserves, and named specialists only when individual causality matters.

Persist injuries, death, capture, missing personnel, equipment loss, evidence, and force/population reconciliation.

Never regenerate casualties because a formation template says it should be full strength.

## 16. Adding or changing a feature

A feature is not complete because a rule file describes it.

For a new persistent mechanic, check all applicable layers:

1. static mechanic/rule/data under `game/`;
2. semantic command or autonomous intent in `runtime/`;
3. exact reducer and authority checks;
4. schema/template/blank owner for new durable structures;
5. owner/index/routing contract;
6. scheduler host only if a future causal boundary truly exists;
7. bounded player-safe read if ChatGPT must understand the result;
8. GM Skill change only if presentation/interaction procedure changes;
9. targeted regression;
10. parity/semantic lint updates when the feature changes an architectural invariant.

Do not create a paper feature with no execution path, or an execution path with no player-safe way for ChatGPT to understand it.

## 17. Fast verification

Normal development loop:

```text
python tools/quick_check.py
python tools/test_changed.py <changed paths>
```

The fast gate parses active schemas/contracts, checks semantic architecture, scheduler bounds, authority structure, command surface, and high-signal invariants.

Use subsystem tests only for changed domains. Run deeper long-horizon/replay/soak diagnostics only for a concrete transaction, scheduler, conservation, or persistence question, not as a default bundle.

## 18. Deployment-sensitive files

Production uses one source/campaign branch and a Railway persistent checkout. The current `railway.toml` deploys on every non-state repository change so the checkout remains at the exact production branch HEAD required by transaction preflight. Runtime-generated commits whose changed paths are entirely under `state/**` do not trigger deployment loops. A mixed state/non-state commit still deploys. Remote-ahead state is never silently adopted by a running transaction.

Before changing deployment behavior, read:

- `docs/RUNTIME_SERVICE_DEPLOYMENT.md`
- `references/ooc-dev.md`

Then route only through the deployment authorities that actually matter: `railway.toml`, `.python-version`, `pyproject.toml`, `requirements.txt`, `runtime/shinobi_runtime/bootstrap.py`, `runtime/shinobi_runtime/tx/remote.py`, `runtime/shinobi_runtime/api/`, and `.github/workflows/verify.yml`. Non-state pushes and pull requests run the maintained release gate; deeper replay/soak diagnostics remain deliberate when a changed subsystem warrants them.

A GitHub/source update and a ChatGPT Skill installation are separate deployment targets.

## Integrated macro and environment routes

- `runtime/shinobi_runtime/api/command_discovery.py`: compact semantic-command discovery; retrieve one command contract after intent selection.
- `runtime/shinobi_runtime/environment.py` + `runtime/contracts/environment.json`: deterministic weather and registered physical affordance channels.
- `runtime/shinobi_runtime/security/detection.py`: weather-aware mixed-channel security detection; exact detections remain persistent security truth.
- `runtime/shinobi_runtime/commands/domains/civil_state.py`: governance control basis, including persisted conflict occupation evidence.
- `runtime/shinobi_runtime/commands/domains/combat.py`: exact formation combat and bounded operational-memory updates.
- `runtime/shinobi_runtime/commands/domains/autonomy.py`: objective/readiness/burden-aware formation selection and institutional report propagation.

Operational memory, environment snapshots and indexes are projections/routing aids. They do not supersede exact combat, information, governance, conflict or person owners.
