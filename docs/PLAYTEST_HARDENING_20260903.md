# Shinobi RPG Playtest Hardening — 2026-09-03

This pass is driven by observed gameplay failures, especially the live Chang'an -> Mount Hua Road melee. Structural checks remain smoke tests; acceptance is replay of the actual broken player-facing scenarios.

## Implemented behavior

- Compound combat intent preserves both Wei's personal combat and bounded ally tasking in one semantic action through `ally_orders`; a required support component is not silently discarded and then narrated as though it failed.
- Retinue combat support supports `reach`, `protect`, `extract`, and `treat` objectives. A `treat` objective persists across exchanges rather than disappearing after the medic begins moving.
- Emergency field treatment requires physical access and uninterrupted treatment time, reuses the existing wound-treatment/stabilization mechanics, consumes real carried medical supplies, and does not replace later infirmary care or erase anatomical injury.
- Standing field medics no longer default back into assault behavior merely because the general retinue doctrine favors concentration/defeat-in-detail. They preserve support capacity unless explicit orders or casualty logic give them a support task.
- Field-medic assignment provisions registered portable medical equipment from exact faction stock. Existing held equipment counts toward the issue; shortages remain shortages; nothing is minted.
- Incapacitated casualties remain physical bodies but transition to fallen collision geometry, allowing rescue/treatment and obstruction to be resolved from real local space.
- Autonomous friendly-line safety prevents newly committing an unsafe attack through a friendly first-contact body while preserving genuine friendly fire from already-committed trajectories/movement.
- Current-transition recovery remains bounded/paginated and is not required as the normal narration path.
- Exact-combat player-facing accounting distinguishes cumulative observation from current observed pressure and avoids treating legacy missing tally data as a false zero.

## Narrow current-save equipment repair

Han Chaohong (`mw.person.house_tang.1032`) was already the standing field medic, but the old assignment path had never physically issued the role's kit. The supplied save is corrected by transferring one physician's kit and four medical bundles from House Tang inventory into Han's loadout with House Tang ownership/provenance retained. House stock is debited by the same quantities. No bodies, money, or equipment are created, and campaign time/revision is not advanced by this OOC repair.

## Cross-game analogous audit

The same recurring failure classes were reviewed in Sword & Banners independently: silent partial actions, plan-without-executor autonomy, misleading force projections, schema/write drift, wait-frontier failures, and swallowed required subsystem errors. Repairs remain repository-local with no cross-game imports or state dependencies.

## Verification actually run

Behavior regressions passed locally:

- `test_play_failure_matrix.py` + `test_play_regression_hardening.py` + friendly-line/transition regressions — 11 passed
- retinue doctrine/persistence/selection slice — 23 passed after replacing the stale expectation that a field medic must attack
- `tools/quick_check.py` — PASS (structure, command surface, rule consumers, state ownership)

The broad changed-file gate was also started and progressed beyond half of its selected suite without an assertion failure, but exceeded the per-command execution window; that timeout is not counted as a pass. Focused play regressions above are the acceptance evidence for this build.

Transient test caches and bytecode are excluded from the playtest package. This build is hardened against the failures above; it is not a claim that no undiscovered gameplay bug remains.
