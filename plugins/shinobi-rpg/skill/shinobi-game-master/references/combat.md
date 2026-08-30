# Exact Combat

Use this reference for duels, team combat, pursuit, ambush, and immediate physical danger.

## Spatial authority

Exact combat owns a bounded local battlefield with coordinates, elevation, facing, occupied body footprints, movement vectors, obstacles, cover, exits, and line of sight. Distance bands are derived summaries only.

Target identity expresses intent. Contact comes from geometry. A released projectile keeps its launch position, direction and velocity. Direct, lane, cone, arc, sweep, and radius effects test physical intersection and obstruction. Friendly bodies can obstruct or be struck when the path actually crosses them.

## Player intent granularity

A player combat command does not need to enumerate every mechanical choice. A terse intent such as **attack**, **press him**, or **keep fighting** authorizes the runtime to fill only the unspecified tactical details from Tang Wei's standing combat doctrine, lawful perception, current geometry, equipment, fatigue, Qi state, poison inventory, and active team doctrine. This can include target selection when the player did not name one, weapon/technique selection, anatomical aim, automatic defense, movement needed to make the attack physical, and conservative Qi/poison use.

Concrete player details always override doctrine for that detail. For example, **throw an unpoisoned needle at his right wrist without Qi** fixes weapon family, poison choice, target and Qi choice while leaving unrelated defensive reactions to the resolver. No special delegation flag is required for ordinary shorthand.

A longer instruction such as **fight for thirty seconds**, **keep attacking**, or **finish the fight** may carry the same standing policy through many exact exchanges. This is valid player delegation. Do not force Tang Wei to micromanage every swing merely to keep the fight moving.

Preserve compound engagement intent, not only force intent. **Kill as many as possible as quickly as possible**, **run them down**, or another explicit relentless lethal-until-resolution declaration must not be reduced to `targeting_intent: lethal` while silently restoring a restrained pursuit posture for the omitted tempo. The registered lethal-until-resolution combat span supplies a temporary assertive, committed, persistent, mobile engagement override while retaining Wei's standing resource discipline and targeting policy; it must never overwrite his saved doctrine.

A long standing combat span is never permission to erase the fight as a scene. The runtime may resolve many exchanges under one declared policy, but the GM must preserve and narrate the committed chronology rather than replacing it with terminal HP, injury, or casualty accounting.

## Combat narration contract

Active combat is a lived scene, not an after-action report.

A combat command receipt that contains ordered `events` is transition evidence. Preserve that event sequence while refreshing play context. The refreshed context establishes current truth; the receipt establishes how the committed transition happened. Do not throw away the events and reconstruct the fight afterward from final health totals.

When current-transition recovery is paginated, follow `next_object_ref` sequentially from the first page until it is null before making any negative claim about the span. Never sample arbitrary event offsets and infer an absence. Claims such as **no kill**, **no wound**, **no Qi use**, **no casualty**, or **nothing material happened** require complete receipt coverage or an authoritative bounded summary that explicitly establishes that absence. If complete chronology cannot be recovered, narrate only what the recovered evidence positively establishes and say the omitted outcome is unresolved rather than inventing certainty.

For any committed combat span, narrate the fight in chronological scene beats before presenting a compact status summary. The reader should be able to follow what pressure existed, what Wei attempted, what answered him, what changed, and why the battlefield now looks different.

The core presentation loop is:

`pressure/geometry -> Wei's action -> defense/collision -> ally/enemy reactions -> wound/status/tactical change -> continued pressure`

Repeat that loop as many times as the committed span materially requires.

Do not turn raw events into a literal log. Routine misses, repeated guard work, and mechanically similar exchanges may be compressed into fluid connective prose. But do not compress away material developments. Every first visible onset of a serious wound, incapacitation, death, dangerous poison effect, weapon loss, major knockdown, formation break, rescue pressure, surrender attempt, reinforcement, or decisive positional reversal must appear at the point it happens in the chronology.

If a span contains many exchanges, write it as an actual sustained combat sequence. Use paragraphing and pressure transitions so the reader can feel the battle evolving. It is acceptable for several routine exchanges to occupy one paragraph when nothing important changes; it is not acceptable to skip ten minutes of fighting and then announce who died.

A good combat response should feel like fiction generated from mechanics, not mechanics summarized as prose.

### Player-facing prose translation

Player-facing combat prose must not name resolver primitives when ordinary physical language can express the same committed fact. Terms such as **attack line**, **movement lane**, **contact geometry**, **trajectory intersection**, **range band**, **decision origin**, **execution frontier**, **combatant ref**, **contact trace**, or **retreat corridor** belong in explicit OOC audits, not in the lived fight. A road, doorway, gap between bodies, line of sight, spear reach, or visible path may of course be described when those are things Wei can actually perceive.

Translate mechanics into embodied cause and effect. **Target moved out of reach** becomes the point arriving a handspan late as the opponent jerks back. **A friendly body intersected the attack geometry** becomes a fist, blade, or shaft smashing into the person who physically crossed between attacker and target. **The approach lane was blocked** becomes bodies, a cart, a wall, bad footing, or another established obstacle physically preventing the close. Never make the reader decode the simulation vocabulary to understand the action.

Write sustained combat with the continuity of an action scene in a strong novel or film: immediate threat, committed movement, steel or body contact, reaction, visible consequence, then the next pressure. Use concrete orientation such as **to your left**, **three paces downslope**, **between the carts**, **inside his spear point**, or **behind the man you just passed** when supported. Prefer breath, balance, timing, pain, sound, footing, weapon feel, faces, and split-second choices over abstract tactical labels.

Do not narrate a resolved event as a technical explanation merely because its receipt is technical. The receipt is evidence for the GM, not dialogue for the player. If an OOC mechanical note is useful, place it after the scene and clearly separate it from the fiction.

## Standing orders and player control

Agency cadence and narration cadence are separate.

A standing order such as **keep attacking**, **press them**, or **finish the fight** may continue automatically across many committed exchanges without asking the player to restate the same instruction. Casualties and injuries do not automatically cancel that order.

Deaths, critical wounds, incapacitations, poison crises, and tactical collapses are mandatory **narration checkpoints**. Show them when they happen. They become **control checkpoints** only when the new state creates a genuine protected decision that the standing order does not already answer.

Examples where standing attack normally remains sufficient:

- an enemy dies and another lawful target remains;
- an ally is wounded but the declared policy was to keep pressing and no rescue choice is forced;
- routine Qi expenditure, fatigue, misses, blocks, and minor wounds accumulate;
- the formation degrades but the established combat doctrine can lawfully adapt without choosing a new strategic objective.

Examples where control should return because the standing order is no longer enough:

- Wei is incapacitated or otherwise unable to continue the declared action;
- the only explicit target becomes unavailable and the player's intent does not authorize retargeting;
- an ally's condition creates a real rescue-versus-continue tradeoff not already resolved by standing doctrine;
- a credible surrender, ceasefire, ransom, binding demand, or parley creates a new voluntary commitment decision;
- the mission objective changes, becomes impossible, or conflicts with continuing the attack;
- an unexpected development creates a new strategic choice rather than merely another combat problem.

Do not manufacture a menu merely because blood was spilled. If the player already said to keep fighting and the current doctrine still lawfully determines ordinary continuation, keep the scene moving while narrating what it costs.

If the player explicitly asks to fast-forward or compress a fight, reduce prose density accordingly, but still preserve chronology around irreversible or identity-changing events. Compression changes narrative density, not causal truth.

## Narrating committed combat

Lead with physical action. Do not begin an active combat response with a roster, casualty table, or status digest.

Render Wei's declared attack, movement, threat, order, or supplied action on screen before the world reaction. Then synthesize committed events in chronological order.

A useful sustained combat scene usually includes:

- Wei's position relative to the immediate threat and what is constraining him;
- the attacks, movement, defenses, collisions, and recoveries that actually matter;
- nearby allies and enemies acting when they materially affect Wei, a casualty, the objective, or local geometry;
- concrete wound onset and visible degradation rather than abstract damage labels;
- shifts in initiative, spacing, lanes, crowding, pursuit, retreat pressure, and formation integrity;
- battlefield speech when it is naturally supported;
- the pressure that carries directly into the next beat.

Translate numbers into lived evidence first. Fatigue becomes shorter breath, late recovery, shaking structure, or degraded footwork when supported. Blood loss becomes visible bleeding and functional decline. Qi expenditure becomes the concrete speed, force, control, defense, or recovery effect actually committed. Poison becomes observable symptoms and capability effects Wei can lawfully perceive.

Exact numbers may follow in a compact status block when useful for the next decision, but that block is secondary. Never let the status block become the scene.

Time must remain legible. When a fight spans minutes, show strain accumulating through the narrated sequence. Do not use **an hour passes beneath steel** as a substitute for the combat that made that hour consequential.

## Scene quality

Combat prose should be concrete, spatial, and causal rather than generically cinematic.

Prefer supported physical cues such as feet slipping or planting, steel binding, a spear haft jarring, a blade being knocked off line, breath shortening, blood changing a grip, an injured fighter protecting one side, Qi sharpening a burst of movement, bodies constricting a lane, or a retreat corridor opening.

Vary rhythm. A routine stretch can move quickly. A decisive collision may deserve several paragraphs. Let prose density follow consequence.

Avoid repetitive sentence templates such as **Wei attacks. The enemy blocks. Wei attacks again.** Show intention, geometry, reaction, and consequence as one physical flow.

Do not add unsupported flourish that changes mechanics. The goal is not purple prose; the goal is making mechanically rich combat readable and exciting.

## Battlefield voices and human presence

Team combat is also a co-located people scene. Named allies are not health bars.

When fresh player-safe facts support it, let combatants use brief reversible battlefield speech and reactions: warnings, calls for help, pain, confirmation, challenges, coordination, a medic shouting a visible casualty state, or an ally reacting to someone going down.

Do not force everyone to speak, rotate through the roster, or use dialogue to recite mechanics. One sharp warning may be enough. A fighter busy surviving may say nothing.

Ordinary nonbinding battlefield speech may be realized as scene performance under the normal scene contract. Persist only speech whose later attribution materially matters. A surrender, ceasefire, binding order, promise, ransom term, custody change, or other hard consequence still requires its mechanical authority before narration treats it as accomplished.

## Observer reports and enemy counts

Treat combat observation as observer-specific knowledge. When `scene.combat_observation_context` exists, `player_observation` is Wei's own stored observation while each entry in `ally_observer_summaries` belongs to that ally and is not automatically Wei's knowledge.

When `scene.combat_present_person_ids` exists, treat it as the exact player-safe friendly cast that has actually reached the active combat space. Prefer it over stale `state/scene.json` cast memory, route membership, mission rosters, or `person_reads.suggested_owner_ids` when deciding which allies can physically speak, protect, be addressed, or otherwise participate in the immediate battle scene. A registered future reinforcement is reserved for the combat but is **not co-present** until its exact reinforcement clock arrives.

`confirmed_observed_hostile_count` is cumulative encounter observation: it is the count of distinct hostile combatants that observer has lawfully detected during the active combat so far. It is not a live census of who remains capable, conscious, present, visible, or engaged at this moment, and it is not a guarantee that no additional enemy exists outside the observer's knowledge or outside the current combat space. Never expose hidden hostile IDs or substitute the hidden opposing roster size for an observer's count.

Current-strength narration must come from fresh player-lawful battlefield evidence: direct sensory events, current player-safe presence or perception projection, or a fresh attributed report. Never render the cumulative observed count as an exact statement that **X remain**, **X are still up**, **X surround you**, **X are left**, or equivalent current-strength prose unless fresh player-lawful evidence independently supports that exact current cardinality. If exact current strength is not lawfully established, describe the current pressure qualitatively instead of recycling stale numerical precision.

Never back-calculate an IC live enemy count by combining `confirmed_observed_hostile_count` with GM-private participant, casualty, incapacitation, withdrawal, escape, or exact-roster data. GM-private exact combat totals may inform behind-the-curtain direction and an explicit OOC audit, but they are not automatically Wei's knowledge. For current scene presentation, fresh lawful perception outranks stale mission reports, route rosters, historical observation totals, and GM-private arithmetic.

If Wei asks an exactly co-present scout or ally how many enemies they saw, let that person report the concrete confirmed count through ordinary reversible dialogue. Prefer wording such as **I saw seven** or **I counted seven; there may be more** when total force remains uncertain. Do not collapse a stored positive observation to a generic **I don't know** merely because the observer cannot certify the entire hostile force.

Do not union allied observations into Wei's knowledge merely because the handoff contains them. The observation becomes Wei's scene knowledge only through a lawful shared-information path such as the ally reporting it, Wei directly observing it, or another established communication path.

## Parley during combat

Active exact combat does not make speech impossible. Wei may call out, question, warn, identify himself, request parley, or make another reversible nonbinding social attempt while combat remains active. Use `jianghu_interaction_resolution` for that attempt rather than forcing Wei to attack or disengage merely because combat has been initialized.

When Wei is addressing the opposing side generally and no opposing individual ID is player-visible, use the exact active `combat_ref` as the interaction `target_ref`. That means **address the opposing combat side**. Never guess, retrieve, or expose a hidden hostile person ID just to make dialogue possible.

A combat parley attempt does not pause the exact-combat timeline, create a safe zone, make attacks impossible, establish that anyone answered, or produce a ceasefire, surrender, truce, ransom, safe passage, custody transfer, retreat agreement, or other binding consequence. Those outcomes require their own mechanical authority.

When fresh play context exposes `scene.combat_parley`, treat that handoff as the durable reversible conversation surface for the exact active opposing side. Its `open_threads` are player-authored response-bearing conversational moves still live against that combat side; `open_questions` is the compatibility subset for literal questions. The combat ref is a group address, not a hidden spokesman identity.

When an ordinary response is natural and the player-safe scene supports one, do not default to sterile silence merely because no hostile person ID is visible. Realize one bounded opposing-side line, then persist it with `jianghu_scene_session_resolution` using `action: record_speech`, with both `session_ref` and `speaker_ref` set to the exact active combat ref. If the line actually responds to one projected open thread, set `resolves_thread_ref` to that exact thread ref. `resolves_question_ref` remains a compatibility alias for literal questions. Refresh play context after the write before continuing the scene.

That group-attributed line may acknowledge, refuse, object, warn, challenge, ask a question, make a nonbinding proposal, lie, evade, or selectively disclose real private facts when that is coherent with the GM-private encounter/cognition context. Hidden hostile identity, employer, mission, force facts, motive, or plans are valid **director knowledge** when explicitly returned as GM-private, but they are not player knowledge. Reveal one only when the NPC actually chooses to disclose it or Wei can lawfully perceive/infer it. Do not invent a new hidden fact merely to make the exchange interesting.

Persisted combat-side speech is only an **attributed statement from the opposing side**. It has no mechanical-consequence authority and is not automatically objective truth. A hostile speaker may truthfully reveal something Wei did not know, refuse to explain, lie, bluff, threaten, misunderstand, or state an opinion. The runtime's GM-private truth tells the director what is actually behind the line; the attributed speech records only what Wei heard. Mechanically significant new knowledge should be persisted through the appropriate information authority when the disclosure itself must become durable player knowledge.

A reversible line such as **Turn back**, **You are not owed an explanation**, **Name your purpose**, or **Come no closer** does not itself move anyone, pause combat, establish that every combatant obeys, or create an agreement. If either side actually attacks, moves consequentially, surrenders, accepts terms, creates a ceasefire, changes custody, pays ransom, grants passage, or makes another hard commitment, use the relevant mechanical command before narrating that consequence.

A new response-bearing combat-side conversational thread should remain open until responded to or made irrelevant by a hard scene boundary. The read projection may also recover an unresolved legacy combat-side question that was written before combat questions became first-class threads. Never generalize that legacy compatibility to an old combat ref, a person target, or an already answered question.

## Action sequence

Resolve roughly:

1. lawful action selection;
2. equipment/resource/status/range legality;
3. movement and startup;
4. detection;
5. physical defensive response selection;
6. defensive movement/commitment;
7. geometry recalculation;
8. contact determination;
9. weapon/body interaction;
10. wound, blood loss, shock, displacement or status;
11. recovery and commitment;
12. pending-action recalculation.

Melee outside reach requires physical closing movement. Extreme speed shortens travel and recovery but never removes travel, body orientation, balance, or commitment.

## Defense

Possible lawful responses include evade, reposition, parry, deflect, block, brace, and counter-intercept. Selection depends on perception, skill, equipment, doctrine, injuries, fatigue, geometry, timing, momentum, nearby bodies, terrain, and objective. Successful evasion/reposition changes authoritative coordinates.

## Multiple attackers

Pressure comes from real timing, different angles, facing changes, movement, limb/weapon commitment, balance, recovery, crowding, and blocked escape routes. Do not model several attackers as repeated fresh one-on-one defenses.

## Team tactics

A trained team may create a shared tactical problem, desired state, and temporary roles such as anchor, screen, control, shape, track, intercept, pressure, flank, protect, ranged denial, reserve, extract, or exploit. Planning uses only lawfully shared information. Individual action selection and the resolver still determine success.

## Persistence

Persist positions, facing, movement, recovery, defensive commitment, objective identity, incapacitated bodies, equipment state, wounds, fatigue and Qi state across internal processing boundaries. An incapacitated participant stops acting but remains a body, casualty and objective state.

### GM-private combat direction versus Wei's perception

When fresh context contains `scene.gm_private_director_context.combat`, use it aggressively **behind the curtain**. It exists so the AI GM can know the real current geometry, all registered participants, actual wounds, tactical state, objectives, team plans, private character direction when available, encounter motive/causality, and other current-scene truth needed to choreograph a living fight instead of paraphrasing a thin player projection. This is not player knowledge. `scene.combat_observation_context`, direct sensory events, attributed reports, recognition, and information mechanics determine what Wei can actually know.

Do not confuse **GM omniscience** with **omniscient narration**. It is good for the GM to know that an unseen attacker is circling, that a fighter is protecting a damaged knee, that the ambush was motivated by cargo, or that an enemy is trying to disengage. Use that truth to make actions and reactions causally intelligent. Reveal the hidden fact itself only when Wei perceives it, reasonably infers it from observable effects, hears it disclosed, or receives it through a lawful information path.

The private director packet may explain why an unseen opponent moves, why someone hesitates, what attack is coming, or which wound is affecting a fighter, but the narration must reveal those causes only through observable consequences unless Wei has a lawful information path. Do not make hidden actors vanish from the simulation merely because Wei has not detected them. Likewise, do not name or precisely locate them to the player until detection/recognition supports it.

The exact combat resolver remains outcome authority. Omniscient GM context improves direction and prose; it never lets the narrator change contact, defense, injury, fatigue, Qi use, poison, position, or timing.