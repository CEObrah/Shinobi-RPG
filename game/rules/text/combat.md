# Deterministic Combat Authority

Combat is resolved from authoritative state before narration. The fixed action order is `game/data/mechanics/core.json`. Body geometry is `game/data/mechanics/body.json`, technique primitives are loaded from the exact `mechanical_base_path` in the technique record, injury is `game/data/mechanics/injury.json`, morale is `game/data/mechanics/morale.json`, and dōjutsu is `game/data/mechanics/dojutsu.json`.

Same authoritative state, same submitted action, and the same recorded deterministic random receipts produce the same mechanical result. Narrative wording has no mechanical authority.

## Exact combat

Exact combat is used when individual identity can materially change the outcome. Each exact participant resolves from that person's persistent capability, condition, location, resources, knowledge, intent, equipment, techniques, and relevant team/doctrine context.

An NPC's combat objective is derived from typed persistent objective/order state and lawful local context. Descriptive prose is not parsed backward into machine orders.

Exact injury, incapacitation, death, capture, resource expenditure, and other persistent consequences update the real person owners and reconcile any force/formation/team representation exactly once.

## Aggregate combat

Formation/battle combat uses aggregate formation participants. A formation's combat capability is derived from its saved component composition and the appropriate registered force capability profiles plus current readiness, morale, cohesion, doctrine/tendency, command, terrain/front, and other registered modifiers.

The caller does not choose a different troop-pool profile to make a formation stronger or weaker. Troop-pool capability profiles are source data, not a second personnel ledger.

Casualties reduce formation personnel and reconcile the force availability/population authorities. Capability-source classification counts are not decremented as though they were physical bodies.

## Exact identities inside aggregate combat

Named people and exact teams inside a formation are subsets of the formation's committed headcount, never bonus personnel. Their exact capability may contribute only a bounded differential relative to the already-counted formation baseline.

When opposing sides contain exact actors whose identities can materially change a local outcome, aggregate combat may reserve those actors from anonymous casualty allocation and create a linked exact child engagement. The exact child must reference the parent combat, consume the reserved identities, and reconcile the parent exactly once.

The same person cannot be harmed once as an anonymous aggregate casualty and again as an exact child-combat participant. A one-sided named reservation without a lawful exact opponent is not allowed; that sector stays aggregate unless another exact encounter is causally established. Campaign time cannot advance while a pending exact-combat reservation remains unresolved.

## Capture and custody

An exact capture identifies a lawful captor and creates pending physical custody at the combat location. Capture does not guess a prison or create a second prisoner body.

Secure detention, transfer, exchange, release, and escape use the custody system and custody-capable places. Aggregate prisoners remain aggregate unless exact identity matters.

## Strategic context

When combat occurs on a registered conflict front, route disruption/control, derived supply pressure, fortification, and evidence-backed occupation may affect or follow the result through their own authorities. A pending exact child engagement does not itself establish strategic control. When the child closes the parent aggregate battle, the final aggregate result preserves the parent battlefield location so strategic evidence remains attributable to the real front. Fronts do not own manpower and cannot manufacture formations or casualties.
