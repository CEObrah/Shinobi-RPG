# Documentation map

Current operational/design documentation is intentionally small:

- `RUNTIME_SERVICE_DEPLOYMENT.md` — current deployment and durability rules.
- `runtime-world-simulation-boundary.md` — current runtime/LLM authority boundary.
- `CAMPAIGN_REBASELINE_20260902.md` and `.json` — immutable lineage-origin record only, not current deployment instructions.
- `forensics/` — archived pre-rebaseline state used only for bounded failure analysis and fixture reconstruction.

One-off audit reports, playtest fix ledgers, release-status snapshots, and historical repair anchors are deliberately excluded from the live repository surface once their durable rules/tests have been absorbed into canonical source, Skill contracts, or immutable regression fixtures.
