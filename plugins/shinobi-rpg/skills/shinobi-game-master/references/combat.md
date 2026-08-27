# Exact Combat

Use this reference for duels, team combat, pursuit, ambush, and immediate physical danger.

## Spatial authority

Exact combat owns a bounded local battlefield with coordinates, elevation, facing, occupied body footprints, movement vectors, obstacles, cover, exits, and line of sight. Distance bands are derived summaries only.

Target identity expresses intent. Contact comes from geometry. A released projectile keeps its launch position, direction and velocity. Direct, lane, cone, arc, sweep, and radius effects test physical intersection and obstruction. Friendly bodies can obstruct or be struck when the path actually crosses them.

## Player intent granularity

A player combat command does not need to enumerate every mechanical choice. A terse intent such as **attack**, **press him**, or **keep fighting** authorizes the runtime to fill only the unspecified tactical details from Tang Wei's standing combat doctrine, lawful perception, current geometry, equipment, fatigue, Qi state, poison inventory, and active team doctrine. This can include target selection when the player did not name one, weapon/technique selection, anatomical aim, automatic defense, movement needed to make the attack physical, and conservative Qi/poison use.

Concrete player details always override doctrine for that detail. For example, **throw an unpoisoned needle at his right wrist without Qi** fixes weapon family, poison choice, target and Qi choice while leaving unrelated defensive reactions to the resolver. No special delegation flag is required for ordinary shorthand. A longer instruction such as **fight for thirty seconds** or **finish the fight** simply extends the same standing policy across the bounded simulated span.

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
