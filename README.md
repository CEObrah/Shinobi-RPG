# Shinobi RPG

Self-contained persistent Shinobi campaign/runtime for ChatGPT.

## Authority

- `runtime/` - deterministic commands, reducers, scheduling, transactions, persistence, and MCP/API surfaces.
- `game/` - static mechanics, schemas, content, world definitions, and reference data.
- `state/` - mutable committed truth for the current Wei Tang campaign.
- `plugins/shinobi-rpg/skills/shinobi-game-master/` - canonical ChatGPT GM operating and narration package.

Chat history, model memory, docs, tests, indexes, and narration are never campaign authority.

## Verify

Install the runtime dependencies, then run:

```text
python tools/quick_check.py
python tools/test_changed.py <changed paths>
python -m pytest -q -p no:cacheprovider tests/runtime
```

`quick_check.py` is the fast structural/current-contract gate. The pytest suite is the release regression authority.

## Deploy

Railway/private MCP deployment and Git durability are documented in `docs/RUNTIME_SERVICE_DEPLOYMENT.md`.

The runtime-generated campaign writes are state-only. Under the shipped Railway watch policy, any non-state source/Skill/docs/test/tool/workflow change triggers deployment synchronization; state-only gameplay commits do not.

## ChatGPT Skill

The complete Skill source is:

`plugins/shinobi-rpg/skills/shinobi-game-master/`

Package and install the whole Skill directory. Repository source, Railway deployment, MCP/app schema refresh, and ChatGPT Skill installation are separate delivery states.
