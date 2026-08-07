# Organization and Unit Law

## Core hierarchy

`person / unorganized manpower -> homogeneous unit -> team or command group -> formation / operation -> force / institution`.

A **unit** is the only persistent aggregate mass-combat organization. A unit has one troop type and one intended organizational standard loadout. It owns headcount, aggregate capability, doctrine, training, tendencies, cohesion, morale, readiness, condition, experience/history, home chain, current assignment and equipment issue state. Large ordinary units resolve from aggregate statistics under `data/mechanics/unit-resolution.json`; never create one character sheet per ordinary member.

A **team/task group** may combine multiple homogeneous unit types. A **commander** is a separate person who can command multiple units or subordinate command nodes. A **formation** is a temporary operation/battle arrangement and owns no manpower. A raw manpower pool is accounting only and cannot fight.

## Hard unit boundary

One unit means one troop type and one intended standard loadout. If only a subset should receive a different durable loadout, doctrine, training plan, commander, assignment or other persistent standard, `SPLIT UNIT` first. Example: changing the standard for 10 of 40 general shinobi requires a 30-person child/remainder and a 10-person child, both still general-shinobi units.

Split/merge is a canonical transaction governed by `data/mechanics/unit-partition.json`. Neutral splits preserve the parent represented capability distribution; integer categories use deterministic largest-remainder allocation. Explicitly concentrating veterans, specialists, stronger members, better equipment, or other quality requires a real selection/reallocation action with criteria, authority, evidence and time. Conserve people, named-member claims, equipment, supplies, injuries, fatigue, experience, morale/cohesion inputs and history. Do not silently place the best personnel in one child unless an explicit selection action with authority/time actually does so. Compatible same-type units may merge only after persistent standards are reconciled; integration may temporarily reduce cohesion/familiarity.

`SET LOADOUT` changes the target standard for the whole unit. It does not instantly issue gear. A refit consumes real stock, transport/handling, maintenance/fitting, ammunition and time. Temporary shortages, damage or substitutes belong to issue/readiness state and do not create a second standard loadout. Named exact/individual-lite members may hold personal equipment exceptions without changing the unit standard.

## Command hierarchy

Use `data/mechanics/command.json`. Every commander has one ownership-agnostic direct budget: direct personnel plus direct command slots. Personal and assigned units share it. A direct leaf unit costs one slot; a subordinate command node costs one slot. Whole units delegated to a subordinate stop counting against the superior direct personnel/leaf-unit load and instead count on the subordinate, while the superior retains one subordinate-node slot. Strategic authority over descendants is not direct control.

Splitting never grants free power: more directly controlled children consume more command slots. Staff/communications, doctrine familiarity, terrain/dispersion, information quality, health and fatigue modify command capacity/latency, not subordinate body stats.

## Ownership, attachment, return

Temporary command changes authority, not ownership. Intact assigned units retain owner, home unit/establishment, history, doctrine identity and equipment custody unless a lawful transfer says otherwise. Raw personnel specifically assigned for player organization may be formed into legal homogeneous units without changing source ownership unless authority grants transfer.

On return, dissolve temporary player command layers and restore the source home chain. Never restore factory condition: casualties, injuries, experience, lawful promotions, relationships, cohesion/morale changes, equipment loss and history persist. The owner then reconstitutes using real replacements, stock, instructors/facilities and time.

## Support

Use `data/mechanics/support.json`. Medical-nin and specialist support are real targetable units. They do not automatically add line assault frontage merely because attached. Named specialists retain their exact personal capability.

## Formation and large battle

Formation templates describe arrangements a force knows; active formation state exists only when instantiated for a real operation. Formations reference units and never own their personnel. Large battles may vectorize materially equivalent units for computation, but all losses, fatigue, ammunition and other consequences settle back to real unit IDs. Wake full unit capability whenever specialist use, variance, unusual equipment/terrain, named actors or a close threshold could change the result.

## Player agency

Institutional/world forces may have predefined home units. The player character's own unorganized personal force remains unorganized until the player orders a structure. OOC/preview discussion never creates a unit, doctrine, assignment or plan.

## Command groups as direct elements

A **command group** is a persistent/operational command-only node, not a troop unit. It owns no manpower. It points to one real commander person, zero or more directly controlled homogeneous units, optional directly controlled named people, and zero or more subordinate command groups. Its authoritative state lives under `state/cmd/command-groups/` only after an actual appointment/delegation creates it.

For span-of-command, a superior's direct elements are **direct troop units + direct subordinate command groups**. Example: `Archer Unit`, `Infantry Unit`, `Mercenary Unit`, and `Jang Command` consume four direct slots. The units nested under `Jang Command` do not also consume the superior's direct slots or personnel budget; they consume Jang's. The superior still retains recursive strategic authority where the appointment/order grants it.

The commander of a command group remains a real combat-capable person. If present, that person can move, fight, use personal equipment/techniques, be wounded, killed, captured, isolated, exhausted, or routed. Personal combat never gets averaged into the unit's ordinary-soldier capability. Command loss triggers deputy/succession/standing-doctrine handling; directly absorbing orphaned child units increases the superior's direct load immediately.
