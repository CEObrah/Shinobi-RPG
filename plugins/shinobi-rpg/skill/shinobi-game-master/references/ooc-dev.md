# OOC Development

`OOC DEV:` is the explicit source/rules/data/Skill/deployment command. Development never advances campaign time.

Repository authority:
- `runtime/`: deterministic reducers, transactions, scheduler, APIs
- `game/`: static mechanics and world data
- `state/`: mutable campaign truth
- repository Skill: GM procedure/presentation source

Before changing behavior, inspect the current authority and update its schema/template/tests together. Preserve one writable authority, closed payloads, exact revision checks, transaction idempotency, conservation, direct identity routing, and fail-closed validation.

Do not hide campaign-truth repair inside a refactor. Do not reroll established people during representation changes. Never weaken a sound invariant merely to make a test green.

## Verification loop

Run local maintained verification first:

```text
python tools/quick_check.py
python tools/test_changed.py <changed paths>
```

`quick_check.py` is the fast structural/package gate. `test_changed.py` always runs the small core contract/invariant slice and adds maintained subsystem regressions from the actual changed paths; long-horizon soak tests remain deliberate release/simulation work. For a release candidate, run any deeper package integrity/replay checks the changed subsystem requires. A test that did not run is neither passing nor failing.

After a branch/PR is pushed, inspect the repository's required GitHub Actions. The hosted `verify` workflow intentionally reruns the maintained gates on a clean standard runner: structure/command checks, Jianghu semantics, hot-state bloat separation, deterministic no-op round trip, and changed-owner regressions. It does not run long-horizon soak work on every PR. A red required check blocks merge until the actual cause is classified and repaired: implementation defect, stale/incorrect test, bad fixture, dependency/environment failure, or CI workflow defect. Local green does not prove CI green; CI green does not substitute for local verification. Merge only after required checks are green.

GitHub Actions is development QA, never campaign/mechanical authority, and normal gameplay never polls it. After required checks are green, the default `OOC DEV:` policy is to merge automatically unless the player explicitly asks to review first, hold the PR, or avoid merge. After merge, verify Railway source-head/deployment sync and the smallest safe live smoke path before treating production as updated. Live play/playtesting then remains the final integration layer.

A local ZIP, local test pass, Git commit, GitHub merge, Railway deployment, MCP refresh, and installed Skill update are distinct delivery tiers. Never imply one merely because another occurred.
