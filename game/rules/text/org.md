# Organization, Teams, Forces, and Formations

## Core model

The simulation uses four player-relevant organizational concepts:

- A **person** is one exact persistent individual when identity matters.
- A **team** is a small exact roster of named people who train, travel, and operate as a socially coherent group.
- A **force** is the persistent manpower and ownership authority for a military or security organization.
- A **formation** is an aggregate operational body assembled from a force for deployment, battle, security, or another military task.

Cohorts, scheduler hosts, components, kernels, and similar terms are engine implementation details. They do not create additional gameplay organization levels.

## Exact teams

A team owns exact membership, leadership, roles, assignment authority, doctrine, training setup, readiness, and current commitments. Every member is a real person reference. Team identity never creates manpower.

Teams are used when the exact roster matters socially or tactically: academy teams, named mission teams, ANBU cells, Root cells, temporary task forces, household teams, and comparable groups.

A team may be attached to a larger formation through its `current_assignment_ref`. Attachment changes operational context, not exact membership or population accounting. The formation does not duplicate the team roster.

## Forces

A force owns conserved manpower. Its `availability` categories form the conserved physical personnel partition and answer how many people are ready, mobilizable, deployed, recovering, captured, missing, or otherwise unavailable. Troop pools describe capability/source envelopes used to derive aggregate composition; they are not a second disjoint population partition and are not required to sum to force total.

A force is not itself a battlefield participant. It allocates lawful manpower to formations and receives surviving personnel back when formations are released or reconstituted.

Force total is conserved across all availability categories. Recruitment, death, capture, release, retirement, transfer, and other lawful population changes are the only ways the total changes.

## Formations

A formation represents a conserved allocation from exactly one force. It may contain multiple aggregate components and may have named commanders, specialists, or exact teams attached when their identities matter.

A formation owns operational state such as represented personnel, authorized personnel target, role and current activity summary, doctrine and training references, readiness, cohesion, morale, location, command state, and aggregate composition and condition.

A formation does not create people. Every person represented by a formation is part of the deployed partition of its source force, so explicit formation personnel can never exceed deployed personnel. A force may also have deployed people who remain abstract because no current scene or operation requires their formation to be materialized. When a theater is fully represented, such as a 10,000-person field army in a village war, its explicit formations may account for the entire deployed commitment in that theater.

Formations may mobilize, drill, reconstitute, split, merge, move, fight, take casualties, and release manpower when authority and resources allow. After mobilization, changing a formation's operational location requires registered route/time movement; another lifecycle action cannot rewrite its location as a shortcut.

## Large-war representation

Large battles resolve formations and bounded sectors, not one character sheet or one four-person team object per soldier.

Ordinary squads and fire teams inside a mass formation are implicit in the formation's composition, doctrine, training, command, and capability distributions. They are not persisted as exact teams unless their roster has independent social or narrative continuity.

Named exact teams can participate inside a formation without adding extra personnel. If a 500-person formation contains a four-person named team, the formation still represents 500 people. The named four are an exact subset of that 500 and the remaining 496 may stay aggregate.

When a named team or specialist can materially change a local outcome, the battle resolver may wake those exact people for detailed resolution in the relevant sector. Exact names are metadata over already committed formation personnel and never add bodies. Evacuation, capture, death, and lawful recovery reconcile the exact identity overlay, formation headcount, force availability, and physical population exactly once. The rest of the formation remains aggregate.

A village may therefore deploy 10,000 shinobi without constructing 2,500 exact four-person team records. Exact teams are created only where exact identity matters.

## Split and merge

Split and merge are deterministic conservation operations governed by `game/data/mechanics/formation-partition.json`.

A neutral split changes organization, not personnel quality. Requested child headcounts must sum exactly to the parent headcount. Conserved integer categories are partitioned by deterministic largest remainder. Capability distributions are inherited or recomputed without rerolling for favorable children.

Explicitly concentrating veterans, specialists, stronger members, better equipment, or another selected subgroup requires lawful selection criteria, evidence, authority, and time. Named people are never duplicated or silently moved.

A merge conserves personnel and categorical state. Continuous aggregate state is personnel-weighted. Cohesion cannot increase merely because two formations merged and may take an integration penalty until joint training restores familiarity.

## Command authority

Ownership, command authority, operational attachment, temporary assignment, location, and equipment custody are separate facts.

A commander may command a formation only through a lawful assignment or ownership authority. Command authority does not transfer force ownership and does not create additional personnel.

Delegating a subordinate command changes who directly controls which operational body. It does not duplicate the subordinate formation in the superior's headcount.

## Equipment and support

Equipment issue, medical support, sensor support, logistics, and other specialist capabilities remain real resources. A formation's intended standard does not instantly issue equipment. Refit and replenishment consume stock and time through their own lawful mechanics.

Named specialists retain exact personal capability when woken. Aggregate support personnel remain part of formation personnel and are not added as bonus bodies.

## Player agency

The player may organize only people and formations over which the player holds lawful authority. OOC discussion and previews do not create teams, formations, doctrine, assignments, or manpower.
