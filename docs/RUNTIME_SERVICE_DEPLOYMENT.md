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

On startup, `shinobi_runtime.bootstrap` clones or safely reconciles `main`, preserves only provable WAL-owned crash state, and rejects dirty or unexplained divergence. A lower remote revision is still rejected by default, but any changed source-owned `release_baseline_id` is handled **before normal ancestry/fast-forward rules** as an explicit reset boundary. Bootstrap may adopt that replacement lineage only when campaign identity is unchanged, no recoverable WAL exists, and the replacement checkout exactly matches its packaged campaign/player/revision/time and complete state-tree digest. This applies to both unrelated-history replacements and ordinary fast-forward commits carrying a new certified baseline. A bad or incomplete reset rolls back to the previous local head and fails closed.

Production startup then fails closed unless the immutable Railway build commit is an ancestor of the live checkout and every later commit is state-only. A stale source image must not expose even a read-only playable surface; deploy the new image before reconnecting ChatGPT.

## Campaign lineage and resets

Normal source deployments preserve the current campaign checkout and its matching private WAL/receipt/recovery store. Never clear persistent volumes merely because source code changed or because the campaign revision advanced. The active revision is read from `state/meta.json`; documentation must not hard-code it.

A full campaign rebaseline is an exceptional, explicit maintenance operation. A replacement release must use a new `release_baseline_id` and an exact release-state contract. The bootstrap can then replace an older persistent checkout automatically only after the new baseline verifies byte-for-byte through its state-tree digest and campaign metadata; pending WAL evidence blocks automatic adoption. After verification, bootstrap moves completed WAL and idempotency receipts from the retired lineage under `retired-release-baselines/` before the new baseline can serve writes. If that private-store retirement fails, the previous Git head is restored and startup fails closed. Preserve any required forensic snapshot outside live authority. Historical details of the 2026-09-02 lineage origin remain in `docs/CAMPAIGN_REBASELINE_20260902.*` and `docs/forensics/`; they are not live deployment instructions.

## Deployment freshness verification

A Git commit, Railway deployment, MCP schema publication, and installed ChatGPT Skill are separate tiers. The packaged GM Skill carries a non-secret compatibility fingerprint that must be supplied to `get_play_context`; a stale installed Skill or cached MCP schema therefore fails closed instead of silently driving a newer runtime. Every authoritative `get_play_context` also re-runs the local deployment freshness guard, so a process that became stale after startup cannot emit a normal playable context with a green delivery proof. This proves the running process against its local checkout/build identity, not that an unseen external Git change or Railway routing/domain change does not exist. After deployment, refresh the MCP schema and installed Skill together, then use the bounded OOC audit and live `get_play_context` smoke path to confirm the connected runtime is on the intended source and campaign revision before consequential play.

For an uploaded/local release handoff, run the maintained release gates first and then build the final artifact with `python tools/package_release.py <output.zip>`. The packager excludes transient caches/checkpoints, requires runtime/game/state/Skill/deployment surfaces, reopens and byte-compares the ZIP, and runs fast release-state/Skill checks against the extracted artifact itself.

## Transactions and recovery

Consequential gameplay writes use semantic commands, exact expected revision, deterministic reducers, staged validation, WAL/receipt idempotency, atomic persistence, Git commit/push, and read-back verification. Do not edit live `state/` through GitHub as a substitute for the runtime.

A crash is recoverable only through the exact private WAL/receipt evidence for the active campaign lineage. Never attach a recovery store from a different lineage or campaign revision history.

## MCP and authentication

Expose only authenticated semantic gameplay operations. Configure the supported JWT/OAuth issuer, JWKS, audience, scopes, and exact allowed player subject. Do not expose shell, arbitrary file patching, Git commands, or autonomous actor impersonation through the player-facing API.

If MCP tool names or schemas change, verify both the deployed server and the connected ChatGPT app/action snapshot. A Railway deployment alone does not refresh ChatGPT's cached tool contract.

## Skill installation

A repository Skill update does not install the ChatGPT Skill. Package/install the complete repository Skill directory and verify the installed Skill separately.
