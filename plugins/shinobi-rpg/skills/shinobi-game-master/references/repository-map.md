# Repository Map and Update Cookbook

## Contents

1. Authority map
2. Canonical Skill documentation
3. Minimum-context development routes
4. Structural write contract
5. Common update matrix
6. People and materialization
7. Teams, forces, and formations
8. Missions, information, family, and assets
9. Time and autonomous world
10. Large battle workflow
11. Deployment-sensitive paths

## Authority map

Use three authoritative repository layers:

```text
runtime/   deterministic engine, semantic commands, transactions, scheduling, APIs
game/      static rules, schemas, world definitions, mechanics, canon/reference content
state/     mutable campaign truth
```

Use these supporting areas:

```text
plugins/shinobi-rpg/skills/shinobi-game-master/   canonical ChatGPT GM operating package
docs/                                             deployment and human operations documentation
tests/                                            verification, never campaign truth
tools/                                            development/validation utilities
```

Never treat tests, examples, caches, indexes, docs, Skill prose, model memory, or chat history as mutable campaign authority.

## Canonical Skill documentation

Keep ChatGPT GM documentation in one place:

- `plugins/shinobi-rpg/skills/shinobi-game-master/SKILL.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/narration.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/combat.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/scene-playbook.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/choices.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/agency-and-knowledge.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/player-interface.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/world-simulation.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/runtime-architecture.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/repository-map.md`
- `plugins/shinobi-rpg/skills/shinobi-game-master/references/ooc-dev.md`

Do not maintain root-level duplicate voice, interface, runtime, or repository-map manuals.

## Minimum-context development routes

For live gameplay, use the MCP API rather than manually reading repository state.

For OOC DEV source work, begin from the smallest relevant authority:

- runtime architecture: `references/runtime-architecture.md` plus the relevant `runtime/` module;
- repository location/update question: this file plus `runtime/contracts/repository-map.json`;
- current campaign state: exact owner route under `state/`, only when development diagnosis genuinely requires raw state;
- rule/mechanic: `runtime/contracts/rule-router.json` then the routed game/runtime authority;
- owner lookup: `state/index/owners.json` and only the matching owner-index shard;
- structural template: `runtime/contracts/template-index.json`;
- blank creation skeleton: `runtime/contracts/blank-owner-index.json`;
- mutation contract: `runtime/contracts/system-contract-index.json`;
- narration routing: `runtime/contracts/narration-router.json`;
- causal scheduling: `state/time/causal-scheduler.json`.

Known IDs should route directly before discovery. Stop retrieval once enough authority is loaded.

## Structural write contract

For any owner creation, schema shape change, or structural migration:

1. resolve the semantic system;
2. load its system update contract;
3. resolve the target schema/template;
4. load the fact-free blank skeleton for new owners;
5. load current authority owners and causal dependencies;
6. validate authority before deriving desired after-images;
7. apply deterministic reducer or explicit migration;
8. validate schema, template, references, chronology, conservation, secrecy, and domain invariants;
9. persist through the transaction coordinator or approved migration path;
10. read back committed owners and run relevant validators.

Never infer new fields from neighboring examples. Never create dual writable owners for the same fact.

## Common update matrix

| Change | Primary mutable authority | Typical supporting authority |
|---|---|---|
| Time advance | `state/meta.json`, causal scheduler, due hosts | temporal settlement contract |
| Person health/recovery | exact person or sparse person component/host | health rules, recovery wake |
| Team membership/leadership | exact team owner | person refs, authority/membership contract |
| Team training/doctrine | exact team plus causal people/cohort | generic training/doctrine mechanics |
| Mission | mission owner | issuer authority, participants, scheduler, information/history |
| Travel | scene/person/mission journey state as applicable | location graph, authority/custody |
| Recruitment | population registry | authority, recruitment policy, destination |
| Materialization | population registry plus person-core registry | population/cohort provenance |
| Force command | organization assignments | force owner, grantor authority |
| Formation lifecycle | formation registry plus source force | assignment, manpower/population |
| Aggregate battle | participating formations/forces | command, population, history, information, medical |
| Claim creation | information registry | observation/evidence/mission context |
| Claim delivery | information registry | sender knowledge, audience, channel |
| Relationship | relationship owner/history | affected people and visibility rules |
| Asset transfer | stock/inventory/asset owner | custody/holder authority |
| Family/succession | family owners | people, relationships, authority, history |
| Canon front | canon/front owner and scheduler | static canon definition |

## People and materialization

Determine representation before editing or reasoning about a person:

- aggregate/cohort;
- rostered persistent identity core;
- exact persistent person.

Do not upgrade representation merely for prose convenience.

For a returning cold person:

1. load identity core and current host/cohort;
2. catch up relevant macro host to current time;
3. load material events affecting the person;
4. apply routine host/cohort progression for unchanged intervals;
5. apply sparse individual divergence chronologically;
6. resolve bounded randomness only where not already committed;
7. validate health, affiliation, commitments, family, knowledge, and other material components;
8. persist only genuine divergence or required resolution cursor changes.

Materialization identifies an already represented human and must conserve physical population.

## Teams, forces, and formations

Use generic exact-team mechanics for named teams. Do not create team-name-specific runtime branches without a true domain difference.

For formation operations:

1. load source force and exact formation registry;
2. load command/assignment authority;
3. load routed formation mechanics when composition changes;
4. prove available conserved headcount and capability source;
5. keep ownership separate from command and attachment;
6. allocate composition deterministically;
7. persist doctrine/training/readiness/cohesion/morale without free improvements;
8. reconcile source force and population consequences;
9. emit material history when consequential.

Do not recreate retired micro-unit authorities or pseudo-formations.

## Missions, information, family, and assets

Mission creation must prove issuer and tasking authority. Autonomous missions use runtime-internal authority rather than player impersonation.

Keep claims and delivery separate. Preserve classification, confidence, freshness, evidence lineage, contradictions, audience, and delivery time where represented.

Use family system contracts for courtship, proposals, betrothal, marriage, parentage, adoption, guardianship, household changes, divorce, widowhood, inheritance, and succession. Never create Wei's consent through migration or prose.

Keep force ownership, command, operational attachment, location, and equipment custody separate. Keep asset owner, custodian, holder, assignment, location, and condition separate when represented.

## Time and autonomous world

Treat the causal scheduler as production time authority. Do not recreate global frontier/coverage polling.

Faction, institution, team, formation, population, person-continuity, recovery, mission, commitment, and canon-front hosts should wake only at registered material boundaries.

Autonomous reviews generate lawful internal intents from local causal state and use normal domain mechanics.

Do not scan all people or all factions for ordinary time advancement.

## Large battle workflow

For large combat:

1. resolve participating formation refs;
2. load persisted personnel distributions, doctrine, training, readiness, cohesion, morale, location, role, and tendencies;
3. prove command authority and attachment;
4. load causal terrain, intelligence, support, reserves, logistics, and named specialists;
5. derive non-player tactical intent from saved state/doctrine;
6. resolve bounded sectors/exchanges;
7. persist casualties, capture/missing, injuries, equipment loss, cohesion/morale/readiness change, retreat/surrender, and formation strength;
8. reconcile force availability and population/manpower;
9. wake exact named actors only where individual causality matters;
10. create semantic history and information consequences.

Do not regenerate lost manpower automatically.

## Deployment-sensitive paths

Railway deployment watches runtime/game source and deployment configuration, not ordinary state-only gameplay commits. Preserve that separation.

Before changing deployment-sensitive files, read `docs/RUNTIME_SERVICE_DEPLOYMENT.md` and `references/ooc-dev.md`.

Skill-only documentation changes should not require the live game runtime to redeploy unless runtime contracts/source were changed at the same time.
