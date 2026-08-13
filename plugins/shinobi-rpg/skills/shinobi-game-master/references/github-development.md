# GitHub OOC Development Discipline

Use this reference for repository-backed `OOC DEV:` work when the GitHub connector is available.

1. Resolve the repository and working ref once. Reuse the exact branch and expected parent SHA instead of rediscovering them repeatedly.
2. Route through `references/repository-map.md` and `runtime/contracts/repository-map.json` before broad search. Prefer exact `fetch_file` reads for known owners and symbol/file search for discovery.
3. Use `compare_commits` to understand branch scope. Do not repeatedly crawl large directory trees merely to gain confidence.
4. For one-file replacement, fetch the current blob SHA first. Never issue concurrent sequential writes to the same path.
5. For a coherent multi-file change, prefer one atomic Git transaction when supported: create blobs, create one tree over the known base tree, create one commit with the expected parent, then advance the branch ref.
6. Verify the resulting ref/commit and relevant CI/status before claiming the repository tier is complete.
7. Keep GitHub as source, provenance, recovery, and OOC-development surface. Never use repository browsing as the live player-state API.

If local execution cannot reach GitHub, keep authenticated source reads/writes on the connector and use available CI/status evidence for repository verification. Do not pretend a local checkout or local test ran when it did not.
