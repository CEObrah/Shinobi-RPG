# Repository Map

This is the human navigation and update cookbook. It is not campaign truth.

The repository has three authoritative layers:

```text
runtime/   deterministic execution, transactions, scheduling, reducers, APIs

game/      Shinobi rules, schemas, static content, canon/world definitions

state/     mutable truth for this campaign
```

Project-level `tools/`, `tests/`, `docs/`, deployment files, and root instructions support those authorities but do not replace them.

The machine router is `runtime/contracts/repository-map.json`.

## Minimum-context routes

Normal startup reads only:

- `RUNTIME.md`
- `VOICE.md`
- `runtime/contracts/repository-map.json`
- `state/meta.json`
- `state/player.json`
- `state/scene.json`

Then resolve one route shard and the smallest causal owner needed for the action. Known IDs use direct refs. Discovery indexes are for finding unknown IDs, never as gameplay authority.

Useful routing roots:

- owner lookup: `state/index/owners.json`
- rule routing: `runtime/contracts/rule-router.json`
- route shards: `runtime/contracts/repository-routes/`
- causal scheduler: `state/time/causal-scheduler.json`
- templates: `runtime/contracts/template-index.json`
- blank owner skeletons: `runtime/contracts/blank-owner-index.json`
- system mutation contracts: `runtime/contracts/system-contract-index.json`
- narration routing: `runtime/contracts/narration-router.json`

Static definitions normally live under `game/data/`, `game/rules/`, or `game/world/`. Mutable instances live under `state/`.

Examples:

| Need | First authority | Follow only when causal |
|---|---|---|
| Current time/revision | `state/meta.json` | causal scheduler when advancing time |
| Current scene | `state/scene.json` | referenced people/place/mission |
| Exact person | owner index or direct person ref | health, knowledge, equipment, behavior only when needed |
| Rostered person core | `state/person-core/` | cohort/host baseline plus sparse divergence |
| Exact team | `state/team/` | doctrine, members, missions, commitments as required |
| Force | `state/force/` | assignments, formations, population/manpower only as required |
| Formation | `state/formation/` | source force, command grant, battle mechanics |
| Population | `state/population/registry.json` | recruitment/materialization policy |
| Mission | `state/mission/` | participants, objectives, evidence, information |
| Information | information registry route | only lawful claim/evidence/holder refs |
| Location | `game/world/` plus current state place owner | route graph and scene only as needed |
| Technique | `game/data/tech/` known ID | effect/mechanics refs only when resolving use |
| Canon definition | `game/data/canon/` or `game/world/canon-catalog.json` | current state only if timeline/current truth requires it |

Do not load every person, formation, faction, or canon definition for ordinary gameplay.

## Structural write contract

For any owner creation or shape change:

1. Resolve the semantic system first.
2. Load its contract from `runtime/contracts/system-contract-index.json`.
3. Resolve the target schema through `runtime/contracts/template-index.json`.
4. Load the fact-free blank skeleton through `runtime/contracts/blank-owner-index.json` when creating a new owner.
5. Load current authority owners and causal dependencies.
6. Validate authority first, before deriving desired after-images.
7. Apply the deterministic reducer or migration.
8. Validate schema, exact template, references, chronology, conservation, secrecy, and domain invariants.
9. Persist through the transaction coordinator.
10. Read back the committed owners and run the relevant validator.

Do not infer new fields from neighboring examples. Do not create dual writable owners for the same fact.

## Common update matrix

| Change | Primary mutable authority | Typical supporting authority |
|---|---|---|
| Time advance | `state/meta.json`, causal scheduler, due hosts | temporal settlement contract |
| Person health/recovery | exact person or sparse component/host | health rules, scheduler recovery wake |
| Team membership/leadership | exact team owner | person refs, authority/membership contract |
| Team training/doctrine | exact team plus relevant people/cohort | generic `training.team`, doctrine data |
| Mission | mission owner | issuer authority, participants, scheduler, information/history |
| Travel | scene/person/mission journey state as applicable | location graph, authority/custody |
| Recruitment | population registry | authority, recruitment policy, destination membership/force |
| Materialization | population registry plus person-core registry | population/cohort provenance |
| Force command | `state/org/assignments.json` | force owner, grantor authority |
| Formation lifecycle | formation registry plus source force | assignment, manpower/population as causal |
| Aggregate battle | participating formations/forces | command, population, history, information, medical |
| Claim creation | information registry | observation/evidence/mission context |
| Claim delivery | information registry | sender knowledge, recipient, channel |
| Relationship | relationship owner/history | affected people and visibility rules |
| Asset transfer | stock/inventory/asset owner | custody/holder authority |
| Family/succession | family owners | people, relationship edges, authority, history |
| Canon front | canon/front owner and scheduler | static canon definition only as reference |

## Updating an exact team

Team Fujin, Black Hound, Team Guy, named ANBU/Root cells, and comparable named groups use one exact-team system.

Update the team owner, not a team-name-specific runtime path. Team data may differ in:

- members and roles
- leader/deputy
- parent institution
- authority refs
- classification
- doctrine
- training emphasis
- equipment policy
- readiness
- current mission/commitment refs

All exact teams use generic team mechanics. A new exact team should normally require data/state creation, not a new Python subsystem.

## Updating a formation

A formation is an aggregate operational organization derived lawfully from force manpower. It is not an exact social team and does not imply one person file per member.

For formation creation, mobilization, split/merge, drill, reconstitution, release, or deployment:

1. Load the source force and exact formation registry.
2. Load the relevant assignment/command authority.
3. Load `game/data/mechanics/formation-partition.json` or the routed formation mechanics when the operation changes composition.
4. Prove available conserved headcount and capability source.
5. Preserve ownership separately from command authority and operational attachment.
6. Allocate personnel/categories deterministically.
7. Persist doctrine/training/readiness/cohesion/morale without inventing free improvements.
8. Reconcile source force availability and any population/manpower consequences.
9. Emit material history when the change is consequential.

Retired micro-unit, unit-capability, unit-kernel, and tactical-team state trees are not valid authorities.

## Updating an NPC

First determine representation level:

- aggregate population/cohort: no persistent identity needed yet;
- rostered persistent person core: identity is real, routine mechanics come from its host/cohort;
- exact persistent person: individual components are loaded because causality requires them.

Do not upgrade representation merely for prose convenience. Materialization must identify a human already represented in the source population and change anonymous versus rostered representation without increasing physical population.

For a returning cold person:

1. Load the person core and current host/cohort.
2. Catch up macro hosts to current time.
3. Load material events affecting the person.
4. Apply host/cohort routine progression for unchanged intervals.
5. Apply sparse individual divergence chronologically.
6. Resolve bounded deterministic randomness only where its causal result was not already committed.
7. Validate health, affiliation, commitments, family, knowledge, and other material components.
8. Update the person only when there is genuine individual divergence or a new resolution cursor to persist.

A healthy cold person does not need monthly writes.

## Updating population, recruitment, and materialization

`state/population/registry.json` is the physical-population authority.

Recruitment validates the authenticated authority, source pool, eligibility policy, requested capacity, and destination. Python derives the accepted count. The caller does not choose acceptance results.

Rostered persistent identities are represented subsets of the same physical population. Materialization changes representation from anonymous to rostered and records provenance. It does not create a new human, capability, equipment, history, or relationship.

Deaths, captures, missing personnel, releases, migration, retirement, demobilization, and force transfers must conserve the same population truth.

## Updating missions and commitments

Mission creation must prove that the issuer may task the selected participants. A faction or NPC decision uses runtime-internal autonomous mode; a player request cannot impersonate that actor.

Mission objectives use persisted evidence and material events. Autonomous missions start active and progress on later causal reviews rather than appearing completed at creation.

Promises, orders, obligations, meetings, deadlines, recoveries, travel arrivals, and similar future boundaries use persistent commitments/events. Do not hide a material domain mutation behind a generic prose event.

## Updating information and knowledge

Claim creation and delivery are separate.

- Observation, evidence, or lawful inference may create a claim.
- A sender may deliver a claim only if the sender actually knows it.
- Classification, confidence, freshness, evidence lineage, contradictions, audience, and delivery time remain explicit.
- Loading hidden world truth does not grant knowledge to Wei or another NPC.

Player-safe output is knowledge-gated.

## Updating family, marriage, household, and succession

Use the family routes and family system contract. Courtship, proposals, betrothal, marriage, parentage, adoption, guardianship, household changes, divorce, widowhood, inheritance, and succession are material state transitions.

A proposal to Wei may exist without creating player intent. OOC discussion creates no relationship or family state. Derived kinship indexes remain non-authoritative and rebuildable.

## Force ownership, command, and return

Keep separate:

- force owner
- command authority holder
- operational attachment
- temporary assignment
- current location
- equipment custody

A force assignment must prove the grantor, receiving commander, force scope, headcount, timing, and status. Assignment does not transfer ownership unless an explicit lawful ownership transaction says so.

After battle or mission completion, deaths remain dead, captured/missing personnel remain unavailable, wounded personnel enter medical/recovery state, equipment losses persist, and surviving temporary assignments return or remain attached according to their persisted policy.

## Large battle workflow

Large combat must use bounded authoritative formations and their source forces, not caller-invented pseudo-formations and not participant-proportional files.

1. Resolve participating formation refs.
2. Load each formation's persisted personnel, capability distributions, doctrine, training, readiness, cohesion, morale, location, role, and tendencies.
3. Prove command authority and operational attachment.
4. Load terrain, intelligence, support, reserves, logistics, and relevant named specialists.
5. Derive non-player tactical intent from saved state and doctrine. The caller may choose only the authenticated actor's lawful intent.
6. Resolve bounded sectors/exchanges deterministically.
7. Persist casualties, capture/missing, injuries, equipment loss, morale/cohesion/readiness changes, retreat/surrender, and formation strength.
8. Reconcile force availability and population/manpower.
9. Wake exact named actors only where individual causality matters.
10. Create semantic history and information consequences.

The same force may later reconstitute formations from remaining lawful manpower. No automatic regeneration is allowed.

## Time and autonomous world

`state/time/causal-scheduler.json` owns production causal scheduling. There is no frontier/coverage polling fallback.

Faction, institution, team, formation, population, person-continuity, recovery, mission, commitment, and canon-front hosts wake only at registered material boundaries. Routine long-horizon reviews are bundled and compacted.

Autonomous reviews generate lawful internal semantic intents from local state. They may recruit, create/progress missions, mobilize/drill/release formations, train/adopt doctrine, deliver information, run institutional projects, and create successor commitments when their authority/resources permit it.

Autonomy is bounded by owner-local causal neighborhoods. It must not scan all people or all factions.

## Canon and static world content

Static setting definitions belong in `game/`. The current campaign may activate only what is lawful at the bound timeline.

`game/world/canon-catalog.json` provides broad cold definitions for people, teams, clans, villages, towns, daimyo institutions, military facilities, border regions, criminal groups, merchants/civil institutions, medical sites, training grounds, and historical locations. Cold catalog presence does not make something current state or schedule it for review.

Future canon is conditional reference, never forced state.

## Structural authority summary

- `runtime/` may read `game/` and read/write `state/` through semantic transactions.
- `game/` must not depend on current `state/`.
- `state/` contains data, references stable game IDs, and does not own executable mechanics.
- ChatGPT/plugin calls semantic runtime commands and does not patch state directly.
- OOC DEV may modify source/rules, but live-state repair uses an explicit migration or repair transaction.

This repository optimizes bounded causal work, not file-count aesthetics.
