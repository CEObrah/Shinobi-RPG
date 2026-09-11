# 2026-09-10 Combat simulation and narration repair

## Scope

Original game/subsystem: Shinobi RPG exact Jianghu combat, injury/functional projection, player combat command semantics, current-transition narration recovery, and GM combat presentation.

This repair was performed against the user-supplied local repository ZIP. It does not modify GitHub, deployment, MCP publication, the installed ChatGPT Skill, or the committed live campaign. Mutable `state/` is intentionally preserved.

## Confirmed failure classes and repairs

- Side-unknown coarse wounds could project severe generic limb penalties while `functional_capacity_factors` remained fully healthy. Generic lower/upper impairment is now preserved without inventing a left/right side or collapsing known unilateral anatomy.
- Repeated coarse-zone wounds were treated as certain repeated strikes on one identical structure. Coarse damage and bleeding now accumulate with bounded diminishing returns, while exact named-structure trauma retains exact cumulative behavior and permanent-damage thresholds.
- Direct spear thrusts inherited a broad generic melee contact width. Thrust now has a narrow authored direct-contact width, consumed by exact-combat geometry.
- Melee tracking used a threshold cliff that changed from committed-line behavior to current-position re-aiming. Tracking now blends committed aim toward current position continuously.
- Near-simultaneous attacks could erase an already committed physical guard because a fresh reaction-delay check failed tens of milliseconds later. A live oriented guard can now cover a rapid follow-up while remaining subject to angle, commitment, recovery, and control limits.
- Successful weapon defenses could still proceed almost automatically into body contact. Sufficiently sound parry/deflect/block resolution can now stop body contact before wound resolution; weak or late defenses still transmit into the normal contact path.
- Omitted `poison_ref` implicitly delegated finite poison expenditure. Omission now means no poison; explicit `poison_ref: auto` is the opt-in doctrine delegation.
- Relentless lethal natural-language intent is now explicitly preserved by the packaged GM contract as lethal `until_resolution`, with chase movement when pursuit was actually authorized, rather than being reducible to a lethal flag plus an arbitrary exchange count.
- Combat narrative projection was losing nested wound detail and over-sampling only the tail of long fights. Material-beat summaries now preserve bounded beginning/middle/end coverage and current-transition trimming remains terminating and stratified.
- The GM combat contract now requires full-span recovery before prose when the compact spine is insufficient, and explicitly rejects terminal-state/tail-only summaries and resolver-shaped prose.
- GM scene person projection now distinguishes physical visibility from identity knowledge during hidden-identity combat. Exact hidden names remain only inside explicitly GM-private direction until player-facing identity knowledge exists.

## Test-fixture hardening

Several synthetic combat regressions copied the current campaign player directly. Because the supplied save is legitimately mid-combat with Tang Wei incapacitated, those tests failed before reaching the invariant they intended to test. Their temporary copies now normalize synthetic combat physiology only inside the test fixture. Production availability rules and packaged campaign state are unchanged.

The pinned release-candidate baseline tests are intentionally not weakened. This ZIP's campaign has advanced beyond the revision-97 release baseline, so release-candidate assertions against the old pristine snapshot remain expected to fail on the current mutable save. That mismatch is classified as pre-existing release-state context, not repaired by rewriting campaign history or blessing the damaged revision as a new release baseline.

## Cross-game analogue status

The analogous Sword & Banners repository was not present in the supplied editing artifact or local workspace. The required cross-game analogue therefore could not be inspected or repaired in this local-ZIP task. This is a valid Shinobi-local repair, not a claim of cross-game global closure. No cross-game runtime, state, IDs, or mechanics were imported.

## Verification

Verification recorded from the extracted package itself:

- `python tools/quick_check.py`: PASS.
- Focused combat geometry/defense/pressure/resolution/narration regression slice: 56 passed.
- GM scene/director/parley/contract slice: 77 passed.
- New combat-quality + GM presentation repair suite: 16 passed.
- Synthetic wrapper + tactical movement fixtures after isolation from the live save: 17 passed.
- Core runtime invariants after fixture isolation: 5 passed.
- Qi-flow invariants after fixture isolation: 16 passed.
- `python tools/sync_gm_skill_contract.py --check`: PASS with token `32cf310a12684bc997e562df3f13ff1e3cb3c2d256080784de41f6d2f7c9e179`.
- Maintained `python tools/test_changed.py <changed paths>` completed 56 selected test files: 54 files passed, 1 additional split shard passed, and 2 files failed only on the supplied campaign no longer matching the pinned revision-97 release baseline (`test_release_campaign_state_contract.py` and the release-baseline assertion in `test_delivery_chain_contract.py`). The untouched ZIP reproduces that release-state mismatch, and the release invariant was intentionally not weakened.
- All 754 files under mutable `state/` have identical SHA-256 content before and after development.

The repaired health projection applied to the supplied current Tang Wei wounds now yields zero locomotion/walking/running/standing/combat-movement capacity instead of the prior all-1000 healthy projection, while vision and respiration remain independently derived.
