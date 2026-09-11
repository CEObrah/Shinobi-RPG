# OOC Development

`OOC DEV:` is the explicit source/rules/data/Skill/deployment command. Development never advances campaign time.

Repository authority:
- `runtime/`: deterministic reducers, transactions, scheduler, APIs
- `game/`: static mechanics and world data
- `state/`: mutable campaign truth
- repository Skill: GM procedure/presentation source

Before changing behavior, inspect the current authority and update its schema/template/tests together. Preserve one writable authority, closed payloads, exact revision checks, transaction idempotency, conservation, direct identity routing, and fail-closed validation.

Do not hide campaign-truth repair inside a refactor. Do not reroll established people during representation changes. Never weaken a sound invariant merely to make a test green.


## Cross-game analogous-defect rule

When a confirmed defect is found in this game, classify whether it is P0, P1, or a systemic/repeating P2. Before calling that defect **globally fixed**, audit the analogous subsystem in the other RPG as an independent codebase. If the same failure pattern exists there, repair it there too and run the smallest relevant regression in both repositories. If the other game already has the stronger design, record the concept to port without creating runtime imports, shared campaign state, cross-game IDs, or source dependencies.

The development ledger for such a fix should explicitly record: original game/subsystem, analogous subsystem checked, whether the defect reproduced, what was repaired in each game, and what verification actually ran. A local fix in one repository is still valid, but it is not a cross-game closure until the analogue has been inspected.

## Root-fix development contract

When the player invokes `OOC DEV:`, classify the failing authority before editing and repair the causal owner rather than masking the symptom in narration, a one-off state patch, or a downstream exception. Search for sibling paths that express the same invariant (for example combat entry/aftermath, projectile/autonomous variants, outbound/return route phases, or exact/aggregate projections), add a regression that reproduces the actual failure, and apply the cross-game analogous-defect rule before claiming global closure. Preserve current campaign state unless an explicit, separately justified state repair is requested.

If the player explicitly supplies an uploaded/local repository ZIP and scopes work to that artifact, that local workspace is the requested editing surface. Do not substitute GitHub work for it. Run the maintained local gates against the extracted package, verify mutable `state/` stayed byte-identical unless a state repair was explicitly requested, then clean and repackage the complete repository. GitHub, CI, deployment, MCP refresh, installed Skill, and local ZIP delivery remain separate tiers.

## Verification loop

Run local maintained verification first:

```text
python tools/quick_check.py
python tools/test_changed.py <changed paths>
```

`quick_check.py` is the fast structural/package gate. `test_changed.py` always runs the small core contract/invariant slice and adds maintained subsystem regressions from the actual changed paths; long-horizon soak tests remain deliberate release/simulation work. For a release candidate, run any deeper package integrity/replay checks the changed subsystem requires. A test that did not run is neither passing nor failing.

After a branch/PR is pushed, inspect the repository's required GitHub Actions. The hosted `verify` workflow intentionally reruns the maintained gates on a clean standard runner: structure/command checks, Jianghu semantics, hot-state bloat separation, deterministic no-op round trip, and changed-owner regressions. It does not run long-horizon soak work on every PR. A red required check blocks merge until the actual cause is classified and repaired: implementation defect, stale/incorrect test, bad fixture, dependency/environment failure, or CI workflow defect. Local green does not prove CI green; CI green does not substitute for local verification. Merge only after required checks are green.

GitHub Actions is development QA, never campaign/mechanical authority, and normal gameplay never polls it. After required checks are green, the default `OOC DEV:` policy is to merge automatically unless the player explicitly asks to review first, hold the PR, or avoid merge. After merge, verify Railway source-head/deployment sync and the smallest safe live smoke path before treating production as updated, unless the player explicitly scopes deployment verification out of the task. Live play/playtesting then remains the final integration layer.

## Skill source and packaging order

Use the editing surface the player explicitly requested. For GitHub work, repository source remains authoritative and normal branch/CI/merge/package rules apply. For an explicitly supplied local/uploaded repository ZIP, edit and validate that extracted repository directly and return the cleaned replacement artifact without silently substituting GitHub. Never imply that a local ZIP changed GitHub, deployment, MCP, or the installed ChatGPT Skill.

A local ZIP, local test pass, Git commit, GitHub merge, Railway deployment, MCP refresh, installed Skill update, and packaged Skill are distinct delivery tiers. Never imply one merely because another occurred.
