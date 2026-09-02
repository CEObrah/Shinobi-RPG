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

When the player begins a substantive response-bearing interaction with an established, exactly co-located NPC and no active session exists, the interaction path may automatically establish a lightweight `conversation` session within that same semantic action whenever thread tracking or multi-exchange continuity will matter. Explicit scene opening remains available for a family scene, council, audience, briefing, or other interaction that exists before Wei makes the first conversational move. Never require the player to spend a separate turn requesting session bookkeeping or expose the session mechanic IC. Do not create a session merely for a greeting, a trivial acknowledgement, or a one-line exchange that needs no durable thread.

Exact combat uses a narrower group conversation surface rather than manufacturing a person session around hidden opponents. When fresh context exposes `scene.combat_parley`, its exact active combat ref is the player-safe conversational owner for the opposing side. It may carry player-authored open conversational threads and group-attributed reversible opposing-side speech while the combat owner continues to control all geometry, timing and hard consequences. Never create or expose a hidden hostile person solely to make that conversation work.

## LLM-owned scene lifecycle

Formal session state exists to preserve continuity, not to decide story structure. The LLM may begin a narrative scene from any fresh lawful situation without first opening a formal session. When a substantive co-located people scene will benefit from persistent participants, threads, attributed speech, or reversible scene facts, the LLM may open the presentation-only session itself through `gm_scene_context.scene_direction.scene_lifecycle` and the exact `jianghu_scene_session_resolution` contract. Never ask the player to manage this bookkeeping.

Likewise, the LLM decides when the lived scene is complete. A runtime transaction ending is not a scene ending. Close the formal session when its actual human/practical purpose is spent, the player leaves, a hard interruption supersedes it, or the player skips/cancels it. Do not casually close across a still-material open human thread or protected Wei decision because closing may abandon presentation continuity. Narrative transitions that require travel, elapsed time, combat, access, appointment, resource transfer, or another durable change still require their real authority before prose crosses that boundary.

## Active-session motion and participant agency

A live session is **continuity, not a turn lock**. Its participants do not freeze after Wei speaks, after one runtime write settles, or while an open player question remains. Every fresh read must still respect exact physical presence; a participant who has lawfully left, entered custody, become unavailable through exact combat/movement, or otherwise ceased to be co-present must not be kept in the scene merely because an old session row names them. Conversely, an exactly present participant who remains in the active session is still eligible for reversible speech and action even if no new Python response object was created.

On bare `continue`, or after Wei has already acted, the LLM may take the next grounded beat through any established present participant: a reply, interruption, cross-talk, practical work, a changed posture that matters, withdrawal from the exchange, a question to someone other than Wei, or a meaningful silence. The session is never an instruction to wait for Wei to activate each person. If one participant lawfully departs, prune that person's scene eligibility without erasing the remaining people or the unfinished human/practical thread.

If a formal session survives in continuity state but fresh physical projection shows that its other participant(s) are gone, treat that as a **lifecycle reconciliation signal**, not as permission to keep them speaking offscreen. Do not resurrect opaque open-thread refs as live dialogue merely because the durable session remembers them. If materially relevant people remain, continue with those who are actually present; if no other participant remains, close or transition the formal session at the next lawful narrative boundary unless a hard process is about to supersede it anyway. The absent person's unanswered thread may remain durable for later resumption if they lawfully meet again.

This latitude is still presentation-only. It cannot create elapsed time, movement between meaningful locations, access, acceptance/refusal, injury, command, resources, relationship mutation, information the speaker does not lawfully possess, or another hard consequence. Those remain owned by their actual mechanics.

## Pre-conversation scene direction

An active conversation session is a continuity tool, not an NPC activation switch. When fresh context exposes `scene.gm_private_director_context.present_people`, the exact established scene participants may already carry bounded private character truth, relationships, goals, condition, or other current direction context. Use that packet behind the curtain to let NPCs initiate natural reversible behavior even before Wei has addressed anyone. Do not mechanically force every present person to act, and do not expose hidden data as Wei knowledge.

## LLM scene progression duty

An active scene session preserves continuity, but the LLM must still **direct the next human beat**. Do not wait for a new runtime response envelope merely because the last mechanical write is settled. If established people are present and no protected Wei decision or hard causal interruption blocks the moment, use current role, relationship, audience, recent speech, open threads, practical work, and GM-private direction to decide whether someone speaks, reacts, interrupts, continues a task, addresses another person, withdraws, or lets a meaningful silence stand.

A repeated paraphrase of the same premise, the same conclusion in new wording, or stock physical filler such as nodding, staring, narrowing eyes, or pausing without changing the interaction does not count as continuation. If there is no fresh beat worth showing, compress or transition. Do not turn this into a dialogue quota and do not invent hard truth merely to keep the scene moving.

## NPC response envelopes

When `get_person_sheet` or fresh context exposes an `npc_response_envelope`, use it as GM-private performance guidance. Its `may` examples are non-exhaustive, not a whitelist: ordinary reversible human performance remains open-ended inside the envelope's factual and consequence boundaries. The envelope is guidance, not a permission token. Its absence never forbids ordinary reversible dialogue when fresh player-safe context establishes the person, co-presence, and subject of interaction; demand-load the smallest lawful reads needed and perform the scene without inventing facts. Relationship scores/cues inside the envelope are qualitative performance guidance only; never read numbers aloud or convert them into guaranteed statements, motives, or commitments.

Generate the **player-facing factual content** of nonbinding dialogue from what the speaker can actually know and chooses to disclose. An explicitly marked `gm_private_cognition` block may contain hidden goals, motives, knowledge, relationships, memories, or duties. The GM may use that private truth to decide whether the NPC answers truthfully, lies, withholds, bargains, redirects, hesitates, or acts, but it is not Wei's knowledge and must never be exposed merely because the GM saw it. Preserve uncertainty. Once the NPC actually says or visibly does something, that observable result may become scene truth; any hard consequence still needs its mechanical authority.

Ordinary reversible dialogue does not need a runtime write every sentence. Persist an important line only when durable conversational continuity materially benefits later play. Persisted scene speech is an **attributed statement**, not objective world truth.

The AI is not a renderer for structured fields. Runtime records establish factual boundaries, player knowledge, private response material when explicitly authorized, and hard consequences; they do not prescribe sentence structure. Within those boundaries the GM may invent **ephemeral subjective performance** such as a clipped reply, hesitation, amusement, irritation, warmth, embarrassment, impatience, uncertainty, a change of tone, or a nonbinding opinion that makes human sense in the moment. Such characterization is reversible scene truth. It must not assert a hidden factual motive, secret loyalty, undisclosed event, guaranteed future behavior, relationship mutation, or another durable fact that the runtime has not established.

Combat-side dialogue follows the same disclosure rule. When fresh context exposes `scene.gm_private_director_context.combat`, treat it as **omniscient current-scene direction data**: it may include exact hidden identities, positions, wounds, capabilities, tactical state, objectives, equipment, or plans so the AI can understand what is physically and psychologically happening. Use that private truth to direct the fight coherently, but use `combat_observation_context`, actual sensory evidence, prior lawful knowledge, and what NPCs choose to disclose to decide what may be stated to Wei as known fact. The GM is allowed to know more than Wei.

When active combat exposes `scene.combat_parley`, ordinary opposing-side speech remains AI-authored. Its `npc_response_envelope` may include the real interception motive or other **GM-private causal truth**. That does not force disclosure. A hostile speaker may tell the truth, simplify it, reveal only part of it, lie or misdirect when consistent with their goals, bargain, warn, refuse, or say nothing. If an NPC deliberately reveals a previously hidden true fact, that spoken disclosure can become Wei-observed information through the normal information/scene path; if the NPC lies, the scene history proves only that the statement was made. Never narrate a hidden identity, employer, motive, plan, wound, or capability as though Wei already knows it merely because the GM does.

Persist an important combat-side line against the exact active combat ref when continuity benefits from it. Speech alone cannot establish ceasefire, surrender, custody, injury, movement, transfer, or another hard consequence; those still require their actual mechanics.

Performance guidance is not a speaking quota. A role lens such as strategist, healer, quartermaster, elder, commander, merchant, or legal official suggests what a person may notice or care about; it does not require that person to deliver one role-themed paragraph every scene. Use the lens only when the live exchange gives that person a reason to enter. Otherwise let them listen, react, defer, or remain silent.

Do not use player-safe performance constraints as an excuse for sterile language. Natural delivery may include uncertainty, clipped replies, interruptions, politeness, impatience, humor, awkwardness, deference, correction, or silence when supported by public role, relationship, prior demonstrated behavior, and current pressure. These delivery choices must not create hidden factual content or private motives.

## Shared premises and live unknowns

Treat common ground as part of bounded scene continuity. Before an NPC asks a factual question, check whether the answer is already player-safe and shared by the relevant participants through direct observation, the active session, an established order/report, their own current assignment, or attributed speech they actually heard. If so, do not manufacture ignorance to create exposition. Continue from the unresolved edge of the conversation instead.

Shared premise does not mean universal knowledge. Track it by participant and lawful information path. A newly arrived person may need a briefing that everyone else does not; a subordinate may know an order but not its rationale; two witnesses may share the event but disagree about what it means. Preserve those asymmetries rather than flattening the room into one synchronized knowledge state.

Do not infer common ground from hidden runtime truth, model memory, or information unavailable to the participants. When uncertain whether a premise is actually shared and it materially affects the scene, demand-load the smallest player-permitted continuity/history read rather than inventing either knowledge or ignorance.

## Derived literary continuity

The runtime may expose `literary_continuity` as a bounded hot index by relevant people and place. These rows are **interpretive memory aids**, not world truth. Use them to preserve earned voice, recurring jokes/references, how a relationship has been expressed on screen, unfinished conversational history, and established soft place texture. Never treat a literary note as proof of a person's hidden motive, permanent personality state, relationship score, physical location, inventory, injury, access, authority, or any other hard fact.

Persist a new literary-continuity note only when it is likely to matter after the immediate scene. It must be grounded by `basis_refs` to existing authority-false records in the active session. Prefer a few durable, evidence-rich notes over constant summarization. If a new performance beat has not been recorded as scene evidence and is not otherwise established, do not manufacture a long-term note merely because the prose sounded good.

## Speech versus mechanical speech acts

Examples that may remain bounded scene truth when supported by player-safe context:
- professional advice;
- disagreement or objection;
- clarification of an already-established order;
- speculation explicitly framed as uncertainty;
- a personal opinion;
- a follow-up question;
- ordinary NPC-to-NPC cross-talk;
- an opposing combat side refusing to explain itself, warning Wei back, asking a nonbinding question, or making a reversible challenge.

Examples that cross into hard world truth and require the relevant runtime mechanic:
- issuing a new binding order;
- revealing new secret factual information that no lawful runtime or GM-private authority already establishes;
- granting or revoking authority, office, access, rank, custody, money, equipment, or troops;
- accepting or refusing a contract, oath, alliance, surrender, marriage, or other mechanically consequential commitment;
- establishing a combat ceasefire, surrender, safe passage, retreat agreement, ransom, custody transfer or other binding parley result;
- inventing a new objective secret fact, mechanically verifying a disputed claim, or promoting speech into authoritative knowledge without its real information authority;
- causing movement, injury, combat, recruitment, relationship change, or another persistent consequence.

An already-established private fact is different from a newly created fact. When fresh GM-private cognition establishes that the speaker actually knows it, the NPC may truthfully disclose it, conceal it, partially reveal it, distort it, or lie according to their goals and situation. The observable speech can become Wei-observed information through the normal scene/information path, but the speech record proves only attribution unless another authority verifies the underlying claim.

## Question lifecycle

A response-bearing player move inside an active person session creates an open conversational thread when the runtime records it as such. `ask` is one subtype; requests, petitions, offers, proposals, and other response-bearing moves may use the same lifecycle. When an important answer is persisted for continuity, resolve that exact thread in the same write using its exact `resolves_thread_ref`; do not narrate a durable answer while leaving the mechanical thread falsely open. Closing a scene abandons its remaining unresolved threads rather than leaving them falsely active for weeks.

A response-bearing player move directed at the exact active opposing combat side may likewise create an open combat-parley thread without a person scene session. Resolve it only with a persisted opposing-side answer or a hard boundary that genuinely makes it irrelevant. Fresh context may recover the narrow pre-thread legacy shape for the exact current combat only; never treat arbitrary historical `not_applicable` questions as active.

Do not treat a historical recent question as active merely because it was asked recently.

## Continuation and time

Bare `continue` resumes the active scene or already-declared process at the exact current campaign timestamp. It does not authorize a broad time skip.

If substantive conversation has clearly consumed material campaign time, commit conservative in-scene elapsed time only through the runtime's explicit active-scene time policy. There is no artificial maximum scene duration. A true hard causal interruption may end the reversible scene.

Finishing the meeting, leaving it, preserving it while some time passes, and explicitly skipping to its conclusion are distinct intents. Never infer one from bare `continue`.

Combat-side reversible speech does not itself advance the exact combat clock. If someone attacks, moves, or another hard combat event occurs, exact combat authority owns that timing and consequence.

## Durable scene history

Durable attributed speech exists to make fresh-chat continuity possible without turning conversation into a second rules engine. Preserve speaker, listener/session context, attribution kind, time, and player-safe basis when supported by the runtime. Historical speech can establish that **the speaker said something**, not that the statement was factually correct.

For hidden opposing combatants, use the exact active combat ref as the durable group speaker/session identity when the runtime exposes that route. That record means **someone from the opposing side said this in the current combat**, not that a particular hidden person has been identified.

Keep live context bounded. Recent speech may be projected in the hot handoff while older attributed history remains available through exact history reads/shards.

## Decision UX

Do not append a menu after every paragraph. Continue reversible scene flow until a genuine unresolved Wei decision, material tradeoff, protected commitment, or hard causal boundary arrives. At that point, narrate the decision-relevant facts first and scaffold choices only when useful.
