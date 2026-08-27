# Shinobi RPG: Jianghu Campaign

Persistent deterministic Jianghu RPG operated through ChatGPT and the Shinobi runtime. The Python package/runtime name remains `shinobi_runtime` for deployment continuity; the live game rules are Jianghu rules.

## Authority

- `runtime/`: deterministic execution, transactions, scheduling, combat, health, economy, training and simulation.
- `game/`: current static rules, formulas, equipment, factions, locations and reference data.
- `state/`: minimum sufficient current campaign truth.
- `plugins/shinobi-rpg/skills/shinobi-game-master/`: ChatGPT GM procedure and presentation.
- `tests/`: current behavior verification only.

Development history is not campaign state. The packaged save is the current canonical campaign checkpoint at revision 85 and contains only current campaign truth. Gameplay idempotency receipts are runtime-private and are created only by future committed commands.

## Current campaign baseline

- Campaign revision: 85.
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

The institutional gameplay build has completed the maintained current regression sweep: **60 test files / 575 tests passed**, with the source-focused changed-owner gate at **173/173**, structural quick check PASS, Jianghu semantics PASS, state-bloat audit PASS, and deterministic no-op round trip PASS. The full suite was run in bounded file batches because one monolithic invocation exceeded the execution window.

No fresh 90-day/365-day soak was run for this institutional pass; older simulation artifacts remain historical evidence for their own release boundaries.

Fast current gate:

```bash
python tools/quick_check.py
python tools/test_changed.py <changed paths>
pytest -q tests/current
python tools/verify_jianghu_semantics.py
python tools/audit_state_bloat.py
python tools/verify_noop_roundtrip.py
```

Release gate:

```bash
sh tools/verify_release.sh
```

The maintained release-blocking simulation horizon is 90 days. A dual independent 365-day A/B soak remains available as deliberate extended QA when a major change warrants it:

```bash
SHINOBI_EXTENDED_SOAK=1 sh tools/verify_release.sh
```

Pull requests and pushes to `main` also run `.github/workflows/verify.yml` on a clean standard GitHub runner. The hosted gate uses the same maintained checks: structure/command surface, Jianghu semantic invariants, state-bloat authority separation, deterministic no-op round trip, and focused regressions selected from actual changed owners plus the small core invariant slice. It deliberately does not add unrelated synthetic checks merely to inflate CI. Local verification, GitHub CI/merge, Railway deployment, live playtesting, MCP refresh, and installed Skill state remain separate evidence.

Disposable simulation tools never mutate canonical `state/`.

Current packaged release evidence is summarized in `FINAL-RELEASE-STATUS-2026-08-27.md`. `artifacts/final-1d.json`, `artifacts/final-7d.json`, and `artifacts/final-30d.json` are fresh simulation evidence for this final audit. The packaged 90-day and independent long-horizon certifications remain historical baseline evidence from the immediately preceding release line and are not presented as fresh recertification of this exact final build.

## Deployment

This repository package is source and baseline state. Git push, Railway volume reset/deployment, MCP schema refresh and ChatGPT Skill installation are separate operations.

## Final audit status

The current packaged campaign is revision 85 at `SE-0061-09-14T09:15:00`. The current release boundary, regression results, conservation certification, long-horizon deterministic coverage, and deployment distinction are documented in `FINAL-RELEASE-STATUS-2026-08-27.md`.
