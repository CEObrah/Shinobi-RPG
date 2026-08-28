# Exact Combat

Use this reference for duels, team combat, pursuit, ambush, and immediate physical danger.

## Spatial authority

Exact combat owns a bounded local battlefield with coordinates, elevation, facing, occupied body footprints, movement vectors, obstacles, cover, exits, and line of sight. Distance bands are derived summaries only.

Target identity expresses intent. Contact comes from geometry. A released projectile keeps its launch position, direction and velocity. Direct, lane, cone, arc, sweep, and radius effects test physical intersection and obstruction. Friendly bodies can obstruct or be struck when the path actually crosses them.

## Player intent granularity

A player combat command does not need to enumerate every mechanical choice. A terse intent such as **attack**, **press him**, or **keep fighting** authorizes the runtime to fill only the unspecified tactical details from Tang Wei's standing combat doctrine, lawful perception, current geometry, equipment, fatigue, Qi state, poison inventory, and active team doctrine. This can include target selection when the player did not name one, weapon/technique selection, anatomical aim, automatic defense, movement needed to make the attack physical, and conservative Qi/poison use.

Concrete player details always override doctrine for that detail. For example, **throw an unpoisoned needle at his right wrist without Qi** fixes weapon family, poison choice, target and Qi choice while leaving unrelated defensive reactions to the resolver. No special delegation flag is required for ordinary shorthand.

A longer instruction such as **fight for thirty seconds** or **finish the fight** extends the same standing policy, but it is not permission to erase the fight as a scene. Treat open-ended combat intent as a standing policy carried through bounded committed combat windows. Unless the player explicitly asks to fast-forward or compress the battle, do not resolve an arbitrarily long lethal team fight in one opaque `until_resolution` span.

## Combat scene cadence

Exact combat is still a live scene. A command receipt that contains combat `events` is transition evidence, not disposable backend detail. Preserve the committed event sequence while refreshing play context. The refreshed context establishes current truth; the receipt establishes how the committed transition happened. Do not throw away the event sequence and then reconstruct the fight from final health totals.

For an open-ended standing intent such as **keep attacking**, **press them**, or **finish the fight**, use short bounded combat windows, normally one exact exchange or only a few seconds at a time. After each committed window:

1. retain the returned ordered combat events and elapsed-time change;
2. refresh play context;
3. inspect the events plus refreshed player-visible state for a material combat frontier;
4. narrate that committed exchange as its own visible combat beat before folding it into any later exchange;
5. if nothing material changed and the standing policy remains unambiguous, continue the same already-declared combat policy with a fresh request ID rather than asking the player to restate the order;
6. if a material frontier occurred, stop further combat execution, narrate through that frontier, and return control when the new state creates a genuine choice.

Each bounded window is one persisted continuation under the already-declared combat policy. Never use repeated previews to probe possible futures. Continue only from committed results and fresh context.

Default player-attention frontiers include:

- Wei being wounded, incapacitated, disarmed in a materially consequential way, poisoned, trapped, or otherwise suffering a major capability change;
- an allied death;
- an ally becoming incapacitated, critically wounded, badly poisoned, or an immediate rescue/extraction objective;
- a material collapse or reorganization of the active team structure, screen, formation, escape route, or protected objective;
- an unexpected hostile reinforcement, newly detected threat, major terrain change, or loss of a critical weapon, mount, or route;
- surrender, a credible parley attempt, a binding-demand opportunity, or another development that changes the social objective of the fight;
- the current explicit target becoming unavailable when that makes the player's declared objective ambiguous;
- combat resolution itself.

Do not stop merely because someone takes a trivial cut, spends ordinary Qi, accumulates expected fatigue, or because a routine attack misses. A standing policy exists specifically to carry Wei through ordinary exchanges. Conversely, do not let **finish the fight** silently carry through deaths, critical casualties, poison crises, or objective-changing developments unless the player explicitly delegated continuation through those kinds of events or an established doctrine clearly resolves the tradeoff.

If the player explicitly requests a compressed or fast-forwarded fight, the runtime may resolve a broader span. Even then, preserve chronology around irreversible or identity-changing events. Compression changes prose density, not causal truth.

## Player-facing combat turns

Treat one committed exact combat exchange as the default **player-facing combat turn**. This is a presentation unit only; it does not replace the runtime's millisecond timeline, simultaneous declarations, defenses, movement, recovery, projectile flight, or other physical mechanics.

When the player gives one immediate combat action without delegating a longer span, resolve one exact exchange, refresh current state, and narrate that exchange before asking for another action. Do not skip several exchanges merely because the command could technically accept a larger scope.

When the player gives a standing instruction such as **keep fighting**, **press them**, or **finish the fight**, the GM may carry that instruction through several committed exchanges without requiring repeated input. But the presentation must still be **exchange by exchange**. Each committed exchange gets a distinct chronological narrative beat. Never silently accumulate ten, fifty, or a hundred exchanges and reveal only the terminal state.

A player-facing combat turn should normally make these things legible when they occurred:

- where Wei is relative to the immediate threat and what pressure is on him;
- what Wei actually attempts under the player's instruction and standing doctrine;
- the most relevant defensive reaction, collision, miss, parry, reposition, or contact;
- what nearby allies and enemies do when their actions materially affect Wei, the objective, a casualty, or the local geometry;
- any wound, poison exposure, Qi use, fatigue shift, weapon problem, knockdown, incapacitation, death, or formation change that becomes visible in that exchange;
- the ending pressure and geometry that carry into the next exchange.

Do not turn this into a literal event log. A twelve-person exchange may contain many scheduled actions, and not every fighter needs a sentence. Select the actions that make the exchange causally understandable from Wei's lawful perspective. The rule is **every exchange gets a scene beat**, not **every raw event gets prose**.

Vary the rhythm. One exchange may be two sharp paragraphs because everyone resets and nobody lands cleanly. Another may be much longer because a flank collapses, three weapons collide around Wei, an ally takes a mortal wound, and a poisoned blade changes the objective. Let prose density follow actual consequence.

Combat should feel immediate. Use concrete physical cues supported by the committed state: feet slipping or planting, steel binding, a spear haft jarring, breath shortening, someone shouting a warning, blood changing a grip, a wounded fighter protecting one side, Qi sharpening a burst of movement, bodies constricting a lane, or a retreat corridor opening. Do not add unsupported flourish that changes mechanics, but do not strip mechanically rich events into sterile summaries either.

If multiple committed exchanges are narrated in one assistant response under standing intent, preserve clear beat boundaries through paragraphing and time/pressure transitions. The player should be able to follow **what happened first, what changed, what happened next, and why the current battlefield now looks the way it does**.

The default dramatic loop is therefore:

`exchange resolves -> exchange is narrated -> state is refreshed -> standing intent either continues or a real decision interrupts`

not:

`many exchanges resolve -> final HP/casualty snapshot -> briefing`

## Narrating committed combat

Do not present active combat as an after-action briefing. Lead with the physical exchange and keep the reader inside Wei's perception. A useful combat turn usually has this shape:

`immediate geometry/pressure -> Wei's declared action -> defenses and collisions -> ally/enemy reactions -> decisive wound/status/tactical change -> brief recovery/reset -> next exchange or current decision`

Render the player's declared attack, movement, threat, order, or other supplied action on screen before the world reaction. Then synthesize the committed events in chronological order. Do not dump raw event JSON, event-by-event tables, or a roster-shaped casualty report as the primary presentation.

Expand the events that materially change experience or tactics: closing distance, a defense that changes position, a clean hit, a serious wound, poison exposure or onset, a Qi burst with visible effect, weapon loss, incapacitation, death, rescue pressure, a broken line, a successful interception, a blocked escape, a target switch caused by availability, or combat resolution. Compress repetitive misses and ordinary guard work **within that exchange**, but do not compress away the exchange itself during normal interactive combat.

Never skip over the first player-visible onset of death, incapacitation, severe injury, dangerous poison, or formation collapse and reveal it later as a bullet point. If Lu Yunyun dies during the exchange, the scene reaches Lu Yunyun's death when it happens. If Ye Yongrong goes down and becomes an extraction problem, Wei experiences that loss of the line before the narration asks what to do next.

Translate numbers into lived evidence first. Fatigue becomes slower recovery, rough breath, degraded footwork, shaking structure, or late reactions when supported. Blood loss becomes visible bleeding and functional decline. Qi expenditure becomes the concrete speed, force, control, defense, or recovery effect actually committed. Poison becomes the observable symptoms and capability effects Wei can lawfully perceive. Exact numbers may follow in a compact status block when they help the next decision, but the status block is secondary.

Time should remain legible. When a fight spans minutes, show the accumulating strain through the sequence of narrated exchanges and use exact committed elapsed time when useful. Do not write **more than an hour passes beneath steel** as a substitute for the combat events that made that hour consequential.

## Battlefield voices and human presence

Team combat is also a co-located people scene. Named allies are not health bars. When fresh player-safe facts support it, let combatants use brief reversible battlefield speech and reactions: warnings, calls for help, pain, confirmation, challenges, coordination, a medic shouting a visible casualty state, or an ally reacting to someone going down. Such lines should arise from what that speaker can observe and from their established role, relationship, and pressure.

Do not force everyone to speak, do not rotate through the roster, and do not use dialogue to recite mechanics. One sharp warning can be enough. A fighter who is busy surviving may say nothing. A medic can call **he is still breathing** if that is directly supported; the medic cannot diagnose a hidden poison or wound detail the scene has not established.

Ordinary nonbinding battlefield speech may be realized as scene performance under the normal scene contract. Persist only speech whose later attribution materially matters. A surrender, ceasefire, binding order, promise, ransom term, custody change, or other hard consequence still requires its mechanical authority before narration treats it as accomplished.

## Observer reports and enemy counts

Treat combat observation as observer-specific knowledge. When `scene.combat_observation_context` exists, `player_observation` is Wei's own stored observation while each entry in `ally_observer_summaries` belongs to that ally and is not automatically Wei's knowledge.

`confirmed_observed_hostile_count` means exactly what that observer has detected among the current hostile combatants. It is an observed count, not a guarantee that no additional enemy exists outside the observer's knowledge or outside the current combat space. Never expose hidden hostile IDs or substitute the hidden opposing roster size for an observer's count.

If Wei asks an exactly co-present scout or ally how many enemies they saw, let that person report the concrete confirmed count through ordinary reversible dialogue. Prefer wording such as **I saw seven** or **I counted seven; there may be more** when total force remains uncertain. Do not collapse a stored positive observation to a generic **I don't know** merely because the observer cannot certify the entire hostile force.

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
