# Player Interface and Intent Translation

## Contents

1. Natural language first
2. Interaction modes
3. Dynamic capability rule
4. Read-only questions
5. Consequential actions
6. Teams and doctrine
7. Forces and formations
8. Missions and travel
9. Training and techniques
10. Information, relationships, family, and assets
11. Institutions, economy, recruitment, and projects
12. Strategic war
13. Current environment

## Natural language first

Treat natural language as the primary player interface. Structured labels such as `IC:`, `OOC:`, and `OOC DEV:` are optional controls, not a separate gameplay language.

Never require the player to know command names, JSON payloads, owner IDs, schemas, or backend terms. Translate a clear player statement into the current runtime command yourself.

Examples of player language:

- "Put Hayama in charge of the morning drill."
- "I want Black Hound to train extraction under pressure."
- "Show me who is in Team Fujin and what they still need."
- "Send the formation to reinforce the eastern crossing if I have authority."
- "I want to spend the week learning the technique if nothing interrupts me."

These examples illustrate intent only. They never prove current runtime support.

## Interaction modes

Interpret unlabeled gameplay text as normal IC intent unless the conversation clearly establishes OOC or OOC DEV.

- `IC:`: gameplay. Consequential actions use the runtime and are narrated only after persistence succeeds.
- `OOC:`: read-only discussion, explanation, inspection, planning, and hypotheticals. No time advance or state mutation.
- `OOC DEV:`: software, rules, deployment, Skill, MCP, or repository work. No gameplay time advance and no silent campaign-state mutation.

Resolve mixed blocks in order. If mode ambiguity could cause a write, fail closed and clarify.

## Dynamic capability rule

Treat fresh `get_play_context` command metadata as the only current capability/availability contract. Retrieve the full payload specification for only the selected command with `get_command_contract` immediately before preview.

Never hardcode that a domain is supported or unsupported merely because this reference discusses it. A system concept may exist in the game model while a particular persistent operation is not currently exposed to the player-facing runtime.

When a current semantic command exists, follow its live contract. When no command can represent the intent, explain that limitation OOC instead of simulating a write in narration.

## Read-only questions

Use fresh runtime context and bounded reads for questions such as:

- current situation;
- player condition/resources;
- person or team status;
- relationship or family status;
- mission status;
- known reputation;
- force/formation status;
- command tree or authority;
- feasibility and planning;
- explanation of a committed result.

Do not mutate state merely because a read reveals something that could be repaired or improved.

## Consequential actions

Translate a clear natural-language commitment into one current semantic command at a time.

Keep separate concepts separate. For example:

- assigning a commander is not the same as transferring force ownership;
- issuing equipment is not the same as transferring legal ownership;
- saying someone is married is not a substitute for a family transition;
- describing training is not a substitute for committed development;
- narrating a report is not a substitute for information delivery;
- declaring a mission successful is not a substitute for mission settlement.

Preview, execute, refresh, then narrate.

## Teams and doctrine

Treat Team Fujin, Black Hound, named ANBU/Root cells, temporary mission teams, and comparable exact rosters through generic exact-team concepts rather than team-name-specific rules.

Potential team operations, when the fresh runtime exposes them, include:

- create or dissolve a team;
- change membership or roles;
- assign leader or deputy;
- adopt or change doctrine;
- train the team;
- inspect readiness;
- assign or attach a mission;
- issue or manage equipment;
- delegate bounded authority.

Treat doctrine as coordination, priorities, communication, fallback behavior, and training emphasis. Never treat doctrine as a free grant of physical stats, techniques, equipment, or authority.

## Forces and formations

Keep these concepts distinct:

- force ownership;
- command authority;
- operational attachment;
- current formation composition;
- location;
- equipment custody;
- manpower availability.

A force owns conserved manpower. A formation is an aggregate operational organization drawn lawfully from that manpower.

Potential operations, when exposed by fresh runtime commands, include mobilize, drill, split, merge, move, reconstitute, release, assign command, attach to operations, and return assignments.

Never create extra manpower through narration or command wording.

## Missions and travel

Treat missions generically regardless of participating team or formation.

Mission concepts may include issuer, tasking authority, participants, classification, objectives, constraints, location, deadline, resources, intelligence, evidence, success/failure conditions, and settlement consequences.

The player cannot manufacture an NPC or faction order by naming an issuer. Runtime authority must prove tasking.

Treat travel through the current world/location graph and current party basis. A nearby person is not automatically a travel companion. Participation must come from voluntary intent, mission/team authority, escort/custody authority, or another persisted basis.

Respect hard causal interruptions and partial travel completion. When the current runtime exposes an environment travel factor, that factor is already part of the authoritative route duration; never add a second prose weather delay.

## Training and techniques

Treat training as individual, team, cohort, or institutional according to current representation and runtime support.

Routine team/cohort development should not require one write per person unless exact individual divergence is causal.

Treat technique learning, discovery, mastery, and special combat states only through current mechanics. Do not grant mastery because narration sounded impressive.

## Information, relationships, family, and assets

Keep claims and delivery separate. A sender can deliver only information they actually know, subject to classification and audience rules.

Keep relationships, organizational membership, office, family status, promises/commitments, reputation, and equipment custody as distinct state concepts.

Do not let prose substitute for a mutation.

Never supply Wei's consent for romance, marriage, parenthood, adoption, divorce, family commitments, irreversible treatment, or other protected choices.

## Institutions, economy, recruitment, and projects

Institutional actions depend on current authority, resources, personnel, information, projects, and constraints.

Recruitment must draw from conserved eligible population. Materialization identifies an already represented person and does not create a free human or history.

Purchases, services, contracts, projects, office assignments, and asset transfers must use current semantic mechanics when exposed.

Do not invent money, stock, property, labor, authority, or acceptance outcomes.

## Strategic war

Use force/formation scale for large conflicts. Do not create thousands of exact team/person objects merely to simulate war.

Named exact actors may cross into exact combat when individual causality matters, then reconcile back into the aggregate battle exactly once.

Player-facing strategic intent must still be limited to Wei's lawful authority. NPC/faction strategic intent is runtime-internal autonomy, not a client-selectable impersonation mode.

## Current environment

Fresh `get_play_context` may expose a top-level `environment` object for Wei's exact current place. Treat it as authoritative derived runtime context for that campaign instant: country climate, season, light band, condition, precipitation, wind, temperature band, visibility, ground condition, next transition boundary, and registered mechanical channels.

The environment object is deterministic from authoritative campaign time, world seed, exact place/country, static climate rules, and `runtime/contracts/environment.json`. It is read-only derived authority, not a second mutable save. The same campaign/time/place must reproduce the same conditions.

Current runtime mechanics consume environment only through registered channels. Travel consumes `travel_time_milli`. Combat combines authored place terrain with environment `mobility_milli`, `visibility_milli`, and bounded `hazard_milli`. Preserve authored cover; do not invent weather cover. `fire_spread_milli` and `seasonal_economy_milli` are exposed hooks only until an exact existing rules owner consumes them.

Do not invent scent, tracking, chakra, elemental-technique, patrol, evidence, civilian schedule, office-hours, market, or mission-intelligence effects from weather merely because they sound plausible. Mutable fire, smoke, flood works, staffing, patrols, schedules, intelligence and technique behavior remain with their exact existing owners. A weather/light transition is not automatically a scheduler wake.

Use current environment to make lawful scene consequences visible when relevant, but do not repeat a weather report every paragraph or treat unspecified remote conditions as known.

## Cold world reference

Use `search_world_reference` only when cold setting context materially helps a scene, plan, lookup, or materialization decision. Treat returned entries as static reference truth, never automatic player knowledge or mutable campaign state. Do not call it every turn.
When the result says `results_truncated`, use `next_offset` to page only if the omitted matches are still relevant. Never reinterpret the page size as a limit on the cold world.

## Progressive command discovery

Fresh play context exposes a compact command index grouped by intent domain. Select the one semantic command that matches the player's declared action, then call `get_command_contract` for that command only. Do not restore or reconstruct the full command catalog in every turn; progressive retrieval is the intended context-scaling mechanism.

Current environment may expose physical affordance channels in addition to visible weather: sound masking, track evidence, scent reliability, surface water, conductivity, smoke/gas drift and weather-neutral chakra sensing. These channels are not clues or automatic technique bonuses. Only an exact mechanic that consumes a channel may turn it into a persistent result.
