# Scene Craft

Use with `narration.md` for every substantive co-located people interaction: family discussions, House councils, audiences, negotiations, briefings, mission reports, camp conversations, training reviews, medical treatment, investigations, escort handoffs, arguments, reunions, interrogations, tavern conversations, and other beats where interaction itself carries meaning.

## Interaction-first default

Do not turn a live interaction into an administrative digest merely because the runtime exposes structured facts. When the people, their knowledge, roles, relationships, stakes, or disagreement matter, render the exchange as a lived scene first. Let structured summary support the scene afterward when useful, never substitute for the interaction itself.

Begin with an observable human beat rather than a prose digest. A useful default is:

`space/action anchor -> first person acts or speaks -> another person reacts/cross-talks -> practical clarification/disagreement -> concise bridge -> material consequence -> genuine player decision if one exists`

When two or more relevant named NPCs are physically present and current context supports substantive speech, normally let dialogue, reaction, questioning, and cross-talk carry the decisive information before the narrator compresses anything. The same principle applies to a one-on-one interaction when the relationship, information exchange, disagreement, emotion, or decision is itself important.

Do not force everyone to speak and do not inflate trivial transactions. A purchase, greeting, direction request, routine gate check, room confirmation, or similar exchange may stay compact unless a meaningful social, informational, or consequential beat emerges. Use the people whose role, knowledge, stake, or relationship makes their contribution material.

## Hard anti-briefing gate

The most dangerous failure mode is **polished briefing prose**: a dateline, generic weather or accounting, a sequence of correct rank/contract/knowledge distinctions, people who verbalize those distinctions, then a large numbered menu. That shape is mechanically careful and narratively dead. Reject it.

For a House, mission, sect, escort, or Jianghu planning scene, prefer a shape such as:

`dateline -> concrete human/practical action or first line -> another person reacts -> one or two decision-relevant facts emerge through the exchange -> practical activity continues -> pressure turns or sharpens -> Wei receives the actual decision`

Do not make the first several paragraphs perform orientation that the scene itself can perform. Do not state `that distinction matters`, `the question is`, `not because X but because Y`, or similar analyst narration merely to interpret the data. Make the distinction matter through who bears the obligation, who is willing to go, which route is dangerous, whose reputation is exposed, what treatment costs, what a promise requires, or what another person is willing to risk.

A scene is allowed to leave true background information unstated until it becomes causal. The goal is not to empty the context packet into prose. The goal is to choose what the camera is on.

## Scene progression, not prose motion

**Decide the next beat before composing sentences.** Use the live scene context to identify one thing that will actually change: who speaks or acts, what practical work advances, what pressure shifts, what information becomes lawfully available, what consequence lands, or what genuine player decision is reached. Then write toward that change. Do not discover the point of the response by repeating the setup until the prose happens to stop.

When `scene_direction.beat_candidates` is present, use it only as a compact causal-priority hint. A candidate marked by an open human thread or recent exchange may deserve attention before an unrelated bystander, and private-direction availability means the GM has better backstage characterization for that established person. It is **not** a speaking queue, turn order, mandatory actor list, or instruction about what anyone must say. Current pressure, relationships, knowledge, practical work, and scene rhythm still determine the beat.

A live scene must not confuse **more sentences** with **more happening**. Once the current beat is established, advance it. Normally one of these should change before the response hands control back: the conversation, a person's observable behavior, the relationship pressure being expressed, the practical process under way, the immediate physical situation, the information lawfully available to Wei, or the causal situation.

Present NPCs are agents, not answer boxes. They may start the next reversible beat themselves when the scene gives them a reason. A father may answer another family member before returning to Wei; an officer may cut across a colleague; a healer may continue working while speaking; a companion may notice something established in the room or on the road; a subordinate may ask the person who actually owns the problem. The LLM should infer this momentary performance from established character, relationship, audience, pressure, and knowledge rather than waiting for a Python field that says `speak now`.

Do not write a second paragraph whose only job is to say the first paragraph again. Do not rotate through decorative nods, looks, narrowed eyes, pauses, silence, map-gazing, tea handling, or equivalent stock business merely to create motion. Such beats are useful only when they carry a specific current meaning or cause the interaction to move.

If no meaningful reversible human beat exists, **compress**. If a standing process already has an obvious continuation, continue it. If a genuine consequential choice has landed, hand that choice to the player. Do not manufacture an argument, encounter, secret, or speech just to satisfy pacing.

## AI-native directing loop

This game deliberately uses the LLM for the part a deterministic engine is bad at: **moment-to-moment human direction**. Do not ask the runtime to choose a speaker or pre-author a response. Use runtime truth as constraints and evidence, then direct the scene yourself.

Before every substantive live-scene response, perform this loop internally:

1. **Locate the last real beat.** What did Wei just say or do? What did another person just do? What practical action is already under way? Do not narrate that same beat again merely to orient yourself.
2. **Identify the live pressure.** Who wants something now, who owns the practical problem, what relationship is being expressed, what uncertainty is live, or what process has an obvious reversible next step?
3. **Choose the actor by reason, not list order.** Use present-person identity, role, current duty, relationship/history, audience, recent speech, GM-private cognition, and lawful knowledge. The first person in the cast is not automatically the next speaker.
4. **Stage one or a few material beats.** Let someone answer, interrupt, continue working, address another NPC, refuse to engage, correct a misunderstanding, make a joke that fits them, shift the practical task, or otherwise change the lived moment. Use as many beats as the scene needs, not a fixed quota.
5. **Let people interact laterally.** In groups, one NPC may answer another, a subordinate may speak to the officer who owns the issue, family members may react to each other, and practical work may continue around Wei. Do not make every line pass through the player character.
6. **Bridge mechanics only where needed.** Translate a committed outcome into what people perceive and do about it. Do not stop because a command completed and do not restate backend facts that nobody needs to say aloud.
7. **Stop at a real handoff.** End when an uninterrupted reversible beat finishes, a lawful external wait begins, a practical transition is complete, or a genuine protected player decision has landed. If obvious scene life remains, keep going.

### Reject the non-response draft

Before sending, compare the response against `immediate_continuity` and the prior visible beat. Reject and rewrite the draft if its main contribution is any of the following:

- restating what Wei just said or did;
- repeating the same conclusion in different words;
- describing everyone as watching, waiting, nodding, pausing, looking at a map, handling a cup, or falling silent without a specific current meaning;
- turning structured state into narrator exposition while established people remain inert;
- ending with everyone waiting for Wei when no protected decision is actually pending;
- asking a question whose answer is already shared premise merely to expose state.

A corrective rewrite does **not** need more words. Usually it needs a different beat: a person reacts, someone else enters the exchange, the practical task advances, the scene compresses, or a genuine decision is allowed to land.

### Finish scenes instead of looping them

Forward motion includes **ending the current dramatic unit when it is spent**. Once the question has been answered, the practical task is complete, the immediate disagreement has reached its natural boundary, or the people no longer have a grounded reason to keep this exchange going, stop mining the same setup for more lines. Let people return to established work, disperse, or compress into the next already-authorized purpose. A still-open runtime scene session is continuity metadata, not a command to keep talking.

Before drafting a substantive turn, make one backstage lifecycle choice: **start**, **continue**, **transition**, or **end** the current narrative scene. This is a directing judgment, not a menu shown to the player. The choice comes from lived pressure and causal continuity, never from whether a backend command just returned. A formal scene session may be opened/closed to preserve continuity, but narrative scene shape remains the LLM's responsibility.

Do not create a fresh topic, conflict, secret, visitor, or encounter merely to avoid ending a quiet scene. The next scene comes from lawful standing intent, committed world pressure, travel, time, reports, relationships, duties, or the player.

### Contested-action boundary

The LLM directs reversible human performance; it does not replace the physical resolver. In active exact combat, pursuit, battle contact, dangerous treatment, or another contested process, speech, cries, hesitation, visible emotion, and nonconsequential human reaction may be staged when plausible, but attacks, defenses, movement that changes geometry, injury, capture, treatment success, resource expenditure, and elapsed mechanical time must come from committed mechanics.

### Presence should produce behavior, not automatic speech

When exact present-person context exists, treat those people as capable of acting without player activation. If substantive dialogue would depend on missing exact characterization, role, knowledge, relationship, injury, or duty, demand-load the smallest sufficient person/object read rather than making the NPC generic or mute.

Do not invent speech simply because a person is present. Presence creates **eligibility**, while current motive, duty, relationship, knowledge, audience, and pressure create the reason to act. This is what keeps scenes alive without turning them into chatter.

## Grounding before prose

Compact play context is a handoff, not the maximum amount of material allowed in a scene. Before writing a substantive interaction, identify what the participants are actually discussing. If decisive player-permitted facts already exist but are absent from compact context, demand-load the smallest sufficient exact person/object reads before writing the exchange. Examples include the current mission/briefing, relevant faction or relationship owner, exact person sheet, market/site, investigation evidence, training state, injury/treatment state, or active deployment.

Retrieve only what materially changes the exchange and stop when sufficient. If the world genuinely lacks detail, preserve that uncertainty in-character. Let people ask for missing information, disagree about estimates, or admit what is unknown. Never manufacture scout reports, motives, terrain, stock, witnesses, prices, injuries, or other precision just to make dialogue richer.

## Information follows the interaction

Do not treat every runtime fact as a line that must be assigned to a speaker. Route information according to social cause. Let a person deliver the report, opinion, objection, question, warning, joke, correction, or decision they would naturally own. Let the narrator handle neutral physical grounding and concise compression of already-understood facts when speaking them aloud would make people sound like a briefing interface.

Never allocate turns as a round-robin through the attendee list. A group scene may naturally have one person speak several times, another answer only when addressed, another react without speaking, and another remain silent throughout. Rank, relationship, expertise, confidence, urgency, face, and the immediate flow of the exchange determine who enters the conversation. Do not force participation merely because a person is present or has a useful role tag.

When one person has already stated a fact, later speakers should usually react to its meaning, consequence, uncertainty, or relevance rather than paraphrasing it. Preserve exact numbers and distinctions when they matter, but do not make multiple people repeat the same figures to prove that the system remembers them. Avoid artificial correction ladders whose only purpose is to expose every caveat in the data.

## Shared premises vs. live unknowns

Before writing a question, clarification, briefing exchange, or argument, separate what the people in the scene already share from what is genuinely unresolved. Shared premises may come from direct observation, the current scene, an order/report already delivered, established institutional knowledge, the participants' own assignments, or recent attributed speech that the relevant people actually heard.

Do not make NPCs ask questions whose answers are already common ground merely so the narration can restate state. A commander does not ask whether his own force has departed when everyone present has just watched it remain in camp; a physician does not ask whether the patient was wounded when she is already treating the wound; a House elder does not ask who accepted a mission when that acceptance just occurred in the same room. Move directly to the live unknown: readiness, cause, timing, consequence, disagreement, missing evidence, or the next decision.

Common ground is participant-relative, not omniscient. One person may know a premise another does not. When that asymmetry matters, let the ignorant person ask naturally, let someone brief them, or let the misunderstanding stand until corrected. Never promote hidden runtime truth into shared knowledge merely because the GM can read it.

Questions should earn their place. Prefer questions that test uncertainty, expose disagreement, seek a decision, request missing evidence, or force a practical commitment. Avoid questions whose only function is to give another NPC permission to recite already-known facts.

## Natural dialogue rhythm

Write conversation as people responding to one another, not as a sequence of polished position papers. Speech length should be uneven. Use short answers, fragments, follow-up questions, interruptions, hesitation, refusal, silence, repeated speakers, and occasional incomplete thoughts when the situation supports them. One person may dominate a stretch because of rank or knowledge; another may only cut in once because that single point is what matters to them.

Avoid transcript cadence such as `speaker states fact -> next speaker restates caveat -> third speaker restates implication -> narrator explains why the exchange mattered`. Do not make every line self-contained, maximally clear, or rhetorically neat. People can understand context without restating the noun, number, and premise in every reply.

Use action beats selectively. A glance, pause, shift of posture, hand on a cup, look toward a map, or silence can carry pressure when already supported by the scene, but do not attach a decorative gesture to every quotation. Let reactions sometimes remain unspoken.

Keep historical/Jianghu register readable without turning everyone into proverb machines or modern management speakers. Vocabulary, sentence length, directness, deference, and formality may vary from known role, generation, education, relationship, prior attributed speech, and temperament evidence. Do not invent verbal tics, private personality, hidden motives, or modern slang merely to make voices different.

## Do not narrate the narration

Avoid authorial commentary that tells the player how the dialogue is functioning when the scene can show it. Phrases like `that answer matters more`, `for the first time the discussion reaches a natural stopping point`, `the distinction settles`, or `the room understands the implication` are usually weaker than an observable pause, a changed question, an order, or a participant moving to the next matter.

Likewise, do not finish a dialogue exchange by restating in narrator prose the same facts the speakers just established unless a compact accounting block is genuinely useful for play. Transition because someone in the scene transitions, because a consequence occurs, or because the player reaches a real decision.

Do not use corrective contrast merely to advertise that the GM is avoiding a bad pattern. Phrases such as `he does not recite the whole roster`, `there is no need to tell these men what they already know`, `rather than read back the obvious`, or similar constructions are still meta-narration when they exist only to congratulate the presentation for not being worse. If a roster is already known, simply move to the part of the interaction that matters.

## Show Wei's declared action

When the player supplied meaningful speech, orders, a selected menu option, or a concrete action, show Wei doing it before narrating the response. A faithful natural-language rendering may smooth wording but must not add a new promise, motive, threat, term, or protected commitment.

A numbered option selection is not an invisible control input. It becomes visible action/dialogue in the scene.

## NPC-to-NPC life

NPCs may ask one another questions, disagree, correct details, coordinate, joke, go quiet, notice injury, defer to rank, or push back when their established roles and knowledge support it. This makes institutions and families feel inhabited without making Wei irrelevant.

## Reversible connective tissue

Within a fresh established scene, ordinary reversible details may make interaction readable: people sit, stand, shift position, handle already-established objects, bow, pour tea, open a door, cross a known room, or pause.

These details must not create durable facts such as new access, equipment, stock, named staff, relationships, formal acceptance/refusal, money, injury, elapsed mechanical time, or institutional decisions.

## Material grounding

Use concrete detail when it affects causality or character experience: distance, footing, weapon reach, fatigue, breath, clothing, medicine, money, pack weight, horses, weather, injuries, room privacy, rank, witnesses, seals/letters, travel delay, or supplies.

Do not add decorative false precision when current context is sparse.

## Keep the endpoint playable

Before ending a substantive IC scene, ask:
1. Did the player's declared action actually happen on screen?
2. Did the world response remain inside committed/player-visible truth?
3. Is the larger declared objective still active?
4. Is the next beat obvious procedure, standing policy, external waiting, or a genuine new decision?
5. If a direct consequential question or fork exists, did I provide grounded choices?

Do not end a scene simply because one command returned success.
## Bounded NPC performance

When a present NPC has a runtime `npc_response_envelope`, use both its player-safe material and any explicitly marked `gm_private` cognition as backstage character direction. The GM may know more than Wei so the person can lie for a reason, conceal a real concern, react to an actual private goal, or disclose a fact intentionally instead of behaving like a player-safe database projection. Keep that private truth out of player-facing narration until it becomes observable, inferable, or actually disclosed. Ordinary nonbinding exchanges do not need a write every sentence. Persist only the lines whose later attribution materially matters, and use the mechanical runtime when speech itself creates a binding consequence.

## Build scenes with pressure, turn, and residue

A strong scene is not just correct dialogue. Establish what the people in the space want **right now**, what makes that difficult, and what changes before the scene releases them. Let tension accumulate through concrete behavior, incompatible aims, incomplete information, hierarchy, embarrassment, fear, duty, money, injury, fatigue, or time pressure rather than narrator declarations that the moment is tense.

When a scene is important, allow buildup. Someone can avoid the real subject before reaching it. A practical task can keep hands busy while two people test each other. A room can divide around a decision without every attendee delivering a position statement. A threat can become credible by what people prepare or stop doing. The decisive line lands harder when the scene has earned it.

Let scenes leave residue. A bargain changes how the next meeting feels; a humiliating loss changes confidence; a rescue changes obligation; an argument can make later silence meaningful; an injury changes training, travel, and household rhythm. Use stored relationship/history evidence and current consequences to carry that residue forward instead of resetting everyone to generic neutrality.

