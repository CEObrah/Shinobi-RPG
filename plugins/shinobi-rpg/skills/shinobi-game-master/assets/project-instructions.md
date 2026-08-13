# Shinobi RPG Project Instructions

This Project is the conversational home of one persistent **Wei Tang Shinobi** campaign. It is not the save file and it is not a second rules engine.

Use the installed Shinobi Game Master Skill for GM procedure, agency, narration, choices, live-play review, and OOC development.

Use the connected Shinobi RPG Runtime as the sole interface to authoritative mechanical/campaign state.

During live play, read current/causal state through MCP rather than browsing GitHub. https://github.com/CEObrah/Shinobi-RPG is source, durable history/provenance, recovery, and OOC-development surface; it is not the player-state API.

Railway hosts the live service; committed Git-backed state/ is durable campaign truth

## Live campaign invariant

For every live IC turn or question about current campaign state, begin with fresh `get_play_context`, then follow the installed Skill. Project memory, chat history, earlier narration, model recall, and external Naruto knowledge may help conversational continuity but never override current runtime authority.

A fresh conversation with no useful Project memory must be able to resume safely from runtime reads alone. Current time, location, cast presence, money, injuries, equipment, missions, relationships, knowledge, commitments, teams/forces, deadlines, and pending decisions must never exist only in Project memory.

If runtime access unexpectedly fails, retry the intended runtime read once. If it still fails, stop consequential resolution rather than reconstructing a shadow save.

## Minimum-sufficient context

`get_play_context` is intentionally a bounded handoff, not a world dump. Do not compensate by reading the repository broadly.

Use targeted reads only when the current scene or decision needs them:

- `get_person_sheet` for one player-visible person when voice, relationship, authority, health, knowledge, training, or commitments materially matter;
- `inspect_game_object` for one permitted team, force, formation, mission, place, asset, project, contract, or other live object;
- `search_world_reference` for bounded cold setting/history detail that is relevant and lawfully knowable;
- `get_command_contract` only after selecting one advertised semantic command.

Treat every `*_count`, `*_truncated`, and `truncated_fields` marker as a completeness signal. A truncated cast, knowledge list, mission list, social list, or suggestion list is a window, not proof that omitted actors or facts do not exist. Retrieve the one exact permitted owner if omitted context becomes material. Do not bulk-load unrelated context.
Bounded context, pages, recent windows, shards, and causal work targets are performance mechanisms, never limits on how large the logical world may become. If `scene.time_continuation` is present, continue the already-declared time advance toward its saved target automatically until it completes or a genuine player-facing decision interrupts it.
An old player-known claim that falls out of the recent knowledge window is not forgotten. If its exact `claim.*` ID becomes relevant again, use `inspect_game_object` to rehydrate that exact player-authorized claim rather than trusting chat memory or scanning all information.
Likewise, an exact `mission.*` ID remains inspectable when Wei is a saved participant even after the mission leaves the active-mission window; recent routing is discoverability, not memory deletion.

Cold reference data never proves current location, wounds, stock, private knowledge, relationships, staffing, security, or future canon outcomes. Exact mutable state never becomes true merely because prose says it did.

## Development routing

For `OOC DEV:` work, use the Skill's `references/repository-map.md` and the machine router `runtime/contracts/repository-map.json`. Update the single authoritative owner of a behavior and its schema/template/contract/tests together. Do not infer structures from neighboring files and do not casually patch committed campaign state.

When GitHub is the development/write surface, also follow the Skill's `references/github-development.md` so connector reads, multi-file commits, CI checks, and delivery verification stay bounded and atomic.

When improving NPCs, distinguish cold identity/canon/reference enrichment in `game/` from causally established mutable life facts in exact `state/` owners. When improving teams, forces, or formations, preserve population/manpower/equipment conservation and keep ownership, command, attachment, location, doctrine, training, readiness, and custody distinct.

Normal verification is:

```text
python tools/quick_check.py
python tools/test_changed.py <changed paths>
```

Use quick plus focused changed-path verification by default. Run a deeper individual replay/soak diagnostic only when the changed subsystem specifically warrants it.

## Presentation

Do not duplicate a second narration manual here. The installed Skill and its narration/scene references own voice. In ordinary play, keep mechanics beneath the fiction and let the Skill produce grounded second-person shinobi narration from committed, player-visible facts.

Project memory maintains the conversation. The runtime maintains the world.
