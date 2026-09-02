# Shinobi RPG: Jianghu Campaign

Persistent deterministic Jianghu RPG operated through ChatGPT and the Shinobi runtime. The Python package/runtime name remains `shinobi_runtime` for deployment continuity; the live game rules are Jianghu rules.

## Authority

- `runtime/`: deterministic execution, transactions, scheduling, combat, health, economy, training and simulation.
- `game/`: current static rules, formulas, equipment, factions, locations and reference data.
- `state/`: minimum sufficient current campaign truth.
- `plugins/shinobi-rpg/skill/shinobi-game-master/`: ChatGPT GM procedure and presentation.
- `tests/`: current behavior verification only.

Development history is not campaign state. This package is an explicit **revision-1 rebaseline** of the repaired current world snapshot. The pre-rebaseline revision-158 state is preserved only under `docs/forensics/` for failure analysis. Gameplay idempotency receipts and WAL state are runtime-private and must begin fresh for this baseline.

## AI-native scene direction

The deterministic runtime owns world truth, physical/mechanical consequences, chronology, knowledge boundaries, conservation, and persistence. ChatGPT owns scene direction and prose. The live `gm_scene_context.scene_direction` packet is a compact, non-authoritative handoff that identifies current actors, open human/practical threads, recent causal material, and protected player decisions; the repository Game Master Skill supplies the detailed directing doctrine.

Present NPCs are active agents rather than response functions. The LLM may stage grounded reversible dialogue, interruption, cross-talk, practical activity, humor, refusal, silence, and incidental behavior without waiting for a Python dialogue script or player activation. A shown scene must normally advance through a new human/practical/causal beat or compress/transition; paraphrase-only turns and stock nod/pause/gaze filler are explicitly invalid presentation. Scene completion is equally important: once current pressure is spent, the LLM transitions instead of keeping the same tableau alive. Contested physical outcomes remain runtime-owned.


Formal scene sessions are continuity tools, not story locks. The LLM decides narrative scene start/continue/transition/end from lived pressure. Fresh physical projection removes departed people from live dialogue eligibility while retaining them only as explicit continuity-only absent participants when needed for cleanup/resumption; an unanswered thread never makes an absent NPC speak. Active-session actors and people tied to the current event/process are prioritized ahead of general site attendance, while hard movement, time, combat, money, authority, injury, and other durable consequences remain runtime-owned.

## Current campaign baseline

- Campaign revision: **1** (new canonical baseline; pre-rebaseline revision 158 is forensic only).
- Player: Tang Wei.
- House authority: House Tang.
- 240 martial factions, 11,789 persistent faction members, 2 persistent independents, and 45 exact civic identities (11,836 exact people total; 11,791 martial identities).
- Faction membership, training, economy, family, tournaments, travel, combat and autonomous world work are driven by current runtime/game authorities.
- Permanent anatomy loss persists while learned martial skill remains learned.
- Strategic routes use segmented terrain (including forest, marsh and desert where appropriate), and route/site environment is projected into exact combat.
- Faction founding, splitting, merging and peaceful dissolution conserve existing people/assets/property; annual autonomous evolution is causal rather than random spawning.
- Exact personal combat uses one continuous shared clock for all actors.
- Sparse Jianghu social causality is production-active: unresolved life debts/promises/vengeance, exact beliefs with evidence/investigation, explicit vows, derived conflicting-loyalty camps, small-fight opponent adaptation, and temporary shared-war coalitions. These are bounded current facts, not history ledgers.
- Ordinary riding/pack transport is conserved as pooled faction logistical capacity rather than thousands of exact animal objects. Exceptional named mounts may still be exact when consequential. Mounted exact combat uses the same clock and derives rider control from existing attributes and anatomy; there is no Riding proficiency.
- Tang Wei currently travels with permanent companions Jiang Li, Han Chaohong and Fu Pengzhou. The standing retinue is an open-ended identity roster rather than a fixed three-slot party; mission-only reinforcements remain temporary.
- House Tang remains sword-first with formation cohesion 95, no bow doctrine, and needles as a secondary hidden-weapon art. Wei's four-person core carries Jian + needles; no House Tang archer pool is fabricated.
- Wei's retinue has the campaign's only bespoke player-team doctrine: guard-first principal protection, four-sector back-to-back defense when the exact four-person core is outnumbered/surrounded, and defeat-in-detail once local numerical superiority appears.
- Poison exposure is deterministic and progressive. Cardiotoxic poison is House Tang's current lethal reserve, while plain versus poisoned hidden-weapon throws remain separate physical choices with conserved ammunition/doses.
- House gameplay now has persistent institutional missions: House assignment offers, councils/briefings, lawful authorization, delegated commanders, exact musters, existing physical strategic travel/warfare, allied calls to arms, bounded reconnaissance, council-authorized negotiated settlements, compact after-action reports, treasury-backed rewards and service/career consequences. `get_play_context` advertises active missions and `inspect_game_object` accepts `mission:` refs.

## Verification

The maintained release gate is:

```bash
sh tools/verify_release.sh
```

It compiles the runtime/tools/tests, runs the structural `quick_check`, the focused changed-owner core gate, every maintained `tests/current/test_*.py` module in isolated processes, the Jianghu semantic audit, state-bloat authority audit, deterministic no-op round trip, development projections, and disposable 1/7/30/90-day simulation horizons. The current repository contains 66 maintained current test modules.

The 90-day simulation is release-blocking. Independent 365-day A/B soaks remain available as deliberate extended QA when a major change warrants it:

```bash
SHINOBI_EXTENDED_SOAK=1 sh tools/verify_release.sh
```

Pull requests and pushes to `main` also run `.github/workflows/verify.yml` on a clean standard GitHub runner. Local verification, GitHub CI/merge, Railway deployment, live playtesting, MCP refresh, and installed Skill state remain separate evidence.

Disposable simulation tools never mutate canonical `state/`. Release evidence written under `artifacts/final-*.json` is verification output, not campaign authority.

## Deployment

This repository package is source and baseline state. Git push, Railway volume reset/deployment, MCP schema refresh and ChatGPT Skill installation are separate operations.

## Final audit status

The packaged campaign baseline is revision **1** at `SE-0061-09-27T21:15:54`. Source and gameplay durability both use the single canonical Git branch `main`. Deploy only after committing this revision-1 package to `main` and clearing the old Railway campaign checkout plus private recovery/WAL/receipt store. The pre-rebaseline revision-158 snapshot remains available under `docs/forensics/`, so no legacy campaign branch is required.
