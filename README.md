# Shinobi RPG: Jianghu Campaign

Persistent deterministic Jianghu RPG operated through ChatGPT and the Shinobi runtime. The Python package/runtime name remains `shinobi_runtime` for deployment continuity; the live game rules are Jianghu rules.

## Authority

- `runtime/`: deterministic execution, transactions, scheduling, combat, health, economy, training and simulation.
- `game/`: current static rules, formulas, equipment, factions, locations and reference data.
- `state/`: minimum sufficient current campaign truth.
- `plugins/shinobi-rpg/skills/shinobi-game-master/`: ChatGPT GM procedure and presentation.
- `tests/`: current behavior verification only.

Development history is not campaign state. The packaged save starts at revision 1 and contains only current campaign truth. Gameplay idempotency receipts are runtime-private and are created only by future committed commands.

## Current campaign baseline

- Campaign revision: 1.
- Player: Tang Wei.
- House authority: House Tang.
- 240 martial factions and 11,691 persistent faction people.
- Faction membership, training, economy, family, tournaments, travel, combat and autonomous world work are driven by current runtime/game authorities.
- Permanent anatomy loss persists while learned martial skill remains learned.
- Exact personal combat uses one continuous shared clock for all actors.
- Riding horses are conserved faction transport assets. Mounted exact combat uses the same clock and derives rider control from existing attributes and anatomy; there is no Riding proficiency.

## Verification

Fast current gate:

```bash
python tools/quick_check.py
python tools/test_changed.py
pytest -q tests/current
python tools/verify_jianghu_semantics.py
python tools/audit_state_bloat.py
python tools/verify_noop_roundtrip.py
```

Full release gate:

```bash
sh tools/verify_release.sh
```

Disposable simulation tools never mutate canonical `state/`.

## Deployment

This repository package is source and baseline state. Git push, Railway volume reset/deployment, MCP schema refresh and ChatGPT Skill installation are separate operations.
