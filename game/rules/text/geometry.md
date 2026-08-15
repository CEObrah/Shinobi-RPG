# Geometry, Timing, Reactions and Interruption

Numerical timing authority lives in `game/data/mechanics/timing.json`; body reach and mass authority lives in `game/data/mechanics/body.json`; technique geometry lives in the technique and effect registries.

Every consequential action resolves recognition, startup, seals/setup, required movement, release, travel, contact and recovery in that order. Nothing exists before release, no projectile contacts before travel completes, and a legal interruption changes or cancels later phases. Body Flicker and substitution are not teleportation unless an explicit registered spacetime effect says otherwise.
