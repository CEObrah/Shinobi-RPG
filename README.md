# Shinobi RPG

Persistent ChatGPT-operated campaign repository.

The canonical ChatGPT game-master operating package lives under:

`plugins/shinobi-rpg/skills/shinobi-game-master/`

Use these Skill references for human/OOC DEV orientation:

- GM operating procedure: `plugins/shinobi-rpg/skills/shinobi-game-master/SKILL.md`
- Narration and voice: `plugins/shinobi-rpg/skills/shinobi-game-master/references/narration.md`
- Player interface: `plugins/shinobi-rpg/skills/shinobi-game-master/references/player-interface.md`
- Runtime architecture: `plugins/shinobi-rpg/skills/shinobi-game-master/references/runtime-architecture.md`
- Repository/update map: `plugins/shinobi-rpg/skills/shinobi-game-master/references/repository-map.md`
- OOC development procedure: `plugins/shinobi-rpg/skills/shinobi-game-master/references/ooc-dev.md`

Repository authority remains:

- `runtime/`: deterministic execution, transactions, scheduling, reducers, and APIs.
- `game/`: static rules, schemas, content, world definitions, and canon/reference data.
- `state/`: mutable truth for the current campaign.

Deployment and private Runtime/MCP/Railway setup lives in `docs/RUNTIME_SERVICE_DEPLOYMENT.md`.

`README.md`, Skill prose, tests, indexes, caches, narration, chat history, and model memory are never mutable campaign truth.
