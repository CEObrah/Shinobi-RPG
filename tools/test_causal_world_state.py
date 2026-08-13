#!/usr/bin/env python3
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

def load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))

def fail(msg):
    print("CAUSAL WORLD STATE TEST FAILED")
    print("-", msg)
    sys.exit(1)

# Startup-visible invariant.
repo_map = load("runtime/contracts/repository-map.json")
joined = " ".join(repo_map.get("retrieval_invariants", []))
if "sufficient authoritative state" not in joined or "missing detail" not in joined:
    fail("startup causal-sufficiency invariant missing")

# Role-slot structure must be registered and must not become a hidden character sheet.
r_index = load("runtime/contracts/template-index-shards/r.json")
role_ent = r_index.get("templates", {}).get("role-slot-registry")
if not role_ent:
    fail("role-slot-registry template not registered")
if role_ent.get("source_schema") != "game/schemas/role-slot-registry.schema.json":
    fail("role-slot-registry source schema mismatch")
blank_index = load("runtime/contracts/blank-owner-index.json")
if blank_index.get("owners", {}).get("role-slot-registry") != "runtime/contracts/blank-owners/role-slot-registry.blank.json":
    fail("role-slot-registry blank owner not registered")
role_template = load("runtime/contracts/templates/role-slot-registry.template.json")
slot_keys = set(role_template["object_contracts"]["/slots/*"]["allowed_keys"])
for forbidden in {"name", "personality", "biography", "relationships", "inventory", "equipment", "chakra", "techniques", "combat_stats", "private_goal"}:
    if forbidden in slot_keys:
        fail("hidden-character field allowed in role slot: " + forbidden)
for required in {"incumbency_id", "functional_capability_band", "health_availability", "service_development_credit", "retirement_status", "succession_status", "last_settled_at"}:
    if required not in slot_keys:
        fail("role continuity field missing: " + required)

# Mutable place owners remain formally schematized, while compact strategic-site
# capability lives in the authoritative world registry.  Static topology and
# deeper personnel/inventory authorities must not be duplicated into either.
p_index = load("runtime/contracts/template-index-shards/p.json")
place_ent = p_index.get("templates", {}).get("place")
if not place_ent or place_ent.get("source_schema") != "game/schemas/place.schema.json":
    fail("place template is not bound to formal schema")
s_index = load("runtime/contracts/template-index-shards/s.json")
world_ent = s_index.get("templates", {}).get("shinobi-world-registry")
if not world_ent or world_ent.get("path") != "runtime/contracts/templates/shinobi-world-registry.template.json":
    fail("shinobi-world-registry template is not registered")
world_template = load("runtime/contracts/templates/shinobi-world-registry.template.json")
if world_template.get("target_schema") != "shinobi-world-registry":
    fail("shinobi-world-registry target schema drift")
place_keys = set(world_template["object_contracts"]["/payload/places/*"]["allowed_keys"])
if "mechanical_modules" not in place_keys:
    fail("strategic place mechanical_modules missing")
module_keys = set(world_template["object_contracts"]["/payload/places/*/mechanical_modules"]["allowed_keys"])
if module_keys != {"training", "medical", "custody"}:
    fail("strategic-site mechanical module set drift: " + str(sorted(module_keys)))
expected_fields = {
    "training": {"capacity_slots", "quality_milli", "supported_categories"},
    "medical": {"quality_milli", "specialties"},
    "custody": {"capacity_slots", "security_milli"},
}
for module, expected in expected_fields.items():
    actual = set(world_template["object_contracts"][f"/payload/places/*/mechanical_modules/{module}"]["allowed_keys"])
    if actual != expected:
        fail("strategic-site module field drift: " + module + ":" + str(sorted(actual)))
for forbidden in {"personnel", "inventory", "techniques", "relationships"}:
    if forbidden in module_keys or forbidden in place_keys:
        fail("strategic-site registry duplicates deeper authority: " + forbidden)

# Routing and contracts must point at the cold rule.
router = load("runtime/contracts/rule-router.json").get("domains", {})
for domain in ("institutional_role_continuity", "strategic_site_resolution"):
    if "game/rules/text/causal-world-state.md" not in router.get(domain, []):
        fail("causal rule not routed for " + domain)
if repo_map.get("route_index", {}).get("institutional_role_slot") != "military":
    fail("institutional role route missing")
if repo_map.get("route_index", {}).get("strategic_place_known_id") != "world":
    fail("strategic place route missing")
forces = load("runtime/contracts/system-contracts/forces_institutions.json")
if "role-slot-registry" not in forces.get("owner_templates", []):
    fail("forces/institutions contract lacks role-slot owner")
world = load("runtime/contracts/system-contracts/world_state.json")
if not any("sufficient authoritative state" in x for x in world.get("invariants", [])):
    fail("world-state contract lacks causal sufficiency invariant")

rule = (ROOT / "game/rules/text/causal-world-state.md").read_text(encoding="utf-8")
for phrase in ("Minimum sufficient authority", "not a hidden character", "retroactively optimize", "Static ambient locations own ontology"):
    if phrase not in rule:
        fail("rule text missing: " + phrase)

print("CAUSAL WORLD STATE TESTS OK")
