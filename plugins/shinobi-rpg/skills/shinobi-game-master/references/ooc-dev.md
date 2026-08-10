# OOC Development Procedure

## Contents

1. Development boundary
2. Repository roles
3. Source changes
4. Campaign repair
5. Git and Railway
6. Skill maintenance
7. Live-play feedback loop
8. Secrets and credentials
9. Resume live play

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
5. run relevant validators/tests when execution access is available;
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
OOC DEV non-state repository change
-> GitHub commit
-> Railway deployment trigger
-> bootstrap safe fetch/fast-forward
-> new runtime process at the remote branch head
```

The production Railway watch policy is `**` followed by `!/state/**`: every non-state commit redeploys, while a runtime-generated state-only gameplay commit does not. This is deliberate because Git remote durability requires the live checkout HEAD to equal the remote branch before the next gameplay transaction.

State-only gameplay commits must not create a deployment loop. Do not add a non-state file to routine gameplay transaction commits.

## Skill maintenance

Treat the GitHub Skill directory as canonical source for GM behavior.

When changing GM behavior:

1. update `SKILL.md` for always-relevant operating rules;
2. update the smallest relevant reference for detailed craft;
3. update runtime presentation contracts only when they independently encode the same player-facing contract;
4. avoid root-level duplicate manuals;
5. validate structure and references when tooling permits;
6. commit the complete coherent Skill change to GitHub;
7. refresh or replace the ChatGPT-installed Skill when the current environment exposes a supported Skill update mechanism;
8. verify the installed Skill is actually synchronized before claiming that it is.

Do not claim that a GitHub commit automatically updated the ChatGPT-installed Skill unless the installed `skills://` resource is verified to contain the change. Do not claim a direct ChatGPT Skill update when no writable Skill-management action is available.

## Live-play feedback loop

Use actual campaign play to find problems worth fixing. Good candidates include:

- player-facing contracts that hide required enum values or nullability;
- stale projections that contradict committed state;
- mechanics that block actions without explaining who, why, or until when;
- narration rules that produce confusing dialogue, mute NPCs, weak continuity, or missing choices;
- repetitive interaction patterns that make NPCs feel interchangeable;
- opportunities to deepen simulation without adding unnecessary complexity.

Record the observed symptom, identify its authoritative owner, distinguish bug from design opportunity, and make the smallest coherent correction. Never turn speculative improvement ideas into campaign truth.

## Secrets and credentials

Never request, print, commit, or expose GitHub PATs, Auth0 secrets, passwords, OAuth access tokens, Railway secrets, or the MCP preview secret.

Use configured secret stores and environment variables.

## Resume live play

After any non-state GitHub change, confirm the live runtime is synchronized with the relevant source before relying on changed runtime behavior in consequential IC play.

For Skill-only narration changes, distinguish GitHub source from the ChatGPT-installed Skill. Verify the installed Skill separately before claiming new narration behavior is active in the current ChatGPT environment.
