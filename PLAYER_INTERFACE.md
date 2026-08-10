# Shinobi RPG Interface

Natural language is the primary interface. Structured labels are optional conveniences, not a separate gameplay parser.

## Modes

### Normal gameplay / IC

Unlabeled gameplay text or an `IC:` block is interpreted as player intent.

- Every consequential action must go through the Shinobi runtime.
- Narrate only successfully committed results.
- Never estimate, invent, or silently change mechanical state.
- If the runtime rejects or cannot resolve an action, report that accurately.

### OOC

`OOC:` is read-only discussion and inspection.

Use it to discuss mechanics, balance, characters, world state, why an outcome occurred, or hypothetical choices. OOC does not advance time or mutate campaign state unless the player separately gives an explicit gameplay/admin instruction.

### OOC DEV

`OOC DEV:` is software maintenance.

It may inspect runtime/game code, schemas, tests, deployment, or architecture and may implement requested improvements. Code maintenance does not count as a gameplay turn and does not advance world time. Never silently repair campaign truth while changing code. A confirmed state corruption uses an explicit migration or repair transaction.

### Mixed OOC + IC

One message may contain both:

```text
OOC: Why did that happen?

IC: Wei follows the scout into the alley.
```

Resolve blocks in order. OOC remains read-only. Each consequential IC block observes the committed result of earlier IC blocks. If mode ambiguity could cause a write, fail closed rather than guessing.

## Exact teams

Team Fujin, Black Hound, Team Guy, named ANBU/Root cells, and comparable named mission teams use the same exact-team system.

Useful natural-language controls include:

- show team
- form team
- change team membership or roles
- assign or change team leader/deputy
- set/adopt doctrine
- schedule training
- inspect readiness
- assign mission
- attach team to an operation
- stand down or dissolve a temporary team

A team name does not create a bespoke mechanic. Team behavior comes from members, roles, authority, doctrine, training, equipment, readiness, classification, commitments, and mission context.

### TEAM SETUP

```text
TEAM SETUP
Name:
Parent institution:
Purpose/type:
Permanent: yes/no
Leader:
Deputy:
Members:
Roles:
Authority source:
Classification:
Doctrine:
Training emphasis:
Equipment policy:
Communications:
Contingencies:
Home:
Normal assignment:
```

All fields must be lawfully supported by current authority/resources before persistence.

## Formations and forces

A force owns conserved manpower. A formation is an aggregate operational organization drawn from that manpower. Command authority is separate from ownership.

Useful controls include:

- show force
- show formation
- mobilize formation
- split or merge formation
- drill formation
- reconstitute formation
- release/demobilize formation
- assign force command
- attach formation to operation
- return assignment
- show command capacity
- show command tree

### FORMATION SETUP

```text
FORMATION SETUP
Name:
Source force:
Purpose/role:
Personnel allocation:
Commander:
Authority source:
Doctrine:
Training/readiness target:
Equipment standard:
Support:
Communications:
Reserve policy:
Withdrawal rules:
Pursuit rules:
Home:
Operational attachment:
```

Formation creation cannot create manpower. Source force availability, assignment authority, equipment, and population consequences must reconcile.

## Training and doctrine

All exact teams use the same generic team-training mechanics. Differences come from team data, instructors, doctrine, curriculum, facilities, equipment, intensity, fatigue, injuries, and time.

Training may be individual, team, cohort, or institutional depending on representation. Routine cohort/team progression should not require one write per person.

Doctrine may guide priorities, coordination, target selection, communication, fallback behavior, and training emphasis. It does not directly grant physical stats, techniques, equipment, or authority.

## Missions

Missions use one generic mission system regardless of whether the participants are Team Fujin, Black Hound, ANBU, a temporary team, or an aggregate formation.

A mission can contain:

- issuer and authority
- participants
- classification
- objectives
- constraints
- area/location
- deadline
- resources
- intelligence/evidence
- success/failure conditions
- settlement/consequences

The runtime proves tasking authority. A player cannot manufacture an NPC/faction order merely by naming a valid issuer ID.

## Travel

Travel uses the world location graph. Exact-party travel resolves the registered route/local relation, route status/reference duration, the slowest traveler's movement/endurance pace, lawful party/mission context, causal interruptions, and arrival. Weather, encumbrance, covert posture, supply use, and encounter risk do not modify travel unless a command explicitly supplies saved state and a reducer consumes it.

A nearby NPC is not automatically part of the travel party. Companions require voluntary participation, mission/team authority, escort/custody authority, or another persisted lawful basis.

## Strategic war

Large wars are force/formation simulations, not thousands of four-person team objects. A force owns manpower; formations are operational subsets of deployed manpower; exact teams embedded in a formation are identities inside that headcount, never extra bodies.

New formations appear at their force-owned mobilization anchor. They move through registered routes with `formation_movement_resolution`. Persistent wars use `conflict_resolution` for fronts, route disruption/control, bounded supply pressure, fortification, occupation, ceasefire, and conclusion. Named people/teams may wake into exact combat through the aggregate-combat zoom boundary and reconcile into the parent battle exactly once.

Captured exact people or aggregate prisoners use `custody_resolution`; custody capacity and security matter only at registered custody sites.

Eight Gates, jinchuriki transformation, deployed puppets, and active summons use `special_combat_state_resolution` and feed the existing exact-combat resolver rather than creating parallel combat engines.

## Information

Claims and delivery are separate.

- Observation/evidence/inference may create a claim.
- A sender can report/share a claim only if they know it.
- Classification and audience restrictions remain real.
- Rumor, inference, report, and verified fact remain distinguishable.

Secret world truth is not automatically player knowledge.

## Population, recruitment, and materialization

Recruitment works from conserved source population. The player or autonomous institution may request recruitment under lawful authority, but Python determines eligible/accepted results.

Materialization turns an already represented anonymous person into a rostered persistent identity. It does not create a new human or grant free capability/history.

Sword Manor preserves persistent identity for every formal member while routine mechanics remain cohort-backed.

## Health, recovery, relationships, family, and assets

Injuries and recovery persist regardless of which subsystem caused them. Offscreen recovery uses bounded wake events.

Relationships, membership, roles, marriage/family, promises/obligations, and equipment custody are distinct state systems. Narrative statements never substitute for the actual domain mutation.

Useful read-only requests include:

- show person
- show relationship
- show family
- show succession
- show reputation
- show mission
- show team
- show force
- show formation
- show command tree
- explain this result

## Player agency

The runtime and narrator never choose Wei's consequential voluntary intent for him. They may resolve consequences of saved orders/delegation within scope, but not invent allegiance, surrender, promises, spending, irreversible treatment/equipment choices, permanent doctrine, or strategic commitments.
