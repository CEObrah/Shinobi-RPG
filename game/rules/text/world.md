# Places, Routes, Travel, Missions, Diplomacy, and War

A place exists as world content when its identity, name, parent geography, access, or narrative continuity matters. A place does not gain mutable simulation state merely because it is named.

## Place roles

An **ambient place** is descriptive world content. It may host scenes and narration but owns no mutable staff, finances, inventory, security, damage, schedule, or autonomy unless a consequential mechanic requires that fact.

A **navigable place** has a canonical `place.*` identity and either a strategic route position or a local route anchor. Local places under the same route anchor use local travel rather than requiring a separate strategic edge for every building or street.

A **mechanical site** has one or more registered modules whose values are actually consumed by a reducer. Current site modules are training, medical, and custody. Custody owns the detention/escape security value it consumes. Quantity stock, prices, and money remain separate inventory/economy authorities that may reference a place. There is no generic place-security/infiltration module.

Static architecture such as rooms, dimensions, facility names, zones, and connections belongs in cold site definitions unless the fact changes during play. A room description does not become mutable authority merely because it is detailed.

## Travel

Route state is authoritative in `state/world/routes-and-settlements.json`. Deterministic travel uses `game/data/mechanics/travel.json`.

Exact-party strategic travel requires a registered route between route anchors. Local travel requires origin and destination to share the same route anchor. Party travel time derives from the route/reference duration, route status, and the slowest exact member's registered movement/endurance capability. Time advances through the causal scheduler before arrival is persisted.

A formation moves through `formation_movement_resolution`. Its route and destination must be registered, elapsed time is real campaign time, and embedded exact teams move with the formation. Remote command authority does not imply that the commander is physically present or moves with the formation.

No other formation action may change an existing formation's operational location as a shortcut.

## Missions

Missions require an actual issuer/authority, participants, objectives, causal resources/terms where relevant, and lawful state transitions. Mission settlement moves promised money/assets only when the real payer/holder can fund the declared term.

Routine offscreen missions may settle compactly only while the outcome stays below the registered high-salience wake boundary. Irreversible consequences involving important exact people, unique assets, major territory/force/treasury state, or player-critical commitments require higher-resolution mechanics.

## Strategic conflict

Persistent village wars use a small strategic hierarchy:

```text
Conflict -> Front -> Formations
```

A conflict owns sides, objectives, status, and ceasefire/end consent. A front owns bounded place/route references, formation assignments, route state, fortification, control, and evidence-backed occupations. A front never owns manpower. A front's places/routes must form one connected strategic geography rather than grouping unrelated distant areas under one record.

One side may begin hostilities or break a ceasefire when lawfully authorized. A ceasefire or negotiated end becomes mutual conflict state only after every participating side has recorded lawful consent.

Route control/disruption is persistent front state. A formation's supply pressure is derived from the available registered front routes, their status/disruption, and their controller relative to the formation's force. Hostile route control also increases enemy formation movement time. The caller cannot directly set a favorable supply label.

Fortification modifies the relevant defended front. Battle-backed route control or occupation requires a fully resolved aggregate battle and the recorded controller must be one of that battle's actual victorious conflict sides. Occupation is tracked independently by place so controlling one front location does not overwrite another.

A dedicated universal siege subsystem is unnecessary. Siege consequences are represented through route denial/control, supply pressure, fortification, mission/combat evidence, custody, and occupation state.

## Large wars

A force owns manpower. Formations represent conserved deployed allocations. Large wars resolve formations and bounded sectors rather than creating one exact team or one person owner for every soldier.

Named exact teams or people can exist inside that aggregate representation as exact subsets of already committed headcount. When identity changes a local outcome, combat may wake a linked exact engagement and reconcile it back into the parent aggregate result once.

Logistics remain bounded. Strategic route access can create supported, strained, critical, or cut-off supply pressure. The runtime does not track per-soldier food, ammunition crates, wagons, or ordinary procurement unless a specific mission/asset makes that resource causally important.

## Custody

Exact and aggregate prisoners use persistent custody records. Exact combat capture creates pending custody; secure detention assigns a real custody-capable place and consumes capacity. Transfer requires lawful authority at both the current and receiving custody sites. Exchange, release, and escape preserve the same prisoner identity/count rather than capturing or releasing the body twice.

## World creation boundary

Occupation, route disruption, contracts, military movement, custody, and other consequential changes persist only through registered state authorities. Ordinary settlements may materialize only from lawful world/population authorities; narration does not invent hidden villages, armies, prisoners, or resources.
