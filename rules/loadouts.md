# Loadouts

`data/loadouts.json` and routed item registries define reusable loadout contents and item mechanics. A character's current `equipment_loadout_id` selects the baseline issue. `equipment_exceptions` stores owner-specific additions/removals and may store condition/custody overrides when play changes an item. Unique named items use their current item registry.

Do not cache a second fully compiled copy of a loadout inside character state. Resolve the baseline on demand and apply the owner deltas.

Never infer current equipment from a character name, family membership, rank, historical configuration, or this rule file. A loadout, custody, condition, loss, expenditure, or equipment change requires actual access/time/authority where applicable and a canonical state change.


## Unit standard boundary
A unit has exactly one organizational standard loadout. Named exact or individual-lite members may have explicit personal equipment exceptions, but an aggregate subset may not carry a second hidden standard inside the same unit. If only part of an aggregate unit is to receive a different standard, use `SPLIT UNIT` first, conserve the personnel and equipment partition, then refit the child unit from real inventory. A partial refit is represented as incomplete issue against the same standard only when the intended standard is still identical for the whole unit; a deliberately different equipment standard requires a separate unit.

## Standard versus actual issue

A unit's loadout field is its **intended organizational standard**, not a claim that every item is currently present and serviceable. Track temporary shortages, damaged or lost equipment, substitute issue, ammunition depletion, repair backlog, and delayed refit as actual issue/readiness/inventory state. These exceptions do not create a second unit standard. If a durable subset is intentionally assigned a different standard, split that subset into a separate same-troop-type unit before changing its loadout.

Loadout lookup uses `data/loadouts.json` as a small ID-to-shard index. Load only the referenced shard for ordinary play; the index is authority for location, while the shard owns the definition.

## Refit transition

A `SET LOADOUT` order changes what the unit is being refitted toward; it does not conjure equipment. If the new standard cannot be fully issued and familiarized immediately through a lawful time-advancing transaction, keep the current standard and create `refit_state.target_loadout_standard`. Reserve/transfer real inventory, advance fitting/maintenance/familiarization time, and track actual shortages/substitutes through issue state. When refit completes, make the target the current standard and clear the transition. If only a subset is changing standard, split the subset before creating the refit.
