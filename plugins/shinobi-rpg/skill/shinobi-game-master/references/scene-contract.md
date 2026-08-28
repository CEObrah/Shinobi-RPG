# Scene Contract

This reference defines the boundary between hard simulation truth and fluid human roleplay. The two RPGs may mirror this contract independently, but each repository remains self-contained.

## Three layers of truth

1. **Hard world truth** is runtime-owned. Movement over meaningful distance, elapsed campaign time, injury, death, combat outcomes, money, resources, equipment ownership, training gain, office, formal orders, contracts, promises with mechanical consequences, relationship changes, secrets, custody, formation movement, territory, diplomacy, and other irreversible consequences require runtime authority.
2. **Bounded scene truth** is GM-owned inside fresh runtime limits. Present NPCs may acknowledge, clarify, advise, object, disagree, speculate from lawful evidence, ask follow-up questions, joke, interrupt, discuss established facts, and express nonbinding opinions without a bespoke mechanic for every sentence.
3. **Connective presentation** is freely reversible. Posture, pauses, looking at a map, sitting, standing, handling already-established objects, tone, silence, and a few plausible steps inside the established space do not require a write.

Be permissive about reversible fiction and strict about persistent consequence. **Do not mistake mechanical strictness for presentation strictness.** The absence of a bespoke command for ordinary speech is not a reason to collapse a lawful interaction into a narrator digest. Use bounded scene truth for human exchange and reserve runtime writes for actual consequences.

## Active scene sessions

Treat an active scene session as presentation and continuity state, not a second mechanics engine. In Shinobi, `state/scene.json` is presentation-only; mechanical co-presence must come from the single physical-presence resolver over the exact person plus active custody, exact combat, and route movement owners. It may identify the exact established participants, location, process, purpose, agenda, open conversational threads, and a soft scheduling boundary. It never moves bodies, creates access, grants authority, spends resources, changes relationships, or proves hidden facts.

A planned meeting duration is a soft boundary. Do not dismiss participants merely because the nominal duration has elapsed while Wei is still lawfully present and the scene remains active. The session ends because it is completed, Wei leaves, a hard interruption occurs, the player explicitly skips to the conclusion, or another lawful scene supersedes it.

When the player begins a substantive interaction with an established, exactly co-located NPC and no active session exists, establish a lightweight `conversation` session as the first reversible continuity step whenever question tracking or multi-exchange continuity will matter. Do this as part of carrying the player's declared intent; never require the player to ask for a session or expose the session mechanic IC. Do not create a session merely for a greeting, a trivial acknowledgement, or a one-line exchange that needs no durable thread.

## NPC response envelopes

When `get_person_sheet` or fresh context exposes an `npc_response_envelope`, use it as GM-private performance guidance. Its `may` examples are non-exhaustive, not a whitelist: ordinary reversible human performance remains open-ended inside the envelope's factual and consequence boundaries. The envelope is guidance, not a permission token. Its absence never forbids ordinary reversible dialogue when fresh player-safe context establishes the person, co-presence, and subject of interaction; demand-load the smallest lawful reads needed and perform the scene without inventing facts. Relationship scores/cues inside the envelope are qualitative performance guidance only; never read numbers aloud or convert them into guaranteed statements, motives, or commitments.

Generate the **factual content** of nonbinding dialogue from player-safe facts only. Preserve uncertainty. GM-private relationship cues in the envelope may shape delivery, familiarity, warmth, reserve, deference, or friction, but they are not themselves dialogue facts and must never be quoted as scores or treated as proof of a hidden motive. Do not use private motives, unrelated hidden database truth, external history, or model knowledge as factual dialogue content. The envelope may shape performance, but it never authorizes a new fact.

Ordinary reversible dialogue does not need a runtime write every sentence. Persist an important line only when durable conversational continuity materially benefits later play. Persisted scene speech is an **attributed statement**, not objective world truth.

Performance guidance is not a speaking quota. A role lens such as strategist, healer, quartermaster, elder, commander, merchant, or legal official suggests what a person may notice or care about; it does not require that person to deliver one role-themed paragraph every scene. Use the lens only when the live exchange gives that person a reason to enter. Otherwise let them listen, react, defer, or remain silent.

Do not use player-safe performance constraints as an excuse for sterile language. Natural delivery may include uncertainty, clipped replies, interruptions, politeness, impatience, humor, awkwardness, deference, correction, or silence when supported by public role, relationship, prior demonstrated behavior, and current pressure. These delivery choices must not create hidden factual content or private motives.

## Shared premises and live unknowns

Treat common ground as part of bounded scene continuity. Before an NPC asks a factual question, check whether the answer is already player-safe and shared by the relevant participants through direct observation, the active session, an established order/report, their own current assignment, or attributed speech they actually heard. If so, do not manufacture ignorance to create exposition. Continue from the unresolved edge of the conversation instead.

Shared premise does not mean universal knowledge. Track it by participant and lawful information path. A newly arrived person may need a briefing that everyone else does not; a subordinate may know an order but not its rationale; two witnesses may share the event but disagree about what it means. Preserve those asymmetries rather than flattening the room into one synchronized knowledge state.

Do not infer common ground from hidden runtime truth, model memory, or information unavailable to the participants. When uncertain whether a premise is actually shared and it materially affects the scene, demand-load the smallest player-permitted continuity/history read rather than inventing either knowledge or ignorance.

## Speech versus mechanical speech acts

Examples that may remain bounded scene truth when supported by player-safe context:
- professional advice;
- disagreement or objection;
- clarification of an already-established order;
- speculation explicitly framed as uncertainty;
- a personal opinion;
- a follow-up question;
- ordinary NPC-to-NPC cross-talk.

Examples that cross into hard world truth and require the relevant runtime mechanic:
- issuing a new binding order;
- granting or revoking authority, office, access, rank, custody, money, equipment, or troops;
- accepting or refusing a contract, oath, alliance, surrender, marriage, or other mechanically consequential commitment;
- revealing new secret factual information not already player-visible;
- causing movement, injury, combat, recruitment, relationship change, or another persistent consequence.

## Question lifecycle

A player `ask` inside an active session creates an open conversational thread only when the runtime records it as such. An important persisted answer may resolve that exact thread. Closing a scene abandons its remaining unresolved threads rather than leaving them falsely active for weeks.

Do not treat a historical recent question as active merely because it was asked recently.

## Continuation and time

Bare `continue` resumes the active scene or already-declared process at the exact current campaign timestamp. It does not authorize a broad time skip.

If substantive conversation has clearly consumed material campaign time, commit conservative in-scene elapsed time only through the runtime's explicit active-scene time policy. There is no artificial maximum scene duration. A true hard causal interruption may end the reversible scene.

Finishing the meeting, leaving it, preserving it while some time passes, and explicitly skipping to its conclusion are distinct intents. Never infer one from bare `continue`.

## Durable scene history

Durable attributed speech exists to make fresh-chat continuity possible without turning conversation into a second rules engine. Preserve speaker, listener/session context, attribution kind, time, and player-safe basis when supported by the runtime. Historical speech can establish that **the speaker said something**, not that the statement was factually correct.

Keep live context bounded. Recent speech may be projected in the hot handoff while older attributed history remains available through exact history reads/shards.

## Decision UX

Do not append a menu after every paragraph. Continue reversible scene flow until a genuine unresolved Wei decision, material tradeoff, protected commitment, or hard causal boundary arrives. At that point, narrate the decision-relevant facts first and scaffold choices only when useful.
