# Private Jianghu runtime deployment

## Topology

Run one mutable campaign writer:

```text
ChatGPT -> authenticated Jianghu MCP service -> Railway runtime
                                           -> persistent Git checkout
                                           -> private WAL/locks/receipts
                                           -> Git remote durability
```

Inside the checkout, `runtime/`, `game/`, and `state/` are the mechanical authorities. The GM Skill lives under `plugins/shinobi-rpg/skill/shinobi-game-master/`. Keep WAL, receipts, locks, and temporary credentials outside the campaign checkout under `SHINOBI_RUNTIME_ROOT`.

## Railway volume and environment

Use one persistent volume, for example:

```text
/data/campaign
/data/runtime
```

Configure:

```text
SHINOBI_CAMPAIGN_ROOT=/data/campaign
SHINOBI_RUNTIME_ROOT=/data/runtime
SHINOBI_GIT_URL=<private repository URL>
SHINOBI_GIT_REMOTE=origin
SHINOBI_GIT_BRANCH=<production branch>
SHINOBI_GIT_TOKEN=<private credential>
```

Use a single writer service instance. Never commit secrets.

Railway starts:

```text
PYTHONPATH=/app/runtime python -m shinobi_runtime.bootstrap
```

## Source and state synchronization

The shipped `railway.toml` watches every non-state repository path and excludes `state/**`.

- runtime-generated state-only gameplay commits do not trigger a deployment loop;
- any non-state source, Skill, docs, tests, tools, workflow, dependency, or configuration commit triggers Railway deployment;
- bootstrap fetches the production branch and safely fast-forwards a clean checkout when the remote is a strict descendant;
- a verified local-ahead gameplay transaction is preserved for transaction recovery;
- dirty or causally different divergence fails closed.

Before a new remotely durable gameplay transaction, Git preflight requires the local checked-out branch head to equal the fetched remote branch head exactly.

### Deployment freshness verification

Treat a merged source commit and the running Railway process as separate release tiers. Railway exposes the immutable Git commit used for a GitHub-triggered build through `RAILWAY_GIT_COMMIT_SHA`. The runtime compares that build revision with the persistent campaign checkout on every health request and in the bounded OOC audit.

State-only descendants of the build revision are expected because gameplay commits advance `state/` without rebuilding the service. They remain healthy. If any non-state path changed after the build revision, if the build revision is not an ancestor of the checkout, or if a Railway process lacks valid build identity, `/health` returns HTTP 503 rather than claiming the stale image is healthy. The OOC audit exposes only bounded source/checkout revision identity and freshness status; it never exposes credentials or arbitrary repository paths.

After a runtime fix is merged, verify the connected MCP itself with OOC audit before resuming consequential play. A healthy acceptance result includes `deployment_source:summary status=fresh`. If GitHub reports a successful Railway deployment but the connected MCP still reports stale source identity, treat service/connector routing as unresolved rather than patching campaign state or weakening validators.

Railway watch paths are gitignore-style patterns. The repository uses a broad include before the `state/**` exclusion so non-state changes remain deployment-triggering while gameplay-only state commits do not cause a deployment loop. If a non-state merge does not create a new deployment, inspect the Railway service's connected source branch and deployment status rather than weakening campaign or validator invariants.

## Checkout replacement safety

A clean Railway checkout may adopt an intentionally replaced remote repository only when committed campaign-authority paths are byte-identical between the local and remote heads. Source lineage may change; campaign truth may not. If authority bytes differ, bootstrap fails closed.

This permits a deliberate repository restart when the release tree carries the same authoritative `state/` snapshot. Any intentional campaign-truth change must use an explicit validated state reset before deployment.

## Transactions and recovery

Consequential gameplay writes use semantic commands, exact expected revision, deterministic reducers, staged validation, WAL/receipt idempotency, atomic persistence, Git commit/push, and read-back verification. Do not edit live `state/` through GitHub as a substitute for the runtime.


## MCP and authentication

Expose only authenticated semantic gameplay operations. Configure the supported JWT/OAuth issuer, JWKS, audience, scopes, and exact allowed player subject. Do not expose shell, arbitrary file patching, Git commands, or autonomous actor impersonation through the player-facing API.

If MCP tool names or schemas change, verify both the deployed server and the connected ChatGPT app/action snapshot. A Railway deployment alone does not refresh ChatGPT's cached tool contract.

## Verification

Before push after a meaningful change, run the maintained local gates:

```text
python tools/quick_check.py
python tools/verify_jianghu_semantics.py
python tools/audit_state_bloat.py
python tools/verify_noop_roundtrip.py
python tools/test_changed.py <changed paths>
```

`test_changed.py` owns focused changed-owner regressions plus the core invariant slice, so do not add duplicate pytest invocations as decorative extras. The full `tests/current` suite and long-horizon work remain deliberate release verification. After push/PR, required GitHub Actions rerun these checks from a clean checkout; red blocks merge until the correct implementation/test/fixture/environment/workflow owner is repaired. Green permits merge but does not prove deployment. For time/autonomy changes, the current suite includes the monthly-frontier regression. After merge, verify Railway is on the intended branch head, OOC audit reports fresh deployed source, recovery has no unresolved WAL entry, remote Git durability is healthy, and the ChatGPT Skill/app surfaces are synchronized when they changed.

## Skill installation

A repository Skill update does not install the ChatGPT Skill. Package the complete directory at `plugins/shinobi-rpg/skill/shinobi-game-master/`, validate it, install/upload that package in ChatGPT, and verify the installed Skill separately.
