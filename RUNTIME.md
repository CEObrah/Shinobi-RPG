# Runtime Authority

The repository has three authoritative layers:

- `runtime/` defines how the deterministic simulation executes.
- `game/` defines Shinobi rules, static content, world definitions, schemas, and setting parameters.
- `state/` defines what is true in this campaign now.

No layer may duplicate mutable authority from another. `game/` never depends on current campaign state. `state/` references stable game IDs instead of copying static definitions. Chat memory, narration, documentation, indexes, caches, tests, examples, and model recall never override committed campaign state.

## Startup and causal retrieval

Normal startup loads only `RUNTIME.md`, `VOICE.md`, `runtime/contracts/repository-map.json`, `state/meta.json`, `state/player.json`, and `state/scene.json`. From there, load the smallest causal owner or registered route required by the action.

Known IDs use direct references before discovery indexes. Do not preload people, teams, formations, techniques, relationships, organizations, locations, or canon catalogs. Stop retrieval when enough authority is loaded to resolve the action lawfully.

Before a structural write, resolve the registered template through `runtime/contracts/template-index.json`, the blank owner skeleton through `runtime/contracts/blank-owner-index.json`, and the relevant update contract through `runtime/contracts/system-contract-index.json`. Existing files are examples, not structural authority.

## Interface modes

Unlabeled player intent is normal gameplay unless conversational context clearly establishes OOC or OOC DEV.

- `IC:` is gameplay. Every consequential action goes through the semantic runtime and is narrated only after successful persistence.
- `OOC:` is read-only discussion, inspection, explanation, and hypothetical analysis. It does not advance time or mutate campaign truth.
- `OOC DEV:` is software maintenance. It may inspect or change runtime/game code and run tests, but it does not advance world time and must not silently edit live campaign truth.

A message may contain multiple OOC and IC blocks. Resolve them in order. If mode ambiguity could cause a write, fail closed rather than guessing.

The public gameplay API accepts gameplay-mode commands only. Autonomous commands are runtime-internal. A player-facing caller may not impersonate a faction, NPC, commander, issuer, grantor, or authority holder by changing an envelope field.

## Player agency

Never invent Wei Tang's consequential voluntary dialogue, private thoughts, allegiance, surrender, spending, promises, mercy or execution choice, irreversible equipment choice, surgery, permanent doctrine, or strategic commitment.

Saved delegation and standing orders may resolve only within their persisted scope. NPC and faction decisions arise from their own saved goals, knowledge, authority, doctrine, resources, relationships, and causal state.

## Canonical transaction contract

Every consequential mutation follows one transaction system:

1. Capture campaign revision and current time.
2. Resolve the semantic command and authenticated actor.
3. Load the smallest causal read set.
4. Validate authority, membership, location, knowledge, ownership, custody, and other prerequisites.
5. Catch up due causal hosts and dependencies.
6. Execute deterministic reducers and registered RNG draws.
7. Build the exact read/write/event/RNG manifest.
8. Prepare WAL and staged after-images.
9. Validate schemas, templates, references, conservation, chronology, information boundaries, and command-specific invariants.
10. Atomically persist the declared owners.
11. Commit the repository transaction when Git durability is enabled.
12. Read back and verify the persisted result.
13. Write the transaction receipt.
14. Return a player-safe result packet.
15. Narrate only the committed result.

Duplicate request IDs with identical payloads are idempotent. Reused IDs with changed payloads fail. Stale revisions fail. A failed write is never narrated as completed.

## Time and causal scheduling

`state/time/causal-scheduler.json` is the production scheduling authority. The retired frontier/coverage polling architecture is not authoritative and must not be recreated.

A host owns a coherent causal neighborhood and exposes its resolved/safe horizon and next material wake. Future boundaries such as mission deadlines, commitments, recovery, travel arrival, faction review, team review, institutional review, canon fronts, and other material events are scheduled explicitly or derived from a bounded host.

Normal time advancement must not scan all named people or all factions. Runtime metrics for global person and faction-directory scans remain zero. Cost should scale with due causal work, not with total world identity count or elapsed months.

Offscreen does not mean frozen. Routine progression occurs through cohort, population, organization, faction, team, formation, institution, or continuity hosts. Individual people wake only when causality requires personal divergence or exact resolution.

## Autonomous world

Autonomous actors use the same domain mechanics as player actions. They do not receive a separate permissive simulation path.

Faction, institution, team, formation, and important-person reviews may generate bounded internal semantic intents from persisted goals, authority, knowledge, resources, doctrine, current commitments, opposition, and risk policy. The runtime validates and commits those intents through the normal transaction machinery.

Routine actions should be deterministic policy where possible. Strategic qualitative choices may use bounded reasoning, but Python defines the lawful option space, validates the result, and owns persistence.

Autonomous systems may recruit, materialize lawful identities, form or release formations, create and progress missions, train, adopt doctrine, deliver information, run institutional projects, travel, fight, recover, and create successor commitments only when their persisted authority and resources allow it.

## People, cohorts, and materialization

A person has one stable identity model. "Dormant" is a resolution state, not a different species of person.

Representation levels are:

- aggregate population or cohort,
- rostered persistent identity core,
- exact persistent person with individual components when causality requires them.

A logical person sheet may combine a stable identity core, cohort or host baseline, sparse individual divergence, material events, and optional components such as health, knowledge, relationships, equipment, techniques, career, family, and current commitments.

Routine people are never globally ticked. Age is derived from birth time. Cohort and host progression advances routine capability and life context. Material events such as injury, promotion, marriage, office change, capture, relocation, or unique training wake the relevant person and create persistent divergence.

Materialization identifies an already represented human. It converts anonymous representation into a rostered identity without increasing physical population. It grants no free training, equipment, accomplishments, relationships, authority, or retroactive history.

Sword Manor is identity-preserving: every formal member has a persistent identity, while routine development remains cohort-backed until a person diverges.

## Exact teams

Team Fujin, Black Hound, Team Guy, named ANBU cells, named Root cells, temporary named mission teams, and comparable socially coherent rosters use the same exact-team mechanics.

An exact team owns membership, roles, leadership, parent institution, authority refs, doctrine refs, training model, readiness, current commitments, mission refs, classification, and other team state. Narrative identity never creates a bespoke Python subsystem.

All exact teams use generic team training. Team differences come from doctrine, curriculum, instructors, facilities, intensity, equipment policy, mission history, relationships, and individual member state.

Doctrine affects coordination, priorities, tendencies, communication, fallback behavior, and training emphasis. It never directly grants physical strength or techniques.

## Forces and formations

A force is the persistent manpower and ownership authority. A formation is an aggregate operational organization drawn lawfully from force manpower. Exact teams remain separate from aggregate formations.

Ownership, command authority, operational attachment, temporary assignment, location, and equipment custody are distinct facts.

A command assignment must prove its grantor, holder, force scope, allocated headcount, start/end status, and operational limits. A commander cannot gain troops merely because their ID exists.

Formations can mobilize, drill, reconstitute, split or merge when lawful, deploy, fight, take casualties, release manpower, and return to source authority. Deaths, captures, medical recovery, missing personnel, equipment loss, and returned survivors reconcile against the same conserved force and population truth.

Large battles resolve bounded formations/sectors, not one write per soldier. Formation doctrine, training, readiness, cohesion, morale, command, terrain, intelligence, reserves, support, and capability distributions remain mechanically meaningful. Named specialists wake into exact resolution when their identity can change the outcome.

## Population and recruitment

Population is conserved. Current pools distinguish physical population from representation state so rostered identities are subsets of the same human population, not extra people.

Recruitment takes a source pool, destination, requested slots, policy, and authenticated authority. Python derives eligibility and accepted count from the conserved eligible anonymous population. The caller does not choose the mechanical result.

Transfers, recruitment, death, capture, release, relocation, retirement, demobilization, and materialization must reconcile source and destination totals.

## Missions, commitments, travel, and objectives

Mission creation validates issuer authority and participant tasking authority. Autonomous missions begin as real active missions and progress through later causal reviews rather than appearing already completed.

Commitments such as promises, orders, obligations, deadlines, and scheduled duties are persistent owners/events with status transitions. A prose statement is never a substitute for the domain state it claims changed.

Travel uses the game location graph and validates each traveler's lawful participation or custody basis. Locations are game data, never hardcoded campaign-name branches in the runtime.

Mission, combat, and operation objectives share semantic objective concepts such as capture, protect, escape, delay, reconnoiter, destroy, hold, rescue, extract, observe, and deliver.

## Information and knowledge

World truth, claims, and character knowledge are separate.

Observation or lawful inference creates a claim with provenance and evidence. Delivery transmits an existing claim only if the sender actually knows it. Rumor, report, inference, and verified observation remain distinguishable. Classification and audience rules restrict who can receive or narratively know a claim.

Loading secret world truth does not make it player knowledge. Player-facing packets contain only lawful information.

## Health, relationships, assets, and history

Health is one persistent subsystem regardless of whether damage came from combat, training, travel, disease, surgery, or another cause. Injuries, disabilities, recovery, treatment, and death persist. Recovery can use bounded person-specific wake events without polling healthy dormant people.

Relationships use semantic typed edges. Membership and office/role assignment are organizational facts, not overloaded relationship prose.

Assets distinguish owner, custodian, current holder, location, assignment, and condition. Giving, issuing, borrowing, capturing, consuming, or destroying equipment must update the actual asset/inventory authority.

Current state, transaction receipts, semantic event history, and narrative prose are separate. Material events such as death, major injury, capture, promotion, relationship change, mission result, information delivery, major asset transfer, battle, institutional change, promise/obligation, and canon divergence persist as structured history. Routine repetitive work may be compacted.

## Canon and world content

Static canon/reference definitions live under `game/`. Current campaign truth lives under `state/`.

Historical canon already true at the campaign anchor may seed baseline state. Future canon is conditional pressure, not destiny. A future team, teacher relationship, rank, technique, loyalty, death, achievement, or knowledge state does not become current truth until causally established in this campaign.

Static content may include far more people, teams, clans, villages, towns, daimyo institutions, military facilities, border regions, criminal groups, merchants, medical sites, training grounds, ANBU/Root infrastructure, and historical locations than are currently active. Cold definitions do not enter the hot scheduler merely because they exist.

## Narration

Resolve mechanics first, then narrate through `VOICE.md` and the selected narration module from `runtime/contracts/narration-router.json`. Narration may add bounded sensory or stylistic detail that does not change mechanical truth. It may never invent outcomes, knowledge, resources, relationships, injuries, authority, or history.

## Maintenance boundary

Runtime or game-rule maintenance is OOC DEV and does not count as a gameplay transaction. If a confirmed code bug corrupted campaign truth, repair it through an explicit campaign-repair transaction with provenance rather than silently rewriting history.

Do not recreate retired dual authorities for compatibility. Temporary transition bridges must be one-directional, tested, and removable. The target is a huge cold world with a small hot causal neighborhood.
