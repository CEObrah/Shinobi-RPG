# GitHub Connector Development Workflow

Use this reference only when GitHub is the available repository surface. For broad refactors, migrations, release cleanup, or cross-file work, prefer an uploaded/local worktree whenever one is available.

## Choose the right development surface

Use this order:

1. **Uploaded/local worktree:** primary surface for multi-file implementation, migrations, release cleanup, tests, packaging, and large state-safe transforms.
2. **GitHub connector:** remote source/history and bounded read/write surface when a local worktree is unavailable or when the user explicitly wants GitHub delivery.
3. **GitHub web browsing:** not a substitute for the connected GitHub repository tool when the repository is available through the connector.

Never browse GitHub for current live campaign truth. Live state comes from the Shinobi Runtime MCP service.

## Read efficiently

1. Resolve the authoritative owner from `references/repository-map.md` and `runtime/contracts/repository-map.json` before broad reading.
2. Pin the target branch/ref once before writes.
3. Fetch known files directly. Search only when the path or symbol is genuinely unknown.
4. Use targeted line ranges or response search for large files instead of repeatedly fetching whole trees.
5. Do not repeatedly rediscover connector schemas after the required GitHub actions are already loaded.
6. Stop when the reducer/owner/contract/test route is known.

## Write coherently

For one isolated text-file edit, the contents API is acceptable after fetching the current blob SHA.

For a coherent multi-file change, prefer one atomic commit built from a pinned base tree when the connector exposes that workflow. If the branch head moves after it was pinned, re-read and integrate deliberately; never force over unknown work.

Do not serialize a large mutable campaign owner by hand through chat/connector text. Structural state changes require a deterministic migration/repair program, before/after validation, conservation checks, and a semantic diff. A remote file API is not a safe substitute for running that migration locally.

Do not create probe files, dummy workflows, or throwaway commits merely to discover whether a connector or CI runner works.

## Classify failures correctly

Keep these distinct:

- connector policy/safety block before GitHub receives a write: **connector failure**, not repository evidence;
- authentication/permission/ref conflict: **repository-access failure**;
- GitHub Actions run with zero executed steps or no runner allocation: **CI infrastructure failure**, not a failed test;
- workflow step that starts and exits on an assertion/error: **actual source/test evidence**;
- local validator/test failure: **actual source/test evidence**.

Retry an unexpected connector call at most once when the exact operation is safe and idempotent. If it still fails, report the connector boundary rather than changing source semantics to appease the tool.

## Verify delivery tiers separately

Never collapse these into one claim:

`edited locally -> locally verified -> committed -> merged -> deployed -> MCP schema refreshed -> ChatGPT Skill installed`

A source merge does not deploy Railway. A Railway deploy does not refresh a connected-app action snapshot. A GitHub Skill commit does not install the ChatGPT Skill.
