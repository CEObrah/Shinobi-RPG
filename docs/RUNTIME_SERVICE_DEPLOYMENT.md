# Private runtime service: Railway, GitHub, OAuth, and ChatGPT Project

This repository uses a single-writer private deployment model:

```text
ChatGPT Project
    |
    | Shinobi Runtime MCP semantic operations
    v
Railway FastAPI/MCP service
    |
    +-- deterministic Shinobi runtime
    +-- persistent campaign checkout on Railway volume
    +-- private WAL/locks/receipts/recovery data
    |
    v
GitHub private production branch
```

GitHub is versioned source plus replicated campaign history. The Railway volume is the live writable checkout and recovery workspace.

## Authority boundary

Inside the campaign checkout:

- `runtime/` is deterministic engine source.
- `game/` is static Shinobi rules, schemas, content, and world definition.
- `state/` is mutable campaign truth.
- `plugins/shinobi-rpg/skills/shinobi-game-master/` is the canonical ChatGPT GM operating package.
- `docs/` contains human deployment and operations documentation.

Outside the checkout, `SHINOBI_RUNTIME_ROOT` stores transaction implementation data such as writer locks, WAL, recovery metadata, receipts, and temporary Git credential helpers. Those files are not RPG world state.

The public service exposes semantic gameplay operations only. It does not expose arbitrary file editing, shell access, JSON patching, Git commands, or autonomous-mode impersonation.

## Why Railway must follow every non-state commit

The production runtime uses exact Git remote durability. Before a new gameplay transaction, it fetches the configured production branch and requires the local checkout HEAD to equal the remote HEAD exactly. A direct GitHub commit therefore cannot be allowed to advance the production branch without also causing the Railway checkout to restart and fast-forward.

This applies even when the direct commit changes only Skill or documentation files. Those files may not change deterministic mechanics, but they still advance the shared branch HEAD.

The only normal commits that must not redeploy Railway are runtime-generated gameplay commits whose changed paths are entirely under `state/`. The running process already owns those commits and pushes them itself.

## Deployment watch policy

`railway.toml` uses ordered gitignore-style patterns:

```text
**
!/state/**
```

Meaning:

- every non-state repository change triggers a Railway deployment;
- a state-only gameplay commit does not trigger a deployment loop;
- a commit that changes both state and any non-state path still deploys;
- Skill/docs-only commits deploy so bootstrap can synchronize the persistent checkout to the new branch HEAD.

Do not replace this with a short allowlist such as only `runtime/**` and `game/**` while source and campaign state share the same production branch. Doing so can leave Railway behind GitHub and cause the next gameplay write to fail remote-synchronization preflight.

## Gameplay flow

```text
ChatGPT gameplay action
    -> Railway semantic command
    -> deterministic transaction
    -> state-only campaign mutation
    -> Git commit
    -> Git push to production branch
```

The running process already owns the transaction it just committed. The state-only commit is excluded from Railway deployment triggers.

## OOC development flow

```text
non-state repository change
    -> GitHub commit
    -> Railway deployment trigger
    -> bootstrap fetch / safe fast-forward
    -> runtime starts from current remote branch HEAD
```

Do not use direct GitHub state edits as the normal gameplay write path. They bypass runtime locking, WAL, idempotency, semantic validation, conservation rules, authority checks, and transaction receipts.

## Railway volume layout

Attach one persistent Railway volume at `/data`:

```text
/data/campaign    persistent Git checkout
/data/runtime     private WAL/locks/receipts/recovery data
```

Configure:

```text
SHINOBI_CAMPAIGN_ROOT=/data/campaign
SHINOBI_RUNTIME_ROOT=/data/runtime
SHINOBI_GIT_URL=https://github.com/OWNER/REPOSITORY.git
SHINOBI_GIT_REMOTE=origin
SHINOBI_GIT_BRANCH=<production-campaign-branch>
SHINOBI_GIT_TOKEN=<private credential>
```

Use one writer service instance for this filesystem/Git-backed campaign. Do not horizontally scale the mutable campaign writer.

## Startup and persistent checkout

Railway starts:

```text
PYTHONPATH=/app/runtime python -m shinobi_runtime.bootstrap
```

On first boot, bootstrap creates the configured campaign checkout on the mounted volume. On later boots it fetches the production branch, safely fast-forwards a clean checkout, preserves a recoverable local-ahead transaction commit, and refuses unsafe divergence instead of silently resetting state.

WAL recovery runs before the application begins serving campaign operations.

## Deployment teardown

`railway.toml` explicitly sets zero overlap and a bounded drain period:

```text
overlapSeconds = 0
drainingSeconds = 30
```

The service uses a persistent volume and must remain a single writer. Railway also prevents multiple active deployments from mounting the same service volume, but the explicit zero-overlap setting preserves the intended topology in configuration.

The runtime must still rely on WAL recovery rather than assuming every transaction will finish before shutdown.

## Git durability

Gameplay transactions mutate only declared campaign owners, validate/read back the result, commit the resulting transaction, and push it when remote durability is configured.

Remote durability requires local and remote branch equality before a new transaction. Push failures or unexpected remote heads fail closed and must never be narrated as durable success.

Because source, Skill/docs, and campaign state currently share one production branch, any direct non-state GitHub change must be followed by the automatic Railway deployment before consequential play resumes.

## MCP and OAuth

The ChatGPT connection uses the public HTTPS MCP endpoint with authenticated access. Configure the runtime's supported OAuth/JWT environment variables for the selected identity provider, including issuer, JWKS URL, audience, scopes, and exact allowed player subject.

The player-facing MCP/API accepts gameplay mode only. Faction and NPC autonomous actions are runtime-internal and are never exposed as a client-selectable impersonation mode.

Never commit or expose GitHub PATs, OAuth access tokens, Auth0 secrets, Railway secrets, passwords, or the MCP preview secret.

## ChatGPT Project modes

The dedicated Project should distinguish:

- normal gameplay / `IC:`: consequential actions call the runtime and narrate only committed results;
- `OOC:`: read-only discussion and inspection, with no world-time advance or mutation;
- `OOC DEV:`: source, rules, Skill, deployment, and repository maintenance, with no silent campaign-state edit.

A single message may contain multiple blocks. Resolve them in order. Consequential ambiguity fails closed.

## Skill deployment is separate from Railway deployment

The canonical ChatGPT GM package lives at:

```text
plugins/shinobi-rpg/skills/shinobi-game-master/
```

A Skill change on GitHub triggers Railway in the current shared-branch topology so the persistent checkout stays synchronized. That Railway deployment does not install the Skill into ChatGPT.

After changing the Skill:

1. validate and package the complete Skill as `skill.zip`;
2. commit the Skill source to GitHub;
3. allow the Railway deployment to synchronize the checkout;
4. upload the new `skill.zip` to ChatGPT.

Keep the GitHub Skill source and installed ChatGPT Skill synchronized.

## Campaign repair

A confirmed bad campaign fact caused by a software defect should be repaired through an explicit migration or campaign-repair transaction with provenance. Do not disguise state repair as a source-code edit and do not manually patch half-written campaign files around WAL recovery.

## Production acceptance checklist

Before resuming consequential live play after a meaningful non-state change:

- Railway deployment succeeded;
- bootstrap reached the current production branch HEAD;
- transaction recovery has no unresolved WAL entry;
- public API rejects non-gameplay command modes;
- stale and duplicate request behavior remains correct;
- schemas, templates, and domain validators accept the current campaign;
- hidden information remains absent from player-safe packets;
- remote Git durability remains configured and healthy;
- a non-state GitHub commit triggers Railway;
- a state-only runtime gameplay commit does not trigger Railway;
- Skill changes were also repackaged and uploaded to ChatGPT when applicable.

## Operations and recovery

Back up the Railway volume according to operational needs. GitHub is the replicated campaign-history remote, while the volume is the live writable checkout and recovery workspace. Both the campaign checkout and private WAL/recovery data must survive service restarts.

If startup finds an interrupted transaction, let runtime recovery resolve it. Do not manually edit around the transaction coordinator.
