# Player Interface

Natural language is the player interface. The player describes what Tang Wei tries to do normally. ChatGPT interprets the action first, realizes reversible scene behavior directly, and consults the Shinobi Runtime mechanic catalog only for contested or durable consequences. Do not make the player write backend commands, payloads, IDs, revisions, or phase transitions.

## Interaction modes

Treat player messages as one of these modes, possibly in sequence within the same message:
- **IC/gameplay**: act in the world and resolve through the runtime;
- **read-only OOC**: inspect/explain current state without mutation or time advancement;
- **OOC DEV**: edit source, rules, data, Skill, tests, deployment, or maintenance systems;
- **mixed**: resolve blocks in the order the player supplied them without confusing development work with campaign action.

## Bare continuation is presentation, not elapsed-time consent

The ordinary words `continue`, `go on`, `keep going`, or equivalent do not automatically authorize an arbitrary time skip.

Resolve bare continuation in this order:
1. if the previous player-declared action still has an obvious reversible/procedural continuation, carry it forward;
2. if a standing wait/training/travel/combat policy already authorizes elapsed time, continue that policy until its next lawful boundary;
3. if a genuine unresolved player decision is already waiting, do not choose for Wei; re-establish the decision and scaffold grounded options if needed;
4. if nothing in current intent authorizes elapsed time, continue the scene presentation or hand back the actual decision rather than inventing a wait.

A runtime resume hint is mechanical capability, not natural-language authorization. Do not interpret bare `continue` as permission to accept an offer, choose dialogue, select a destination, spend money, or execute another protected commitment.

## Recovering an interrupted committed transition

OOC discussion, development work, tool failure, context compaction, or a fresh conversation can interrupt **presentation** after a gameplay transaction has already committed. Never replace the missing transition with a terminal state digest merely because the original execute receipt is no longer in conversational context.

After the required fresh `get_play_context`, inspect `object_reads.current_transition_ref` when all of the following are true:
- current state clearly reflects a just-committed gameplay transition whose chronology matters to the resumed scene;
- the transition receipt/events are not already available in the current conversation;
- replaying how the current revision was reached is necessary to narrate or safely continue the player's intent.

The advertised ref is `transition:current`. It exposes only the immutable receipt for the **current campaign revision**, not arbitrary historical turns. If its object has `next_object_ref`, follow those exact returned refs until enough/all ordered events have been recovered for the scene. Treat refreshed play context as current-state authority and the recovered receipt as transition evidence. Never let the receipt override newer state, and never use transition recovery to probe hidden futures or browse old history.

If the recovered receipt includes an exact `command` and `result_metadata.continuation_required` is true, that command is durable evidence of the player's immediately preceding standing intent. Carry it only to the degree the normal agency/delegation rules permit. If a legacy receipt has `command_recoverable: false`, do not reconstruct protected target, Qi, poison, restraint, spending, dialogue, or other voluntary details from inference; narrate what is recoverable and return control when continuation would otherwise require guessing.

Do not replay a transition that has already been shown in the current conversation. This recovery path exists for interrupted/re-entered presentation. In combat, narrate the recovered ordered events as the fight that produced the current wounds, deaths, positions, fatigue, and pressure before using any status accounting. In social/travel/institutional scenes, likewise restore the human/causal beat before summarizing its terminal state.

A stale presentation handoff is not automatically a new protected decision. If recovered current-revision evidence and refreshed state show that Wei already acted on that handoff, continue from the actual current frontier rather than re-presenting the original menu merely because `state/scene.json` still remembers the contact that began the scene.

## The player does not write commands

**The LLM is the command orchestrator.** It understands the player's natural-language objective first, then uses fresh mechanic-family discovery as a consequence toolkit. It may sequence several exact runtime operations beneath one continuing player intent and one continuous narrated scene, refreshing context between writes and stopping only for a real new decision or hard causal boundary. Do not force one player turn per command and do not expose command selection as gameplay. Scene start/end is not derived from command count.

Use `get_play_context` first. Its compact mechanic families describe available consequence resolvers, not available fictional actions. After semantic interpretation shows that a hard consequence needs adjudication, call `get_command_family` for one family and then `get_command_contract` for the one selected operation.

Use `get_person_sheet` and `inspect_game_object` only for exact player-permitted people/owners when compact context is insufficient. Never guess hidden IDs or repository paths.

For a consequential write, use preview -> exact immutable command/attestation -> execute -> refreshed context. Do not ask the player to supply the command payload.


## Action versus consequence

Accept the player's attempted action before asking which mechanics apply. A missing `punch`, `put_bowl_on_head`, `interrupt_meeting`, or similarly bespoke command is irrelevant. If Wei says `I punch him`, the semantic meaning implicates exact combat. If he places an established bowl on someone's head and nobody meaningfully resists, that may remain scene realization. If he smashes the same bowl into someone's skull, combat, improvised contact, anatomy/injury, witnesses, and related consequences become relevant because the **meaning** changed. When a mundane prop may later cross into combat, persist its first observed `object_state` with the bounded form/material/condition descriptor. When Wei later takes up or uses that same object, persist a second `object_state` that cites the first fact and repeats the exact descriptor. Only that matching two-stage fact may be promoted into transient combat physics. Never reuse one mundane object's provenance to classify a different object.

For compound declarations such as `I draw my sword, attack him, and tell everyone else to stay back`, preserve all stated components. Resolve the attack mechanically, realize the concurrent speech naturally, and let witnesses exercise independent agency. Never translate the declaration into only the first backend operation and silently discard the rest.

The semantic representation may contain actor, target, method, goal, manner, constraints, sequencing, spoken words, Qi/poison choices actually stated, and player-declared intent. It must never contain `the enemy fails to dodge`, `the guard agrees`, `the attackers are terrified`, `the troops obey`, or another outcome owned by mechanics or NPC agency.

## Player action versus world response

A committed player interaction proves Wei acted. It does not by itself prove a **hard external result** such as binding acceptance/refusal, granted access, committed resources, movement, contract/oath, surrender, relationship change, or another durable consequence.

That rule is not a gag order on a co-located NPC. If fresh physical/scene authority establishes a real conversational counterpart, ordinary reversible response belongs to the GM/NPC cognition layer and may happen immediately without a bespoke response command. Use attributed speech history only when continuity matters. When a substantive targeted `speak` is persisted for cross-turn continuity, it is response-bearing by default; do not require an extra backend flag just to keep the human reply live. If the interaction is remote, access is not established, or the requested answer itself would create/reveal hard state, then wait for the appropriate runtime/cognition/information authority rather than inventing it. An explicitly final/non-response-bearing line may suppress the thread; defaults must never override that semantic intent.


## Live scene sessions and attributed speech

When fresh context exposes an active scene session, treat it as reversible conversational continuity. `jianghu_interaction_action` records Wei's side of an exchange. `jianghu_scene_session_resolution` may open/close an explicit scene, persist an important NPC attributed statement that is already safe to realize from player-visible facts, or record a salient reversible scene-local fact for fresh-chat continuity. Persisted speech and scene facts have no mechanical-consequence authority. Speech may resolve a generic open conversational thread, including a question, request, petition, offer, proposal, or other response-bearing move in that active session. A scene fact is only an observed continuity record such as local object placement, room-level positioning, a visible reaction, or a shared premise; it cannot substitute for combat, inventory, money, travel, authority, relationship, or other hard-state mechanics.

The LLM owns whether that persistence session should exist. A narrative scene can start, continue, transition, or end without one. When continuity across commands or fresh contexts is useful, use `gm_scene_context.scene_direction.scene_lifecycle` to route through the interaction family, load `jianghu_scene_session_resolution`, and open or close the presentation session automatically. The player never needs to say `open scene` or `close scene`. Do not let an open session keep a spent scene talking, and do not let a completed runtime command terminate a still-living scene.

Do not persist every sentence or gesture. Ordinary connective dialogue and disposable staging remain narration. Persist only scene details likely to matter later. An NPC may disclose an already-existing private fact the speaker lawfully knows; persist the attributed statement and use the information mechanic when a durable epistemic record is needed. Use the relevant mechanical command when speech or action would create a binding order, acceptance/refusal, transfer, movement, injury, relationship change, oath, mission disposition, an invented secret, or another hard consequence.

## Multi-step intent

A player may express an objective that logically spans several commands, such as:
- accept an escort assignment, prepare, depart, travel, escort, return, and report;
- call a House meeting, present a plan, appoint a commander, outfit a team, dispatch them, and later review the AAR;
- travel to a town, find a known contact, speak with them, and make a request;
- train under an established routine for several days unless interrupted.

Treat the player's stated objective as standing intent across obvious reversible substeps. Preserve scene-only components and execute required consequential mechanics sequentially, refreshing context after writes. Stop only when a new consequential choice, hard causal boundary, or unsupported **hard consequence** appears. A missing bespoke command for ordinary dialogue or reversible fiction is never itself a stop condition.

Do not make the player restate the same objective at every phase.

## Institutional play

Natural requests such as `call a House meeting`, `take the escort assignment`, `put X in command`, `send these members`, `negotiate a truce`, `exchange these prisoners`, `ask our ally for help`, or `review the mission report` map to institutional mission command plus the existing physical owners it orchestrates.

Binding diplomacy by a player without the required House office must carry exact council-approved mission authorization. Load the current `mission:` owner when phase, briefing, authorization, commander, assignments, or AAR matters.

Do not make the player specify backend phase transitions. Resolve only genuine choices such as accepting/declining an assignment, major plan, spending, doctrine, treaty terms, commander selection where multiple lawful choices matter, and career offers. Routine mustering, scheduler progression, return bookkeeping, and settlement may proceed from an already authorized plan.

## Player-facing mechanics

Explain mechanics in player language first. Backend names are OOC implementation details, not the interface.

Useful game concepts include combat doctrine, techniques, weapons, Qi, poison, fatigue, injury, medicine, training focus, team roles, local/strategic travel, services, contracts, missions, tournaments, deployments, infrastructure, recruitment, production, equipment, factions, diplomacy, family, custody, investigation, and causal time advancement.

When the player asks `Can I do X?`, answer from fresh current authority and distinguish:
- **available now**;
- **available after a known prerequisite/state change**;
- **conceptually valid but implementation-blocked**;
- **not supported by current game rules/state**.

Do not hide an implementation defect by pretending the action is fictionally impossible.

## Freedom of action

Choice menus are suggestions, never a whitelist. Natural-language free action remains available unless the runtime or current state truly constrains it.

If the player already declared an action, attempt that action before presenting alternatives. If it cannot be executed, explain the narrow reason and preserve the player's underlying intent where possible.

## Information and investigation

GM-private omniscient truth may be used to understand what is actually happening and to direct NPCs coherently. Do not answer a hidden-world question **to Wei** merely because the GM knows the answer. Wei-facing knowledge must still arrive through observation, disclosure, investigation, lawful records, witnesses, scouts, messengers, rumors, inference from visible evidence, or another supported information path.

If Wei does not know, say what he does know and what lawful actions could reduce uncertainty.
