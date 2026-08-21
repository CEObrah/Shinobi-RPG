# Repository Map

Current authorities:

```text
runtime/shinobi_runtime/commands/       closed Jianghu semantic commands
runtime/shinobi_runtime/martial_world/  deterministic domain mechanics
runtime/shinobi_runtime/combat/         exact local geometry/defense/team tactics
runtime/shinobi_runtime/people/         direct persistent person reads
runtime/shinobi_runtime/tx/             transaction/WAL/receipt durability
runtime/contracts/                      current closed structural templates

game/data/martial-world/                static Jianghu rules/world data
game/schemas/                           current mutable-owner JSON schemas

state/meta.json                          campaign time/revision/mode
meta.player_id -> person route          authoritative player person; player view is derived
state/scene.json                         current scene projection
state/martial-world/                     all other mutable Jianghu owners
```

For source work, find the smallest owner. For live gameplay, use Runtime reads rather than repository browsing.
