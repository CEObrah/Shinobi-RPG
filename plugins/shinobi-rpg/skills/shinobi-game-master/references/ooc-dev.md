# OOC Development

Development never advances campaign time.

Repository authority:
- `runtime/`: deterministic reducers, transactions, scheduler, APIs
- `game/`: static mechanics and world data
- `state/`: mutable campaign truth
- repository Skill: GM procedure/presentation source

Before changing behavior, inspect the current authority and update its schema/template/tests together. Preserve one writable authority, closed payloads, exact revision checks, transaction idempotency, conservation, direct identity routing, and fail-closed validation.

Do not hide campaign-truth repair inside a refactor. Do not reroll established people during representation changes.

Normal local verification:

```text
python tools/quick_check.py
python tools/test_changed.py <changed paths>
```

For a release candidate, run the maintained Jianghu test suite and package integrity check. A test that did not run is neither passing nor failing.

A local ZIP does not imply repository commit, deployment, MCP refresh, or installed Skill update.
