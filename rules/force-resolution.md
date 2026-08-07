# Force Resolution

Mass combat resolves by **unit**. A unit is one homogeneous troop type with saved aggregate/average capability distributions and current condition. There is no separate lower organizational mass layer.

Keep named exact and individual-lite actors exact. Apply their effects to the units they command, support, attack, heal, scout for, or otherwise materially affect. Resolve ordinary personnel through their unit's deterministic aggregate mechanics.

Split resolution whenever units differ materially in troop type, commander, doctrine, equipment, terrain, objective, morale, casualties, position, or special capability. Never average different troop types together.

Scale is computation only. A 50-person unit and a 5,000-person unit use the same underlying laws; larger units may use aggregate distributions for efficiency, but receive no representation bonus.


## Combat eligibility
Resolve line-combat contact only from units whose troop type is frontage-eligible. Combat-support and service-support units remain real, targetable and casualty-bearing but do not add default assault frontage or offensive contact capacity. If attacked, overrun, or explicitly committed, resolve their self-defense/committed action from their own aggregate stats and loadout. Casualties reduce the support capacity they actually provide.


## Capability loading
Unit owner files keep organization/readiness state compact. For combat or capability calculations, load only the engaged unit's `stats_ref` under `state/unit-capability/`. Never load the capability directory wholesale.
