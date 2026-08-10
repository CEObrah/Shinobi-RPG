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
7. trigger the ChatGPT Skill change from the complete canonical Skill bundle when the conversation exposes the review/edit flow;
8. let the user review and accept the ChatGPT Skill change;
9. after acceptance, verify `skills://shinobi-game-master` contains the changed files and markers before claiming synchronization.

For supported in-chat Skill updates, use this sequence explicitly:

```text
edit canonical GitHub Skill source
-> validate the complete Skill
-> trigger/stage the ChatGPT Skill change
-> user reviews and accepts the change
-> verify the installed `skills://` bundle afterward
```

Do not treat creation of a review card or pending edit as installation success. Until the user accepts it, the prior installed Skill remains authoritative for what ChatGPT has actually loaded. After acceptance, re-list the installed Skill resources and read at least one changed marker or file before reporting success.

Prefer this supported direct ChatGPT Skill update over asking the user to manually download and re-upload a ZIP. If the review/edit flow is unavailable or fails to surface, package the complete validated Skill as `skill.zip` as a fallback and state that the installed Skill has not yet been synchronized.

Do not claim that a GitHub commit automatically updated the ChatGPT-installed Skill unless the installed `skills://` resource is verified to contain the change. Do not claim a direct ChatGPT Skill update before the review has been accepted and verified.

## Live-play feedback loop

Use actual campaign play as continuous integration, playtesting, narrative review, and feature discovery. Read `references/live-play-review.md` for the full quality rubric.

Judge the game across all relevant layers, including:

- narration clarity, scene transitions, pacing, repetition, atmosphere, and decision handoff;
- character voice, dialogue, relationships, cast clarity, NPC-to-NPC interaction, and social continuity;
- combat mechanics, tactical depth, action economy, geometry, counters, objectives, resource pressure, injuries, AI behavior, and balance;
- combat narration, exchange readability, technique presentation, spatial continuity, consequences, uncertainty, and ally/opponent agency;
- training, teams, travel, economy, equipment, missions, relationships, institutions, family, forces, intelligence, and other feature workflows;
- player-interface discoverability, confusing command contracts, opaque rejection reasons, unnecessary friction, and missing repeated capabilities;
- simulation fairness across villages, clans, institutions, factions, exact characters, cohorts, and aggregate actors;
- progression, economy, institutional throughput, autonomous world behavior, canon pressure, and long-run simulation depth.

For a meaningful finding, record the observed symptom and player impact, identify the authoritative owner, classify confidence, distinguish defect from tuning or feature opportunity, suggest the smallest reusable correction, and identify a regression check.

Urgent correctness, agency, false-truth, exploit, or consequential-decision problems should be surfaced immediately. Lower-severity craft and design findings should wait for a natural stopping point so the review loop does not damage pacing more than the issue it is trying to fix.

Repeated symptoms carry more weight than isolated outcomes. Do not rebalance combat because of one lucky exchange, rewrite narration doctrine because of one awkward sentence, or add a major feature because of one hypothetical edge case.

During ordinary play, proactively suggest worthwhile GitHub changes without silently applying them. When the player explicitly requests development or implementation, edit the correct source owner and keep campaign-state repair separate from source improvement.

Never turn speculative improvement ideas into campaign truth.

## Secrets and credentials

Never request, print, commit, or expose GitHub PATs, Auth0 secrets, passwords, OAuth access tokens, Railway secrets, or the MCP preview secret.

Use configured secret stores and environment variables.

## Resume live play

After any non-state GitHub change, confirm the live runtime is synchronized with the relevant source before relying on changed runtime behavior in consequential IC play.

For Skill-only narration changes, distinguish GitHub source from the ChatGPT-installed Skill. Verify the installed Skill separately before claiming new narration behavior is active in the current ChatGPT environment.
