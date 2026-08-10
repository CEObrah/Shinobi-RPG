# OOC Development Procedure

## Contents

1. Development boundary
2. Repository roles
3. Source changes
4. Campaign repair
5. Git and Railway
6. Skill maintenance
7. Secrets and credentials
8. Resume live play

Use this reference only for `OOC DEV:` work.

## Development boundary

Treat development as separate from gameplay.

- Do not advance world time because code or documentation changed.
- Do not turn design discussion into campaign state.
- Do not silently repair state while modifying source.
- Do not use player-facing gameplay commands as a substitute for source changes.
- Do not use direct state editing as normal gameplay.

## Repository roles

Use:

- `runtime/` for engine source;
- `game/` for static rules/content/world definitions;
- `state/` for mutable campaign truth;
- `plugins/shinobi-rpg/skills/shinobi-game-master/` for canonical ChatGPT GM procedure and narration craft;
- `docs/` for deployment/operations documentation.

Read `references/runtime-architecture.md` and `references/repository-map.md` before broad architectural changes.

## Source changes

For requested runtime/game work:

1. inspect the exact current source and contracts;
2. identify the authoritative owner of the behavior;
3. make the smallest coherent change;
4. update tests/contracts/docs that define the same behavior;
5. run relevant validators/tests;
6. commit with a specific message;
7. verify deployment/compatibility when the changed path is deployment-sensitive.

Do not create compatibility layers that become second writable authorities.

## Campaign repair

If a software bug produced a confirmed bad campaign fact:

1. diagnose the source bug separately;
2. fix the source/rule;
3. identify exact corrupted campaign owners and provenance;
4. use an explicit migration or campaign-repair transaction;
5. validate and record the repair;
6. never rewrite history casually or conceal the repair inside unrelated source work.

## Git and Railway

Treat the Railway volume checkout as the live writable campaign workspace and GitHub as versioned source plus replicated campaign history.

Gameplay flow:

```text
ChatGPT action
-> Runtime semantic command
-> deterministic transaction
-> state change
-> Git commit/push
```

Development flow:

```text
OOC DEV source/game change
-> GitHub commit
-> deployment trigger when watched paths changed
-> bootstrap safe fetch/fast-forward
-> new runtime process
```

State-only gameplay commits must not create a deployment loop.

## Skill maintenance

Treat the Skill directory as canonical GM documentation.

When changing GM behavior:

1. update `SKILL.md` for always-relevant operating rules;
2. update the smallest relevant reference for detailed craft;
3. update runtime presentation contracts only when they independently encode the same player-facing contract, such as `choice-presentation.json`;
4. avoid root-level duplicate manuals;
5. validate the Skill;
6. package the complete Skill as `skill.zip` for ChatGPT upload;
7. keep the GitHub source and uploaded Skill synchronized.

## Secrets and credentials

Never request, print, commit, or expose GitHub PATs, Auth0 secrets, passwords, OAuth access tokens, Railway secrets, or the MCP preview secret.

Use configured secret stores and environment variables.

## Resume live play

After meaningful runtime/game changes, confirm the deployed runtime is compatible before resuming consequential IC play.

After Skill-only changes, upload the new validated Skill before expecting new GM behavior in fresh chats.
