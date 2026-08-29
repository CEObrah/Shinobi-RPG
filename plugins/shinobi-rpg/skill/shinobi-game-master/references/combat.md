# Exact Combat

Use this reference for duels, team combat, pursuit, ambush, and immediate physical danger.

## Spatial authority

Exact combat owns a bounded local battlefield with coordinates, elevation, facing, occupied body footprints, movement vectors, obstacles, cover, exits, and line of sight. Distance bands are derived summaries only.

Target identity expresses intent. Contact comes from geometry. A released projectile keeps its launch position, direction and velocity. Direct, lane, cone, arc, sweep, and radius effects test physical intersection and obstruction. Friendly bodies can obstruct or be struck when the path actually crosses them.

## Player intent granularity

A player combat command does not need to enumerate every mechanical choice. A terse intent such as **attack**, **press him**, or **keep fighting** authorizes the runtime to fill only the unspecified tactical details from Tang Wei's standing combat doctrine, lawful perception, current geometry, equipment, fatigue, Qi state, poison inventory, and active team doctrine. This can include target selection when the player did not name one, weapon/technique selection, anatomical aim, automatic defense, movement needed to make the attack physical, and conservative Qi/poison use.

Concrete player details always override doctrine for that detail. For example, **throw an unpoisoned needle at his right wrist without Qi** fixes weapon family, poison choice, target and Qi choice while leaving unrelated defensive reactions to the resolver. No special delegation flag is required for ordinary shorthand.

A longer instruction such as **fight for thirty seconds**, **keep attacking**, or **finish the fight** may carry the same standing policy through many exact exchanges. This is valid player delegation. Do not force Tang Wei to micromanage every swing merely to keep the fight moving.

A long standing combat span is never permission to erase the fight as a scene. The runtime may resolve many exchanges under one declared policy, but the GM must preserve and narrate the committed chronology rather than replacing it with terminal HP, injury, or casualty accounting.

## Combat narration contract

Active combat is a lived scene, not an after-action report.

A combat command receipt that contains ordered `events` is transition evidence. Preserve that event sequence while refreshing play context. The refreshed context establishes current truth; the receipt establishes how the committed transition happened. Do not throw away the events and reconstruct the fight afterward from final health totals.

For any committed combat span, narrate the fight in chronological scene beats before presenting a compact status summary. The reader should be able to follow what pressure existed, what Wei attempted, what answered him, what changed, and why the battlefield now looks different.

The core presentation loop is:

`pressure/geometry -> Wei's action -> defense/collision -> ally/enemy reactions -> wound/status/tactical change -> continued pressure`

Repeat that loop as many times as the committed span materially requires.

Do not turn raw events into a literal log. Routine misses, repeated guard work, and mechanically similar exchanges may be compressed into fluid connective prose. But do not compress away material developments. Every first visible onset of a serious wound, incapacitation, death, dangerous poison effect, weapon loss, major knockdown, formation break, rescue pressure, surrender attempt, reinforcement, or decisive positional reversal must appear at the point it happens in the chronology.

If a span contains many exchanges, write it as an actual sustained combat sequence. Use paragraphing and pressure transitions so the reader can feel the battle evolving. It is acceptable for several routine exchanges to occupy one paragraph when nothing important changes; it is not acceptable to skip ten minutes of fighting and then announce who died.

A good combat response should feel like fiction generated from mechanics, not mechanics summarized as prose.

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

Missing hostile identity, motive, employer, or complete force accounting does not require enemies to become mute. Opposing combatants may shout warnings, taunts, refusals, pain, confusion, tactical calls, or other reversible lines grounded in what Wei can actually observe. Narrow uncertain claims instead of suppressing the human scene.

## Observer reports and enemy counts

Treat combat observation as observer-specific knowledge. When `scene.combat_observation_context` exists, `player_observation` is Wei's own stored observation while each entry in `ally_observer_summaries` belongs to that ally and is not automatically Wei's knowledge.

Friendly presence has separate meanings. When these projections exist:

- `scene.combat_present_person_ids` is the player-safe friendly cast currently able to act in the exact combat space;
- `scene.combat_body_person_ids` is the wider player-safe set of friendly bodies still physically present, including incapacitated or dead bodies;
- `scene.combat_dead_person_ids` identifies friendly bodies currently projected dead;
- `scene.combat_incapacitated_person_ids` identifies friendly bodies projected incapacitated or unconscious without also being dead.

Use the active cast when deciding who can speak, protect, attack, move under their own power, or otherwise take combat action. Use the body/casualty projections when narrating wounded retrieval, obstruction, protection, treatment pressure, or the physical presence of fallen allies. Do not let an incapacitated or dead body speak or act merely because it remains physically present.

Prefer these exact-combat projections over stale `state/scene.json` cast memory, route membership, mission rosters, or `person_reads.suggested_owner_ids`. A registered future reinforcement is reserved for the combat but is **not co-present** until its exact reinforcement clock arrives.

`confirmed_observed_hostile_count` is cumulative detection history for that observer in the active combat: the number of unique hostile combatants that observer has directly detected at some point in the fight. It is **not** current active hostile strength, not a casualty-adjusted headcount, and not proof that no additional enemy exists outside the observer's knowledge or outside the current combat space. Never expose hidden hostile IDs or substitute the hidden opposing roster size for this observed count.

When available, use `player_hostile_status_observation` for Wei's last directly observed hostile condition accounting. Its active-unwounded, active-wounded, incapacitated, dead, and unknown counts are observer-safe snapshots of the last direct observation, not omniscient current state. `observed_status_unknown_count` means the observer has previously detected those hostiles but lacks a reliable stored condition snapshot for them.

Do not derive current enemy strength by subtracting remembered casualties from `confirmed_observed_hostile_count`. Do not present all historically observed hostiles as currently standing merely because their latest condition is unknown. Describe only what Wei can presently support: bodies he can see, enemies he can still track, last-seen casualties, uncertainty created by terrain or movement, and any explicit last-observation status projection.

Legacy combats may contain positive cumulative hostile observations whose condition snapshots are unknown because the fight began before status snapshots were recorded. Preserve that uncertainty. Future direct observation can refine the status projection; never invent retroactive enemy casualties or silently convert `unknown` into `active`.

If Wei asks an exactly co-present scout or ally how many enemies they saw, let that person report their concrete cumulative confirmed count through ordinary reversible dialogue, with the temporal qualifier that fits the scene. Prefer wording such as **I counted seven on the road; I cannot tell how many are still up** when present status is uncertain. Do not collapse a stored positive observation to a generic **I don't know** merely because the observer cannot certify the entire hostile force.

Do not union allied observations into Wei's knowledge merely because the handoff contains them. The observation becomes Wei's scene knowledge only through a lawful shared-information path such as the ally reporting it, Wei directly observing it, or another established communication path.

## Parley during combat

Active exact combat does not make speech impossible. Wei may call out, question, warn, identify himself, request parley, or make another reversible nonbinding social attempt while combat remains active. Use `jianghu_interaction_resolution` for that attempt rather than forcing Wei to attack or disengage merely because combat has been initialized.

When Wei is addressing the opposing side generally and no opposing individual ID is player-visible, use the exact active `combat_ref` as the interaction `target_ref`. That means **address the opposing combat side**. Never guess, retrieve, or expose a hidden hostile person ID just to make dialogue possible.

A combat parley attempt does not pause the exact-combat timeline, create a safe zone, make attacks impossible, establish that anyone answered, or produce a ceasefire, surrender, truce, ransom, safe passage, custody transfer, retreat agreement, or other binding consequence. Those outcomes require their own mechanical authority.

When fresh play context exposes `scene.combat_parley`, treat that handoff as the durable reversible conversation surface for the exact active opposing side. Its `open_questions` are player-authored questions still live against that combat side. The combat ref is a group address, not a hidden spokesman identity.

When an ordinary response is natural and the player-safe scene supports one, do not default to sterile silence merely because no hostile person ID is visible. Realize one bounded opposing-side line, then persist it with `jianghu_scene_session_resolution` using `action: record_speech`, with both `session_ref` and `speaker_ref` set to the exact active combat ref. If the line actually answers one projected open question, set `resolves_question_ref` to that exact question ref. Refresh play context after the write before continuing the scene.

That group-attributed line may acknowledge, refuse, object, warn, challenge, ask a question, speculate explicitly from player-safe evidence, or make a nonbinding proposal. It may use ordinary natural tone, hesitation, contempt, caution, or uncertainty when supported by the visible situation. It must not invent or expose a hidden hostile identity, secret employer, secret mission, private motive, concealed force fact, or other new secret factual information merely to make the exchange interesting.

Persisted combat-side speech is only an **attributed statement from the opposing side**. It has no mechanical-consequence authority and is not automatically objective truth. A hostile speaker may refuse to explain, lie, bluff, threaten, misunderstand, or state an opinion, but the factual content still must stay within the player's lawful evidence unless another runtime authority independently establishes the fact.

A reversible line such as **Turn back**, **You are not owed an explanation**, **Name your purpose**, or **Come no closer** does not itself move anyone, pause combat, establish that every combatant obeys, or create an agreement. If either side actually attacks, moves consequentially, surrenders, accepts terms, creates a ceasefire, changes custody, pays ransom, grants passage, or makes another hard commitment, use the relevant mechanical command before narrating that consequence.

A new player combat-side question should remain open until answered or made irrelevant by a hard scene boundary. The read projection may also recover an unresolved legacy combat-side question that was written before combat questions became first-class threads. Never generalize that legacy compatibility to an old combat ref, a person target, or an already answered question.

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
