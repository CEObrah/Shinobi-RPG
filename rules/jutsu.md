# Jutsu / Technique Resolution

A known `method_id` resolves directly from `data/tech/records/<method_id>.json`. Apply `data/tech/defaults.json` first and overlay the exact record; this removes repeated boilerplate without changing any technique. Do not load the whole technique catalog. The record owns identity, cost, startup/timing, geometry, prerequisites, counters and base parameters, and contains exact `effect_profile_path` and `mechanical_base_path` references. Follow only those two records plus the shared resolver/mechanics actually required.

If a user/NPC refers only to an unknown display name or alias, use `data/tech/name-index.json` to discover the method ID. `data/tech/manifest.json` is maintenance/validation routing, not ordinary gameplay context.

Resolution order is legality -> startup -> cost -> geometry -> opposed capacity -> deterministic effect resolver -> protection/injury/status -> recovery. Never invent an effect, range, cost, counter or secondary behavior not present in the exact record, referenced effect profile/primitive or another explicitly referenced special mechanic.

Technique mastery belongs to the actor's repertoire. Technique data never grants a character knowledge of a method. World truth and player knowledge remain separate.
