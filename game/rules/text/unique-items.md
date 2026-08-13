# Unique Items and Chakra Interfaces

Numerical authority for chakra interfaces, named-item base links, unique modules, and named-item mechanical profiles lives in `game/data/mechanics/unique-items.json`. Custody and condition live in `state/reg/named-items.json`. Ordinary physical weapon/armor mechanics continue to resolve from the global item registry.

A named item resolves as:

`ordinary base profile + registered unique module + current exact custody/condition`.

A unique module never substitutes for missing base weapon statistics. No named item may gain an unregistered module during an encounter. Module costs, capacities, forms, absorption, repair, extension, stored mass, and counters are exactly those in the structured registry.
