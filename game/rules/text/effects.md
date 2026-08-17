# Technique Effects

Each technique has exactly one deterministic effect profile at the `effect_profile_path` stored in its technique record. Load that exact profile only when resolving that technique. Shared formulas live in `game/data/mechanics/effect-resolvers.json`; the exact primitive lives at `mechanical_base_path`.

Effect indexes/manifests are routing/maintenance data and are not required when the exact path is already known. Narration cannot add mechanics absent from the loaded deterministic effect, primitive or explicit special-system reference.
