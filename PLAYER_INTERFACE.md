# Player Interface

This file documents optional control grammar for ChatGPT. It is interface documentation, never fictional campaign state. Natural language is always valid and primary.

## Intent and persistence

- `OOC:` marks discussion/design only and never persists a roster, goal, relationship, request, assignment, acquisition, doctrine change, or other campaign fact.
- Questions, comparisons, hypotheticals, feasibility checks, brainstorming, and design requests are nonpersistent unless the user actually commits Wei to an in-world action.
- A clear natural-language in-world action or instruction uses the normal authority, resource, information, time, legality, validation, and persistence transaction before success is narrated. No special action prefix is required.

## Management commands

`FORM UNIT`, `SPLIT UNIT`, `MERGE UNIT`, `REFIT UNIT`, `FORM FORMATION`, `FORMATION SETUP`, `SET COMMAND`, `DELEGATE COMMAND`, `SHOW COMMAND CAPACITY`, `SET DOCTRINE`, `SET TENDENCIES`, `SET LOADOUT`, `SET TRAINING`, `SET STANDING ORDERS`, `ATTACH UNIT`, `DETACH UNIT`, `SHOW UNIT`, `SHOW FORMATION`, and `SHOW COMMAND` are structured natural-language controls, not a software parser.

### Unit / team setup fields

Target; ownership; permanent yes/no; name; role; commander; deputy; succession; members/source manpower; doctrine; combat tendencies; loadout standard; training plan; communications; contingencies; resource rules; standing orders; home; normal assignment.

Before persistence, resolve real authority, member obligations, availability, injuries, equipment, instructors, facilities, training time, costs, doctrine familiarity, and conflicting assignments. A formation references units and never duplicates their people. Temporary command never transfers ownership.

## Shinobi-specific team types

Use `data/team/team-types.json`. A special-mission cell can support a flexible named roster when institutionally lawful. The presence of that capability does not create a request, roster, or future assignment. ANBU cells require active ANBU authority, not candidacy.

## Structured setup blocks

### TEAM SETUP

```text
TEAM SETUP
Target:
Team type:
Permanent: yes/no
Commander:
Deputy:
Succession:
Members:
Roles:
DOCTRINE:
COMBAT TENDENCIES:
LOADOUT STANDARD:
TRAINING PLAN:
COMMUNICATIONS:
CONTINGENCIES:
RESOURCE RULES:
EXTRACTION:
STANDING ORDERS:
HOME:
NORMAL ASSIGNMENT:
```

### UNIT SETUP

```text
UNIT SETUP
Target/source:
Ownership:
Permanent: yes/no
Name:
Role:
Commander:
Deputy:
Succession:
Members/source manpower:
DOCTRINE:
COMBAT TENDENCIES:
LOADOUT STANDARD:
TRAINING PLAN:
COMMUNICATIONS:
CONTINGENCIES:
RESOURCE RULES:
STANDING ORDERS:
HOME:
NORMAL ASSIGNMENT:
```

### FORMATION SETUP

```text
FORMATION SETUP
Name:
Purpose:
Commander:
Deputy:
Command posture:
Included units:
Ownership: retain source ownership
March order:
Battle deployment:
Scout screen:
Reserve:
Flank structure:
Logistics:
Communications:
Withdrawal rules:
Pursuit rules:
Temporary battle orders:
```

`SHOW UNIT`, `SHOW FORMATION`, and `SHOW COMMAND` are read-only. `SET DOCTRINE`, `SET TENDENCIES`, `SET LOADOUT`, `SET TRAINING`, and `SET STANDING ORDERS` modify only the named layer and do not silently rewrite the others.

## Split, merge, refit, and delegation

- `SPLIT UNIT <unit> INTO ...` partitions one homogeneous unit. Neutral splits preserve the parent represented capability distribution and allocate integer categories deterministically. Selecting veterans/specialists is a separate evidence/time-consuming selection action.
- `MERGE UNIT <units>` merges compatible same-troop-type units after standards/authority are reconciled. Capability moments pool by personnel; cohesion may fall from integration.
- `REFIT UNIT <unit> TO <loadout>` changes the target standard for the entire unit. If only a subset should change, split first. Refit requires actual equipment, custody/transport, fitting/maintenance, ammunition or mounts where relevant, familiarization and elapsed time.
- `DELEGATE COMMAND <units> TO <commander>` creates/updates a subordinate command node. Whole delegated units stop counting against the superior direct-personnel/leaf-unit load and instead consume one subordinate-command slot in the superior.
- `SHOW COMMAND CAPACITY <commander>` reports base rating, evidence-supported capacity modifiers, effective direct-personnel capacity, effective direct-command-slot capacity, current direct load, strategic recursive total, load ratios, band, and which branches should be delegated if overloaded.

Personal, assigned, attached, institutional, allied-under-command and hired troops use **one shared command budget** when they report directly. Ownership never creates a second free capacity ledger. A direct leaf unit costs one slot; a subordinate command node also costs one slot.

## Attachment and return

`SHOW HOME <unit/person>` shows source owner, home unit, current parent, current commander, and return policy.

`ATTACH UNIT` temporarily changes command/operational parent while preserving owner and home unit.

`RETURN UNIT` or `RETURN DETACHMENT` ends the temporary assignment, dissolves player-only temporary formations, restores the home chain, reconciles equipment custody, and triggers source-owner reconstitution. It never restores dead/missing personnel or lost equipment.

NPC/world-owned forces already possess home units, doctrine, training, loadouts, tendencies, standing procedures, and formation templates. Player personal units and raw personnel explicitly given to the player to organize do not receive an invented setup.

## Homogeneous unit rule

A unit contains exactly one troop type. Never build a mixed unit from different troop types. If 5,000 infantry and 2,000 archers are assigned and split in half, create **four** units: 2,500 infantry, 2,500 infantry, 1,000 archers, and 1,000 archers. A single commander may command one infantry unit plus one archer unit through a team/command group or formation, but the two units remain mechanically separate.

For named personal forces, unit membership does not erase individual-lite/exact records. If 50 individually represented personal guards are split into two 25-person units, all 50 people remain persistent named people while each unit receives its own commander, doctrine, training, loadout rules, tendencies, cohesion, and history.

Raw unorganized manpower may exist temporarily as a troop pool/allocation for accounting, but it cannot fight until allocated into one or more homogeneous units.

## Reputation and recognition

`SHOW REPUTATION <subject> [audience]` is read-only. It shows only reputation state the player can lawfully know; hidden audience beliefs remain hidden unless intelligence/reporting reveals them.

`SHOW RENOWN <subject>` summarizes player-known professional/public recognition without inventing a universal fame score. `SHOW PRESTIGE <subject> [audience]` and `SHOW NOTORIETY <subject> [audience]` follow the same knowledge gate.

There is no `SET REPUTATION` command. Reputation changes only through causal events, witnesses, reports, propaganda/counter-propaganda, appointments/honors, contracts, battles, scandals, and other world actions resolved by the reputation mechanics. Hypothetical discussion may estimate possible audiences or risks but changes nothing.

### Command-tree display

`SHOW COMMAND TREE <commander>` is read-only. Display direct troop units and subordinate command groups at the same indentation level. A subordinate command group is labeled `<Commander> Command` (or its saved display name) and expands to its own direct units/nodes beneath it. It counts as one direct command slot in the parent, but it is **not** a troop unit and owns no manpower.

Example:
```text
Wei
├── Archer Unit
├── Infantry Unit
├── Mercenary Unit
└── Jang Command
    ├── Infantry Unit II
    ├── Spear Unit
    └── Archer Unit II
```

The commander named on a command group remains an independently simulated person in combat. If Jang is wounded/killed/captured/cut off, resolve succession and communication; do not delete or regenerate Jang's subordinate units.

## Family and succession controls

Natural language remains primary. Structured aliases are optional:

- `SHOW FAMILY <person>` — read-only unions, household/dependent/parentage/succession refs known to the player.
- `SHOW SUCCESSION <House/clan/title>` — read-only current succession state/known claims.
- Asking about marriage implications, blockers, or likely required authorities is read-only and creates no intent.
- `PROPOSE MARRIAGE TO <person>` is an explicit in-world proposal attempt when the user actually directs Wei to do it; it does not force acceptance.
- `ACCEPT PROPOSAL <id>` / `DECLINE PROPOSAL <id>` are explicit player responses after loading the real pending proposal.
- `END BETROTHAL <id>`, `RENEGOTIATE BETROTHAL <id>`, and other family/household instructions use the same authority/time/persistence contract.

A proposal *to* the player can exist as world state without becoming player intent. OOC discussion never creates courtship, betrothal, marriage, parenthood, adoption or divorce state.
