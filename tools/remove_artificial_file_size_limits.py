#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def edit(rel: str, transform):
    path = ROOT / rel
    old = path.read_text(encoding="utf-8")
    new = transform(old)
    if new == old:
        raise SystemExit(f"No change made to {rel}")
    path.write_text(new, encoding="utf-8")


def remove_lines_containing(text: str, needles):
    lines = text.splitlines(True)
    kept = [line for line in lines if not any(needle in line for needle in needles)]
    return "".join(kept)


edit(
    "tools/audit.py",
    lambda text: remove_lines_containing(
        text,
        [
            "process_policy_registry_bloat",
            "startup_player_bloat",
            "startup_scene_bloat",
        ],
    ),
)

edit(
    "tools/test_runtime.py",
    lambda text: remove_lines_containing(
        text,
        ["readme_too_large", "agents_too_large"],
    ),
)


def semantics(text: str):
    text = text.replace(
        "# Router/map must expose the current architecture and stay compact enough to be useful.\n",
        "# Router/map must expose the current architecture and route authority correctly.\n",
    )
    text = remove_lines_containing(text, ["CONTEXT ADVISORY: repository-map.json", "CONTEXT ADVISORY: RUNTIME.md"])
    return text


edit("tools/test_semantics.py", semantics)


def unit_model(text: str):
    old = """# Context-size advisory only. Correctness and sufficient instructions take priority over a fixed byte ceiling.\nfor rel,advisory in [('RUNTIME.md',8000),('data/runtime/repository-map.json',12000),('VOICE.md',8000)]:\n    size=(R/rel).stat().st_size\n    if size>advisory:print(f'CONTEXT ADVISORY: {rel} is {size} bytes (soft target {advisory}); review only if duplication can be removed safely')\n"""
    if old not in text:
        raise SystemExit("unit-model context advisory block not found")
    return text.replace(old, "")


edit("tools/test_unit_model.py", unit_model)


def runtime(text: str):
    anchor = "Startup loads only `RUNTIME.md`, `VOICE.md`, `data/runtime/repository-map.json`, `state/meta.json`, `state/player.json`, and `state/scene.json`; then load the smallest causal owner/shard. Known IDs use direct refs; indexes are discovery only. Do not preload catalogs, rosters, units, techniques, social graphs, or establishments. `REPOSITORY_MAP.md` is the read/write cookbook; `PLAYER_INTERFACE.md` is load-on-demand. Stop when enough authority is loaded. Structural writes use one exact cold file template, its registered blank owner skeleton, and the relevant system update contract. Existing owners and examples never define structure.\n"
    addition = "\nControl, routing, narration, state, and mechanic files have no arbitrary content-byte ceilings. They are judged by semantic correctness, completeness, ownership, retrieval cost, and absence of duplicated authority. Split or route content only when doing so improves authority boundaries or causal loading without losing necessary instruction; never delete or weaken useful rules merely to satisfy a numeric file-size target.\n"
    if addition.strip() in text:
        raise SystemExit("runtime size-limit law already present")
    if anchor not in text:
        raise SystemExit("runtime startup anchor not found")
    return text.replace(anchor, anchor + addition, 1)


edit("RUNTIME.md", runtime)

# Guard against reintroducing the exact artificial content-size checks removed here.
for rel in ("tools/audit.py", "tools/test_runtime.py", "tools/test_semantics.py", "tools/test_unit_model.py"):
    text = (ROOT / rel).read_text(encoding="utf-8")
    forbidden = [
        "process_policy_registry_bloat",
        "startup_player_bloat",
        "startup_scene_bloat",
        "readme_too_large",
        "agents_too_large",
        "CONTEXT ADVISORY: repository-map.json",
        "CONTEXT ADVISORY: RUNTIME.md",
        "soft target {advisory}",
    ]
    hit = [x for x in forbidden if x in text]
    if hit:
        raise SystemExit(f"Artificial size guard survived in {rel}: {hit}")

print("ARTIFICIAL FILE SIZE LIMITS REMOVED")
