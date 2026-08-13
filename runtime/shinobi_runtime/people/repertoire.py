"""Small helpers for exact-person technique repertoire state."""

from __future__ import annotations

from typing import Any, FrozenSet, Mapping


def field_usable_method_refs(record: Mapping[str, Any]) -> FrozenSet[str]:
    repertoire = record.get("repertoire")
    if not isinstance(repertoire, Mapping):
        raise ValueError("technique repertoire must be an object")
    mastery = repertoire.get("method_mastery")
    latent = repertoire.get("latent_or_locked_techniques")
    if not isinstance(mastery, Mapping) or not isinstance(latent, list):
        raise ValueError("technique repertoire state is invalid")
    if any(not isinstance(ref, str) or not ref for ref in mastery):
        raise ValueError("technique mastery refs must be non-empty strings")
    if any(not isinstance(ref, str) or not ref for ref in latent):
        raise ValueError("latent technique refs must be non-empty strings")
    latent_refs = set(latent)
    return frozenset(ref for ref in mastery if ref not in latent_refs)


def training_package_refs(record: Mapping[str, Any]) -> FrozenSet[str]:
    refs: set[str] = set()
    repertoire = record.get("repertoire")
    packages = repertoire.get("packages") if isinstance(repertoire, Mapping) else None
    if isinstance(packages, list):
        refs.update(ref for ref in packages if isinstance(ref, str) and ref)
    institutional = record.get("institutional_training_packages")
    if isinstance(institutional, list):
        refs.update(ref for ref in institutional if isinstance(ref, str) and ref)
    return frozenset(refs)


def technique_prerequisite_met(record: Mapping[str, Any], prerequisite: str) -> bool:
    if not isinstance(prerequisite, str) or not prerequisite:
        return False
    known = field_usable_method_refs(record)
    if prerequisite in known:
        return True

    if prerequisite.endswith("_nature"):
        nature = prerequisite[:-7]
        domains = record.get("domain_proficiencies")
        value = domains.get(nature) if isinstance(domains, Mapping) else None
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

    if prerequisite == "chakra_control":
        dimensions = record.get("chakra_dimensions")
        value = dimensions.get("control") if isinstance(dimensions, Mapping) else None
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

    packages = training_package_refs(record)
    if prerequisite == "matching_bloodline_or_clan_entitlement":
        repertoire = record.get("repertoire")
        bloodlines = repertoire.get("bloodlines") if isinstance(repertoire, Mapping) else None
        has_bloodline = isinstance(bloodlines, list) and any(isinstance(ref, str) and ref for ref in bloodlines)
        has_clan_package = any(ref.startswith("PKG_") and ref.endswith("_CLAN_CORE") for ref in packages)
        return has_bloodline or has_clan_package

    if prerequisite.endswith("_clan_entitlement"):
        prefix = prerequisite[: -len("_clan_entitlement")].upper()
        return f"PKG_{prefix}_CLAN_CORE" in packages

    if prerequisite == "hyuga_training":
        return "PKG_HYUGA_CLAN_CORE" in packages

    if prerequisite == "chakra_network_perception":
        return "byakugan_activation" in known or "byakugan" in known

    return False


def technique_prerequisites_met(record: Mapping[str, Any], technique: Mapping[str, Any]) -> bool:
    prerequisites = technique.get("prerequisites", [])
    if not isinstance(prerequisites, list):
        raise ValueError("technique prerequisites must be an array")
    return all(technique_prerequisite_met(record, prerequisite) for prerequisite in prerequisites)


__all__ = [
    "field_usable_method_refs",
    "training_package_refs",
    "technique_prerequisite_met",
    "technique_prerequisites_met",
]
