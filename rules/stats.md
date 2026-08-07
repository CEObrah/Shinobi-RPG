# Stat Scale, Attributes, Skills, and Derived Values

This file defines the universal stat scale. Body dimensions are authoritative in character body state and are resolved through `data/mechanics/body.json`; they are not attributes.

## Attributes

The nine attributes are:

`strength`, `agility`, `endurance`, `toughness`, `coordination`, `awareness`, `intelligence`, `composure`, `presence`.

Attribute Support is calculated by `data/mechanics/stats.json`.

## Scale

- 0-19 rudimentary
- 20-39 developing
- 40-59 competent
- 60-79 professional
- 80-99 elite
- 100-124 master
- 125-159 exceptional master
- 160-199 legendary
- 200+ extreme historical outlier

## Universal action score

The fallback action-score formula and margin bands are owned by `data/mechanics/stats.json`. Use the exact subsystem authority whenever one exists. Body geometry, chakra, dōjutsu, injury, training, morale, and technique primitives may add or replace terms through their registered mechanics.

## Margin bands

Use the registered bands in `data/mechanics/stats.json`.

## Universal combat skills

`sword`, `polearm`, `staff`, `heavy_weapon`, `unarmed`, `grappling`, `thrown_tools`, `bow`, `movement`, `stealth`.

## Universal operational skills

`tactics`, `tracking`, `investigation`, `survival`, `medicine`, `leadership`, `traps`, `infiltration`, `team_coordination`.

## Specific defenses

- Dodge: movement + agility support.
- Weapon parry: relevant weapon skill + coordination support.
- Weapon block: relevant weapon skill + strength or coordination support, as the registered weapon/contact rule requires.
- Grappling escape: grappling + strength or coordination support plus body mass geometry from `data/mechanics/body.json`.
- Unarmed deflection: unarmed + coordination support.
- Jutsu counter: registered counter method mastery and subsystem mechanics.
- Cover: movement + awareness support plus scene geometry.
- Impact resistance: toughness support + armor + body mechanics where applicable.

No deprecated attribute conversion table is gameplay authority.
