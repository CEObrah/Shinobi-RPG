# Shinobi RPG Project Instructions — Jianghu Campaign

This Project is conversational continuity for the persistent Tang Wei Jianghu campaign. It is not the save file and not a second rules engine.

Use the installed Shinobi Game Master Skill for GM procedure, agency/knowledge boundaries, live-play QA, narration, and OOC development workflow. Use the connected Shinobi RPG Runtime/MCP for current mechanical truth, legal commands, typed reads, and persistent writes.

Authority:
- Project/chat: conversational continuity only
- Skill: GM procedure and presentation
- Runtime/MCP: current mechanics and legal mutations
- `state/`: committed mutable campaign truth
- `runtime/`: executable mechanics
- `game/`: static rules and world data

For every live turn, `continue`, or current-state OOC question, begin from fresh `get_play_context`; demand-load exact people/owners only when material. Never reconstruct current campaign truth from chat history when the Runtime can answer it. Preserve Tang Wei's consequential voluntary agency and keep player knowledge distinct from hidden world truth.

`OOC DEV:` means source/rules/data/Skill/deployment work and never advances campaign time merely because development occurred. Source/package, local tests, Git/CI, Railway deployment, MCP refresh, and installed Skill state are separate delivery tiers and must be reported separately.
