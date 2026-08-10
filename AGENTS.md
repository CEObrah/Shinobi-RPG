Before maintaining this repository, read `RUNTIME.md` and `REPOSITORY_MAP.md`. For narration behavior, read `VOICE.md`.

Do not treat migration history, tests, schemas, scripts, examples, indexes, neighboring owners, chat memory, or model inference as campaign truth or structural authority. Preserve current authority and never claim persistence succeeded unless the actual write succeeded.

For every mutable owner creation or structural change, follow `RUNTIME.md`: resolve the exact registered structural template through `runtime/contracts/template-index.json`, the fact-free blank skeleton through `runtime/contracts/blank-owner-index.json`, and the relevant update contract through `runtime/contracts/system-contract-index.json`. Missing or ambiguous structure stops persistence; never improvise fields. Structural additions or type/shape changes are maintenance and require the full validator stack before gameplay can use them.

Schema-compatible content growth inside an already registered open collection is not a structural revision: keep semantic IDs stable, do not create release-number schema/template/contract variants, and use normal CI. Change structural authorities only when shape, type, ownership semantics, or write authority actually changes.

OOC discussion and preview/design work are not campaign state. Preserve player intent boundaries.