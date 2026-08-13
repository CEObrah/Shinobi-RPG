# GitHub Connector Development Workflow

Use this reference during `OOC DEV:` work when GitHub is the available source/write surface.

## Read efficiently

1. Resolve the authoritative owner first from `references/repository-map.md` and `runtime/contracts/repository-map.json`.
2. Pin the working branch once by reading its current head commit and tree SHA before writes.
3. For a known path, use an exact file fetch. Do not list parent directories merely to rediscover a known file.
4. Use repository/code search only when the path or symbol is genuinely unknown.
5. When a response is large or truncated, use exact line ranges or targeted response search instead of repeating a broad fetch.
6. Do not repeatedly rediscover connector schemas after the needed GitHub actions are already loaded.
7. Never browse GitHub for current live campaign truth; use the Shinobi Runtime MCP surface for live state.

## Write coherently

For a one-file isolated edit, the contents API is acceptable after fetching the current blob SHA.

For a coherent multi-file change, prefer one atomic Git commit:

```text
fetch branch head/tree
-> create changed/new blobs
-> create tree against the pinned base tree
-> create one commit with the pinned head as parent
-> move the branch ref by fast-forward
-> fetch/compare the resulting commit
```

Do not make a long chain of one-file commits when the files jointly define one behavior. Never update/delete the same path concurrently.

If the branch head moved after it was pinned, stop the write, re-read the new head, and rebuild the tree rather than force-updating over unknown work.

## Verify through GitHub

When local execution is unavailable but the repository has CI:

1. add focused regressions for the changed behavior;
2. ensure the relevant workflow actually runs those tests;
3. open or update a PR when that is the repository's available way to trigger the audit workflow;
4. inspect workflow/check status and failed job logs when needed;
5. merge only after the required checks pass or after explicitly reporting a known verification limitation.

After a merge, fetch `main` and compare it to the development head before claiming the source is merged. Runtime deployment and ChatGPT Skill installation are separate delivery tiers and require separate verification.

## Keep connector work bounded

Prefer exact source reads, exact commit reads, compare calls, and targeted CI inspection. Directory listings, broad semantic scans, and repeated plugin discovery are fallback tools, not the default development loop.
