# Exact Combat

Use this reference for duels, team combat, pursuit, ambush, and immediate physical danger.

## Spatial authority

Exact combat owns a bounded local battlefield with coordinates, elevation, facing, occupied body footprints, movement vectors, obstacles, cover, exits, and line of sight. Distance bands are derived summaries only.

Target identity expresses intent. Contact comes from geometry. A released projectile keeps its launch position, direction and velocity. Direct, lane, cone, arc, sweep, and radius effects test physical intersection and obstruction. Friendly bodies can obstruct or be struck when the path actually crosses them.

## Player intent granularity

A player combat command does not need to enumerate every mechanical choice. A terse intent such as **attack**, **press him**, or **keep fighting** authorizes the runtime to fill only the unspecified tactical details from Tang Wei's standing combat doctrine, lawful perception, current geometry, equipment, fatigue, Qi state, poison inventory, and active team doctrine. This can include target selection when the player did not name one, weapon/technique selection, anatomical aim, automatic defense, movement needed to make the attack physical, and conservative Qi/poison use.

Concrete player details always override doctrine for that detail. For example, **throw an unpoisoned needle at his right wrist without Qi** fixes weapon family, poison choice, target and Qi choice while leaving unrelated defensive reactions to the resolver. No special delegation flag is required for ordinary shorthand. A longer instruction such as **fight for thirty seconds** or **finish the fight** simply extends the same standing policy across the bounded simulated span.

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
