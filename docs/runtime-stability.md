# Runtime stability gate

The persistent Shinobi campaign treats normal live play as a consumer of a pre-validated runtime, not as the primary integration-test environment.

## Release gate

Every ordinary repository audit runs the complete structural/runtime suite plus a deterministic 30-day replay from the shipped campaign snapshot. The replay uses the production campaign planner, transaction coordinator, schema/template validators, WAL/receipt machinery, and real Git commits in two disposable clones. Both simulations must finish with identical state digests and no unexpected transaction rejection.

For broad simulation changes, run the same replay with `STABILITY_HORIZON_DAYS=90` before landing. The August 2026 stabilization pass used a 90-day double replay to expose and repair cross-system failures involving lazy living-world owners, internal event context, compact-person health, institution authority, and semantic-history archival.

## Producer invariants

New terminal semantic/world events must carry causal context, material consequences, and affected authoritative owners. Internal autonomous event producers must not rely on validator cleanup after planning.

Exact-character health compatibility is representation-aware. Legacy healthy `shinobi_character` condition shapes and current readiness/injury shapes project to the same healthy training factors. Compact persistent `person` owners use their existing nested health resources and health status rather than acquiring a second injury/lifecycle authority.

## Transaction diagnostics

Production transaction failures expose only bounded failure classes such as Git stage/commit/readback/remote-durability failures. Exception text, repository paths, credentials, and Git output remain private.

## External maintenance and synchronization

Gameplay transactions require the persistent Railway checkout and remote branch to agree before mutation. A state-only maintenance repair intentionally does not trigger Railway deployment. If such a repair is committed externally rather than through the live transaction coordinator, perform a subsequent legitimate non-state deployment trigger so bootstrap can fast-forward the persistent checkout to the repaired remote HEAD before gameplay resumes.

Never bypass `head_mismatch` or force a gameplay write across divergent local/remote campaign authority.
