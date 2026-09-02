# Private Jianghu runtime deployment

## Topology

Run one mutable campaign writer:

```text
ChatGPT -> authenticated Jianghu MCP service -> Railway runtime
                                           -> persistent Git checkout on main
                                           -> private WAL/locks/receipts
                                           -> Git remote main
```

Inside the checkout, `runtime/`, `game/`, and `state/` are the mechanical authorities. The GM Skill lives under `plugins/shinobi-rpg/skill/shinobi-game-master/`. Keep WAL, receipts, locks, and temporary credentials outside the campaign checkout under `SHINOBI_RUNTIME_ROOT`.

## Railway volume and environment

Use one persistent campaign checkout plus a separate private runtime directory, for example:

```text
/data/campaign
/data/runtime
```

Configure:

```text
SHINOBI_CAMPAIGN_ROOT=/data/campaign
SHINOBI_RUNTIME_ROOT=/data/runtime
SHINOBI_GIT_URL=<repository URL>
SHINOBI_GIT_REMOTE=origin
SHINOBI_GIT_BRANCH=main
SHINOBI_GIT_TOKEN=<private credential>
```

Use a single writer service instance. Never commit secrets.

Railway starts:

```text
SHINOBI_GIT_BRANCH=main PYTHONPATH=/app/runtime python -m shinobi_runtime.bootstrap
```

## Single-main source and campaign durability

`main` is the only required Git branch. Source releases and runtime-generated campaign transactions share that branch. `railway.toml` watches non-state repository changes and excludes `state/**`, so state-only gameplay commits do not cause deployment loops.

Before every remote gameplay commit, transaction durability requires the local checkout and fetched remote `main` head to satisfy the exact synchronization invariants. If a source release races a gameplay write, the write fails closed rather than overwriting the source commit. The next runtime refresh/retry operates from the new `main` head.

On startup, `shinobi_runtime.bootstrap` clones or safely fast-forwards `main`, preserves only provable WAL-owned crash state, rejects dirty/unexplained divergence, and refuses a local-newer campaign checkout when the remote has been intentionally reset to an older revision.

## Revision-1 rebaseline deployment

The packaged revision-1 baseline must be committed to `main` **before** the new Railway volume is initialized. For this rebaseline, clear both the old persistent campaign checkout and the old private runtime WAL/receipt store. The fresh checkout then clones revision 1 directly from `main`; no rollback or branch migration occurs at runtime.

The pre-rebaseline state remains under `docs/forensics/`, so old campaign branches are not needed for forensic analysis and may be deleted.

## Deployment freshness verification

A Git commit, Railway deployment, MCP schema publication, and installed ChatGPT Skill are separate tiers. After deployment, use the bounded OOC audit and live `get_play_context` smoke path to confirm the connected runtime is on the intended source and campaign revision before consequential play.

## Transactions and recovery

Consequential gameplay writes use semantic commands, exact expected revision, deterministic reducers, staged validation, WAL/receipt idempotency, atomic persistence, Git commit/push, and read-back verification. Do not edit live `state/` through GitHub as a substitute for the runtime.

A crash is recoverable only through the exact private WAL/receipt evidence for the active campaign lineage. Never reuse the old revision-158 recovery directory with revision 1.

## MCP and authentication

Expose only authenticated semantic gameplay operations. Configure the supported JWT/OAuth issuer, JWKS, audience, scopes, and exact allowed player subject. Do not expose shell, arbitrary file patching, Git commands, or autonomous actor impersonation through the player-facing API.

If MCP tool names or schemas change, verify both the deployed server and the connected ChatGPT app/action snapshot. A Railway deployment alone does not refresh ChatGPT's cached tool contract.

## Skill installation

A repository Skill update does not install the ChatGPT Skill. Package/install the complete repository Skill directory and verify the installed Skill separately.
