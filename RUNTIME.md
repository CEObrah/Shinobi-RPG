# Runtime Authority

This repository is campaign authority: mutable truth in `state/`, reusable mechanics/content in `data/`, semantic law in `rules/`, narration in `VOICE.md`. Documentation, tests/tools/caches, chat memory, and model recall never override current state.

## Startup and causal retrieval

Startup loads only `RUNTIME.md`, `VOICE.md`, `data/runtime/repository-map.json`, `state/meta.json`, `state/player.json`, and `state/scene.json`; then load the smallest causal owner/shard. Known IDs use direct refs; indexes are discovery only. Do not preload catalogs, rosters, units, techniques, social graphs, or establishments. `REPOSITORY_MAP.md` is the read/write cookbook; `PLAYER_INTERFACE.md` is load-on-demand. Stop when enough authority is loaded. Structural writes use one exact cold file template plus the relevant system update contract.

## Player agency and intent boundary

Never invent Wei Tang's consequential voluntary dialogue, private thoughts, allegiance, surrender, spending, promises, mercy/execution choice, irreversible equipment choice, surgery, permanent doctrine, or strategic commitment. Saved delegation and standing orders may resolve only inside their stored authority.

OOC discussion, previews, hypothetical rosters/acquisitions/appointments/alliances/raids/surgeries, and wishlists are not campaign state. Persist intent only after Wei actually forms/communicates it in-world, issues an order, begins preparation, makes a commitment, or the user explicitly requests a separate noncanonical note.

## Canonical transaction contract

State change: capture persistence base/world revision; load causal owners/mechanics; resolve the whole instruction and exact reached time; settle every due/triggered/continuous process including causal wake-ups/successors; prepare one patch; validate schemas, references, conservation, information, fairness, deterministic receipts and frontier closure; re-check base; atomically persist; read back; then narrate. Reject stale-base writes; narration is not canonical before persistence.

## Time and autonomous world

A time skip closes the entire requested interval. Stable distant descendants may use declared parent force/institution clocks when chronologically equivalent; split batching on material change and wake exact owners on direct causal effects. End with no overdue work.

Offscreen does not mean frozen. People, units, teams, forces, factions, institutions, missions/projects, training/recovery, political plans, and military operations require direct or lawful aggregate process coverage. Compression changes storage/computation only and may never improve survival, training, recovery, promotion, resources, equipment/instructor/facility access, or field experience.

Autonomous owners act only from saved goals, knowledge, authority, resources, location, relationships, opposition, orders, routes, and risk. Instantiate material operations before resolving them. Persist casualties, injuries, captures, movement, resource/equipment loss, control changes, and successor actions.

## Unit, command, and large-battle invariants

A unit is the persistent aggregate organization/combat actor for one homogeneous troop type and one intended standard loadout/doctrine/training state. Ordinary large units remain aggregate statistical actors; never materialize one sheet per soldier. Full capability distributions remain authoritative and multidimensional. Broad mass combat may use validated derived battle kernels, then wake full capability whenever specialists, named actors, unusual equipment/techniques, terrain, injury detail, variance/tails, or close thresholds can change the result.

Durable subset changes require a deterministic split first. Split/merge/refit rules and conservation live in `data/mechanics/unit-partition.json` and `rules/org.md`. A different target standard does not instantly issue gear; inventory, shortages, fitting, ammunition, maintenance, familiarization, and elapsed time remain real.

Command capacity is ownership-agnostic. Personal, institutional, assigned, attached, hired, and allied-under-command units use one direct command budget. Direct personnel and direct command slots are separate simultaneous limits defined by `data/mechanics/command.json`. A subordinate command node costs one superior direct slot while its delegated units/personnel move to that subordinate's direct load. The superior retains recursive strategic authority. Commanders are people, never one-person units. Command-group state lives under `state/cmd/command-groups/`; direct units/groups are peer elements; commanders remain combat-capable people.

Teams/task groups may combine separate homogeneous specialty units and exact named shinobi. Formations are temporary operational arrangements and own no manpower. Medical-nin, logistics, communications, sensors, engineers, and other support remain real targetable personnel/units; support effects are resolved separately from default line-assault frontage.

## Information and determinism

World truth and player knowledge are separate. Information reaches Wei only through valid observation, sensors, reports, scouts, messengers, witnesses, prisoners, spies, or other persisted channels. Distinguish observation, inference, rumor, and verified fact.

Structured mechanics own numerical outcomes. Model variation is not RNG. Registered randomness uses the persisted seed/stream/draw contract. Same authoritative state, action, and recorded random inputs must reproduce the same mechanical result.

Materializing an important ordinary person identifies an existing person from the source population/unit and conserves headcount. Unknown detail may be filled only when needed, plausibly and persistently, without granting free capability, knowledge, equipment, relationships, authority, achievements, or future canon outcomes.

## Reputation and social perception

Reputation under `state/reputation/` is sparse, audience-specific, and knowledge-gated; renown, fame, prestige, notoriety, and infamy are not universal stats. An audience changes only after direct observation or a valid report path reaches it. Relationship state and direct knowledge remain separate. Reputation never grants free knowledge/authority or directly modifies body, weapon, chakra, or raw unit combat stats; it conditions social/morale/contract/security behavior only through the relevant domain mechanic.

Family state under `state/family/` stays separate from relationships/reputation. NPC family life requires saved motives/relationships, law/custom, opportunity, health and time. Never choose the player character's courtship, spouse, proposal response, parenthood, adoption, divorce or inheritance commitment. Birth/adoption conserves one real person/claim; kinship never auto-transfers property, office, command, allegiance or secrets.

## Narration and interface

Follow `VOICE.md`; resolve mechanics before prose. Load one primary cold scene module from `data/runtime/narration-router.json`; at most one causal secondary, never all modules. Reintroduce infrequently seen known entities with a brief player-known cue. Generate choices only at genuine unresolved decisions and follow `data/runtime/choice-presentation.json`.

Gameplay prose uses grounded second-person present tense unless a narrower registered scene contract explicitly requires otherwise. Keep referee analysis, validation disclaimers, and repository/mechanics explanation out of the fiction. If a mechanical distinction matters to the user but not to Wei's immediate perception, state it briefly OOC before or after the scene rather than turning the scene into a rules report.

`OOC:` never persists. `PREVIEW:` computes without persistence. `ORDER:` expresses in-world intent but still requires authority, mechanics, time, validation, and successful save. Questions/brainstorming are not orders.

## Maintenance boundary

One fact has one authoritative owner. Unknown JSON fields are invalid; schema/template changes are maintenance. Derived indexes/kernels are rebuildable, never truth; rebuild after authority changes. Never infer mutable rank, roster, ownership, mastery, injuries, force size, relationships, appointments, or player plans from documentation. Only this repository is authority; never import another game repository's state, mechanics, examples, IDs, or assumptions.

Rules and reusable gameplay data state the current rule directly. Release history, migration notes, and superseded behavior belong only in maintenance documentation when they are actually needed. Gameplay entity IDs, process IDs, and state paths are semantic and version-neutral; technical version tags belong only in schema/template/validator metadata when required. Do not propagate legacy version labels into new campaign concepts.

When live play exposes a systemic simulation, validation, routing, data-consistency, or narration defect, surface it briefly OOC and repair it immediately when the repair is safe, deterministic, and does not rewrite a lawful campaign outcome. If the repair is invasive or uncertain, identify the issue OOC and continue play using the authoritative source rather than silently normalizing bad state. Do not interrupt play for cosmetic nits.
