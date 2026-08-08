# Repository Map

Navigation and update cookbook for the live Shinobi campaign. This file is not campaign truth. Mutable facts live in `state/`; reusable mechanics/content in `data/`; semantic law in `rules/`. The machine router is `data/runtime/repository-map.json`.

## Startup

Load only `RUNTIME.md`, `VOICE.md`, `data/runtime/repository-map.json`, `state/meta.json`, `state/player.json`, and `state/scene.json`. Do not preload this document, whole catalogs, or directory listings during ordinary play.

## Retrieval discipline

1. Identify the exact causal question/action and affected owner IDs.
2. If an ID is known, use its direct owner/record path. Use an index only for discovery.
3. Load one action-specific domain from `data/runtime/rule-router.json`, not several broad domains "just in case".
4. Follow references only when the referenced fact can change legality or outcome.
5. Stop loading when enough authoritative context exists to resolve the action.
6. Derived indexes and battle kernels are caches, never current truth.
7. For writes: update authoritative owners first, create conservation/development receipts, rebuild dependent caches/indexes, validate, read back, then narrate.

## Minimum-context routes

| Need | First load | Deepen only when |
|---|---|---|
| Current campaign/scene | startup owners | causal action requires another owner |
| Exact/lite NPC | owner index prefix -> exact owner | behavior, health, relationship, training, equipment, or command matters |
| Unit status | exact `state/unit/<id>.json` | full capability only for detailed resolution; kernel for broad battle |
| Unit capability | unit `stats_ref` | detailed skills/variance/tails materially matter |
| Unit battle | unit `battle_kernel_ref` | close threshold, specialist, named actor, terrain/technique/equipment asymmetry wakes full capability |
| Force/home structure | `state/org/home-establishments.json` -> one owner shard | a specific cold unit series materializes or changes |
| Team | exact team owner | referenced member/unit, doctrine, command, training only as needed |
| Technique with known ID | `data/tech/records/<method_id>.json` | follow only its effect and primitive refs |
| Technique discovery | `data/tech/index.json` | only when method ID is unknown |
| Loadout with known ID | `data/loadout-records/<loadout_id>.json` | inventory/issue/custody only for actual refit/use |
| Dōjutsu mechanics | `dojutsu_mechanics` router domain | ocular custody only if a physical eye matters |
| Ocular custody | ocular index -> one owner shard/stockpile | stockpile batch materializes only when an exact stored eye is selected |
| Relationships/knowledge | source-person edge shard | global edge index only for incoming/unknown lookup |
| Reputation/recognition | `state/reputation/index.json` -> subject -> one relevant audience profile | event history only for provenance/propagation/dispute |
| Family/marriage/household | `state/family/index.json#person_index` -> exact referenced record | relationship, health, House/clan/law/property, succession or reputation only when causal |
| Time skip | frontier + `time_settlement` domain | only due process policy/owners and causal wake-ups |
| Player interface | `PLAYER_INTERFACE.md` | only when structured commands/control grammar matter |

## Structural write contract

Every gameplay-created or structurally edited JSON owner has one registered cold structural template. Templates control **shape**, not facts. System contracts control **authority and write order**, not results. Neither is gameplay state.

For any create/structural edit:

1. Identify the gameplay system and load exactly its `data/runtime/system-contracts/<system>.json` through `data/runtime/system-contract-index.json`.
2. Identify the target schema ID. Resolve only the matching first-character shard through `data/runtime/template-index.json`, then load that exact template.
3. Load the authoritative owner(s) named by the system contract. Never start from a cache/index/example.
4. Check creation prerequisites, authority, conservation, elapsed time, knowledge/agency boundaries, and causal evidence.
5. Write only registered keys/types. Unknown fields are invalid. Optional facts use registered optional fields or a registered referenced profile; never invent a new JSON key during play.
6. Dynamic/open maps may add new stable IDs only where the template explicitly permits them; each value still follows the wildcard value contract.
7. Persist authority first, receipts/history second, rebuild affected derived indexes/kernels third.
8. Run the validators required by the system contract plus `tools/test_templates.py`, then read back the changed owners before narration.
9. If the existing template cannot express a genuinely new mechanic, stop the gameplay write. Revise schema + template + system contract as maintenance first, validate, then resume.

`data/runtime/repository-map.json` is deliberately a small hot root router. Most routes live in cold `data/runtime/repository-routes/*.json` shards; use `route_index` to load **one** route shard. `data/runtime/directory-map.json` is cold maintenance/navigation metadata and is not ordinary gameplay context.

### NPC and force deepening

- **NPC:** load the exact owner first. For sustained dialogue or personality-sensitive autonomous action, use the routed behavior-depth source only when the owner lacks sufficient inline behavior. Persist new behavior only from supported evidence.
- **Unit:** load organization/identity first; load `stats_ref` only when capability matters and `battle_kernel_ref` for broad mass-combat phases. Do not load every unit in the parent force.
- **Command:** load the commander/person + exact command-group nodes + only direct child units needed to the requested depth. Command groups own hierarchy, not manpower.
- **Training/development:** load only the target person/unit, its active training contract, required instructors/facilities/equipment/health, and elapsed-time process.
- **Family/reputation/social:** start from the subject/person index and load only materially relevant sparse records. Historical ledgers stay cold unless provenance matters.
- **Narration:** `VOICE.md` is the hot persona. Select one scene module through `data/runtime/narration-router.json`; a second module is allowed only if it is independently causal.

- For an exact character without sufficient inline behavior, route through `data/people/behavior-profile-index.json` and load exactly one `data/people/behavior-profiles/<owner_id>.json` before sustained dialogue, recurring command, or personality-sensitive high-stakes autonomy. The profile constrains invention; it is not personality authority.

## Unit and command authority

A **unit** is one homogeneous troop type with one intended standard loadout, doctrine, and training state. The unit is also the aggregate large-combat actor. Ordinary soldiers are represented by multidimensional distributions, not thousands of character sheets. Never replace that distribution with one scalar "power" score.

A **team/task group** may combine separate homogeneous units and named shinobi. A **formation** is a temporary operational arrangement and owns no manpower. A **commander** is a person, never a one-person unit.

Command uses one ownership-agnostic budget. Personal, assigned, attached, hired, allied-under-command, and institutional units all count together if they report directly. `data/mechanics/command.json` owns direct-personnel capacity, direct-command-slot capacity, modifiers, overload penalties, and delegation. A subordinate command node costs one superior slot while delegated descendants move onto the subordinate's direct budget.

## Updating a unit

### Split / merge

Load the exact unit(s), `rules/org.md`, and `data/mechanics/unit-partition.json`. Neutral splits preserve represented distributions. Integer categories use deterministic allocation. Selection of veterans/specialists requires a real selection process. Split/merge must conserve people, injuries, equipment, experience, history, and capability lineage and write a transaction receipt.

### Refit / doctrine / training

Durable subset differences require separate units. If 1,000 of a 5,000-person same-type unit need another standard loadout, split 1,000 first, then refit that child unit. Changing a target standard does not instantly issue equipment. Actual stock, custody, transport, fitting, ammunition, maintenance, familiarization, and time remain real.

Changing doctrine does not change training automatically. Changing training does not change equipment automatically. Temporary orders do not rewrite permanent doctrine.

### Development

Unit improvement requires actual training/field experience, elapsed time, instructors/facilities/equipment, health/recovery, replacements, and receipts. Update authoritative capability first, then rebuild the derived battle kernel. Representation compression never grants better development.

## Updating an NPC

1. Load exact/lite owner and only the relevant behavior/health/knowledge/relationship/career/training owners.
2. Never create personality filler merely to complete a template.
3. Skills improve only from registered training/experience and time.
4. Injuries/fatigue go to health/body authority; relationships/knowledge to their dedicated registries; audience reputation to `state/reputation/` only after valid evidence delivery; office/command/assignment to institutional authorities.
5. A persistent behavior change requires repeated or decisive causal evidence.
6. Canon/current-world facts must respect the campaign date. Never back-project future achievements.

## Updating reputation and recognition

1. Load the physical/social event that actually happened before creating reputation consequences.
2. Load `reputation_event` mechanics and identify real witnesses/report origin. OOC discussion and repository omniscience are not witnesses.
3. Create one cold reputation event only if perception can materially change. Record signals, evidence quality, visibility, and report lineage.
4. Propagate through existing messenger/intelligence/institution/market/faction routes at real travel/report time. Do not create a second reputation-only global clock.
5. Update only delivered subject+audience profiles. Relationship and direct personal knowledge remain separate authorities.
6. Reputation may condition access, expectations, recruitment, contracts, morale, caution, patronage, security, or political attention only where the relevant domain rule makes perception causal. It never directly buffs combat stats or grants legal authority/knowledge.
7. Current profile is the hot truth. Historical reputation events stay cold unless explaining why an audience believes something or continuing an undelivered report.

For an NPC reaction, first determine the observer's actual audience membership/information access, then load only that audience profile. Do not load every audience that knows the subject.

## Updating family, marriage, household, and succession

1. Start from `state/family/index.json` and the involved people. Load only referenced family records.
2. Keep institutional family status separate from relationship feelings, direct knowledge, reputation, health, property, office and command.
3. A real NPC proposal may be persisted as pending; never persist player acceptance/rejection, spouse choice, parenthood or divorce intent until the player character actually acts in-world.
4. Courtship/proposal/betrothal/marriage/adoption/guardianship/dissolution transactions use `rules/family.md` + `data/mechanics/family.json`, exact elapsed time, source refs and deterministic receipts.
5. Birth creates exactly one real child person, then parentage/household/dependent state; health resolves in its own authority. No free body or duplicate population.
6. Marriage/kinship never auto-transfer property, title, allegiance, clan/House membership, office or command. Load the exact law/House/clan/property/succession owner when those consequences are material.
7. On death, settle widowhood/dependents/guardianship before inheritance/succession. Preserve disputes and prior unions instead of deleting history.
8. Rebuild `state/family/index.json` and `state/family/kinship-index.json` after family authority changes. Kinship index is routing only.
9. Reputation/prestige effects occur only after the family event becomes known to the relevant audience through a valid information route.

## Large battle workflow

1. Identify actual participating units, exact command tree, terrain, orders, support, readiness, morale/cohesion, supply, and information picture.
2. Use unit battle kernels for broad ordinary exchanges. This is the fast path that preserves the original aggregate-force design.
3. Wake full unit capability only where detailed distribution, specialists, named actors, jutsu, equipment, terrain, injuries, or close thresholds can change the result.
4. Resolve command overload separately from soldier capability. Command penalties affect order latency, synchronization, reserve response, and control, never magically weaken bodies/weapons.
5. Service/combat-support effects are separate from default line frontage. Medical-nin remain real shinobi and may defend/fight when actually committed.
6. Persist casualties, captures, fatigue, chakra/resource expenditure, ammunition/tools, position, morale/cohesion, command disruption, and successor actions to real owners.

## Time and autonomous progression

`state/time/frontier.json` owns scheduling. `data/runtime/process-policies.json` owns reusable process policy, not mutable state. Stable distant descendants may advance under declared parent force/institution clocks only when chronologically equivalent. Causal change wakes exact owners. A time skip ends with no overdue work.

## Ocular storage

`state/medical/ocular-registry.json` is a routing index. Living/implanted eyes live in owner shards. A homogeneous unreferenced stockpile may be represented as a deterministic conserved batch with unique virtual ordinals. Selecting/transferring an eye first materializes that exact ordinal and removes it from the batch. No eye may regenerate because of compression.

## What not to load by default

Do not preload all characters, units, unit capabilities, techniques, eyes, relationships, doctrine/training catalogs, establishments, or world institutions. Do not expand ordinary force members into people. Do not treat maintenance files, indexes, caches, or documentation as state.

## Common update matrix

Use this as the default write cookbook. Resolve the exact owner ID first; then load only the named route, one structural template, and one system contract.

- Character facts/behavior/goals: `exact_characters` or `character_behavior_profile` -> `characters` contract -> exact character template. Relationship, knowledge, reputation, family, health and office/command remain separate authorities.
- Character training/development: `npc_development` -> `training_development` contract -> character/person plus causal training/health/process owners. Never grant free growth.
- Unit split/merge/refit: `unit_partition` / `unit_refit` -> `units` plus `inventory_loadouts` contracts -> unit + capability/kernel refs + transaction receipt. Rebuild derived unit indexes/kernels after authority.
- Command/delegation: `command_tree` / `command_group` -> `command` contract -> command-group/person records plus directly controlled units. Commanders remain exact people.
- Team/formation: `teams` / `formation` -> `teams_formations` contract. Teams group homogeneous units; formations are operation/battle arrangements and own no manpower.
- Relationship/knowledge: `relationships_knowledge` -> `relationships_knowledge` contract. Shared affiliation is not a personal relationship.
- Reputation/recognition: `reputation_subject` / `recognition_check` / `reputation_event` -> `reputation` contract. Update only audiences reached by valid evidence propagation.
- Family/kinship/succession: `family_person` / `family_kinship` / `family_transition` / `family_succession` -> `family` contract. Legal/kinship status is separate from feelings and reputation.
- Time/autonomous progression: `time_frontier` / `process_coverage` -> `time_process` contract. Settle all due work through the reached time; do not load every descendant owner when aggregate coverage is valid.
- Technique/special system: `technique_known_id` or the relevant special route -> `techniques_special` contract. Load only the referenced technique/effect/primitive closure.

After any write: authority first -> transaction/event receipt where required -> derived indexes/caches -> validator stack -> read back the changed authority before narration.

## Isolation

Only this repository is authority. Never import another game repository's data, examples, IDs, mechanics, or state.

## Command-group read/write routing

- **Inspect command tree:** load the commander person/command record, `state/cmd/command-groups/index.json`, then only the referenced command-group records and direct unit/person owners needed for the requested depth.
- **Create/delegate:** validate authority and both commanders' capacities; write/update the affected command-group records; update the derived command-group index; then validate/read back.
- **Commander in combat:** load the commander person's exact/lite combat owner separately from the command group. The command group owns hierarchy only and never substitutes for the person or a troop unit.
- **Succession:** on commander incapacity, load saved deputy/successor refs and standing doctrine. If a superior absorbs the child units directly, recompute that superior's personnel and direct-slot load immediately.
- **Display:** direct troop units and subordinate command groups are peer command elements. A subordinate command group counts as one direct slot and may be shown as `<Commander> Command` with its children nested underneath.


## Population / recruitment

Use the `population_recruitment` route. Load `state/population/registry.json` plus only the one destination owner. Recruitment is aggregate for mass forces. Sword Manor may materialize only sparse proven standouts under its saved personal-force model. Do not enumerate person files to process ordinary recruitment or cohort training.
