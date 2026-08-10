# Private runtime service: Railway, GitHub, OAuth, and ChatGPT Project

This repository uses a single-writer private deployment model:

```text
ChatGPT Project
    |
    | Shinobi plugin / MCP semantic commands
    v
Railway FastAPI/MCP service
    |
    +-- deterministic Shinobi runtime
    |
    +-- persistent campaign checkout on a Railway volume
    |
    +-- private runtime durability data (WAL, locks, receipts)
    |
    v
GitHub private repository/branch
```

GitHub is the versioned source and replicated campaign-history remote. The live writable checkout and private transaction-recovery files live on the Railway volume. A database is not required for the current private, single-writer campaign.

## Authority boundary

Inside the campaign checkout:

- `runtime/` is engine source in Git; the deployed Python package is built from it.
- `game/` is static Shinobi rules/content/world definition.
- `state/` is mutable campaign truth.

Outside the checkout, `SHINOBI_RUNTIME_ROOT` stores transaction implementation data such as writer locks, WAL, recovery metadata, receipts, and temporary Git credential helpers. Those files are not RPG world state.

The public service exposes semantic gameplay operations only. It does not expose arbitrary file editing, shell, JSON patching, Git commands, or autonomous-mode impersonation.

## GitHub is not live filesystem synchronization

A GitHub push does not hot-edit the files inside an already-running Railway process. A GitHub-linked Railway service can use commits on its connected branch as deployment triggers. On startup, `shinobi_runtime.bootstrap` fetches the configured campaign branch and safely fast-forwards the persistent checkout when possible.

This repository intentionally separates two flows:

### Gameplay flow

```text
ChatGPT gameplay command
    -> Railway semantic command
    -> deterministic transaction
    -> volume-backed campaign state changes
    -> Git commit/push
```

The running process already owns the transaction it just committed. It does not need to pull that same commit back from GitHub.

### OOC development flow

```text
source/game edit
    -> GitHub commit
    -> Railway deployment trigger
    -> bootstrap fetch/fast-forward
    -> new runtime process
```

Do not use direct GitHub edits as the normal gameplay write path. Direct state edits bypass the runtime's validation, locking, WAL, idempotency, conservation rules, and semantic transaction boundary.

## Deployment watch paths

`railway.toml` watches:

```text
/runtime/**
/game/**
/pyproject.toml
/railway.toml
```

`state/**` is intentionally not a deployment watch path. Gameplay transactions push state commits to GitHub, but those state-only commits should not continuously redeploy the service. Runtime or static game-definition changes do require a new deployment.

If the deployment topology changes, keep this invariant: a gameplay state-only commit must not create a deployment loop.

## Recommended Railway volume layout

Attach one persistent Railway volume at `/data` and configure:

```text
/data/campaign    persistent Git checkout
/data/runtime     private WAL/locks/receipts/recovery data
```

Set:

```text
SHINOBI_CAMPAIGN_ROOT=/data/campaign
SHINOBI_RUNTIME_ROOT=/data/runtime
SHINOBI_GIT_URL=https://github.com/OWNER/REPOSITORY.git
SHINOBI_GIT_REMOTE=origin
SHINOBI_GIT_BRANCH=<production-campaign-branch>
SHINOBI_GIT_TOKEN=<private credential>
```

The runtime source does not hardcode these paths. Environment variables define them.

Use one writer service instance for this filesystem/Git-backed campaign. Do not horizontally scale the mutable campaign writer.

## Startup and persistent checkout

Railway starts:

```text
python -m shinobi_runtime.bootstrap
```

`pyproject.toml` maps the Python package from the repository `runtime/` source tree.

On first boot, bootstrap creates the configured campaign checkout on the mounted volume. On later boots it fetches the production branch, safely fast-forwards a clean checkout, preserves a recoverable local-ahead transaction commit, and refuses unsafe divergence instead of silently resetting state.

The checkout preparation belongs in the runtime start path because the campaign volume is live storage used by the service. WAL recovery runs before the application begins serving campaign operations.

Do not point multiple writers at the same production campaign branch.

## Graceful deployment teardown

`railway.toml` configures a bounded drain period so the old process receives time between termination and forced kill. The runtime must still rely on WAL recovery rather than assuming every transaction will finish before shutdown.

A volume-backed Railway service should be treated as a single active campaign writer. Do not attempt overlapping mounted writers.

## Git durability

Gameplay transactions mutate only their declared campaign owners, validate/read back the result, and commit the resulting campaign transaction when Git durability is configured. Push failure is handled by the transaction durability policy and must never be silently described as durable success.

For the private game, GitHub can contain source/game definitions and versioned campaign state on the same campaign branch. OOC DEV changes should be reviewed separately from gameplay transactions even when both ultimately live in the same repository.

## Why there is no database yet

The current model has one authoritative writer and already supplies:

- deterministic semantic reducers;
- writer locking;
- write-ahead recovery;
- idempotency receipts;
- Git transaction history;
- a persistent writable checkout;
- bounded typed reads.

Adding a database would create another persistence authority and synchronization problem without solving a current requirement. Reconsider a database if the architecture later needs concurrent writers, many campaign replicas, high-volume analytical queries, or transaction throughput that no longer fits the single-writer filesystem/Git model.

## Local service smoke test

Install service dependencies:

```sh
python3 -m pip install -e '.[service,service-test]'
```

Run the API against a local checkout:

```sh
export SHINOBI_API_TOKEN='<at-least-32-random-characters>'
export SHINOBI_CAMPAIGN_ROOT='/absolute/path/to/campaign-checkout'
export SHINOBI_RUNTIME_ROOT='/absolute/path/to/private-runtime-data'
uvicorn shinobi_runtime.api.entrypoint:app --host 127.0.0.1 --port 8000
```

Local diagnostics may omit remote Git configuration. Do not treat that mode as proof of remote durability.

## MCP and OAuth

The ChatGPT connection should use the public HTTPS MCP endpoint and authenticated access. Configure the runtime's supported OAuth/JWT environment variables for the chosen identity provider, including issuer/JWKS/audience and the exact allowed player subject.

The player-facing MCP/API accepts gameplay mode only. Faction/NPC autonomous actions are generated internally by the runtime and are never exposed as a client-selectable impersonation mode.

## ChatGPT Project instructions

The dedicated ChatGPT Project should distinguish three conversational modes:

- normal gameplay / `IC:`: consequential actions call the runtime and narrate only committed results;
- `OOC:`: read-only discussion/inspection, no world-time advance or mutation;
- `OOC DEV:`: source/game maintenance, testing, diagnosis, and deployment work, with no silent campaign-state edits.

A single message may contain OOC and IC blocks. Resolve blocks in order. Consequential mode ambiguity fails closed.

## OOC improvement workflow

A runtime improvement should follow:

```text
OOC DEV request
    -> inspect runtime/game code
    -> implement change
    -> run relevant verification
    -> deploy reviewed version
```

A confirmed bad campaign fact caused by a software bug should be repaired through an explicit migration or campaign-repair transaction with provenance. Do not disguise state repair as a source-code edit.

## Safe acceptance checklist

Before production use after a meaningful runtime/game change:

- startup succeeds from the persistent volume;
- transaction recovery has no unresolved WAL entry;
- public API rejects non-gameplay command modes;
- stale and duplicate request behavior remains correct;
- causal scheduler has no overdue material boundary;
- global person/faction scans remain zero during ordinary time advancement;
- schemas/templates/domain validators accept the current campaign;
- hidden information remains absent from player-safe packets;
- at least one real semantic command previews and commits successfully against a disposable campaign copy;
- remote Git durability is verified when remote durability is configured;
- a state-only gameplay commit does not trigger a Railway redeploy.

## Operations and recovery

Back up the Railway volume according to operational needs. GitHub is the replicated campaign-history remote, while the volume is the live writable checkout and recovery workspace. Both the campaign checkout and WAL/recovery data must survive service restarts.

If startup finds an interrupted transaction, let runtime recovery resolve it. Do not manually edit half-written campaign files and then narrate around the damage.
