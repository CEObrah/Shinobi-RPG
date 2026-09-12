"""Fail-closed compatibility handshake for the installed Shinobi GM Skill.

The token is not a credential. It is a release-coupling fingerprint derived
from the packaged Skill tree so a stale installed Skill cannot silently drive a
new runtime. ``tools/sync_gm_skill_contract.py`` owns token regeneration.
"""
from __future__ import annotations

import secrets
from typing import Any

GM_SKILL_CONTRACT_ID = "shinobi-gm-skill-v1"
GM_SKILL_CONTRACT_TOKEN = "b5575474802c7da3605e8d6ded3e9d5ec5284478dfe014e130fc97464c8b7b1f"


def verify_gm_skill_contract_token(value: object) -> bool:
    """Return whether ``value`` matches the packaged GM Skill fingerprint."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and secrets.compare_digest(value, GM_SKILL_CONTRACT_TOKEN)
    )


def verified_delivery_integrity() -> dict[str, Any]:
    """Return a non-secret player-safe proof for a successful handshake."""
    return {
        "gm_skill_contract_id": GM_SKILL_CONTRACT_ID,
        "gm_skill_contract_verified": True,
        "release_tier_rule": (
            "This proves only that the caller supplied the current packaged GM Skill "
            "compatibility token. Git, Railway deployment, campaign state, MCP schema "
            "publication, and Skill installation remain separate release tiers."
        ),
    }


__all__ = [
    "GM_SKILL_CONTRACT_ID",
    "GM_SKILL_CONTRACT_TOKEN",
    "verify_gm_skill_contract_token",
    "verified_delivery_integrity",
]
