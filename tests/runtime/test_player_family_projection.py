from __future__ import annotations

from shinobi_runtime.api.player_family_projection import _player_family_context


class _Repo:
    def read_json(self, path: str):
        assert path == "state/family/kinship-index.json"
        return {
            "person_links": {
                "pc_wei_tang": {
                    "parents": ["char.father", "char.mother"],
                    "children": [],
                    "spouses": [],
                    "former_spouses": [],
                    "guardians": [],
                    "wards": [],
                    "households": ["family.household.tang"],
                },
                "char.father": {
                    "children": ["pc_wei_tang", "char.brother", "secret.outsider"],
                },
                "char.mother": {
                    "children": ["pc_wei_tang", "char.brother"],
                },
            }
        }


class _Ops:
    repository = _Repo()

    def _permitted_person_lookup_ids(self, *, player_id: str):
        assert player_id == "pc_wei_tang"
        return ("pc_wei_tang", "char.father", "char.mother", "char.brother")


def test_family_context_exposes_only_currently_permitted_kin() -> None:
    result = _player_family_context(_Ops(), player_id="pc_wei_tang")
    assert result["parent_refs"] == ["char.father", "char.mother"]
    assert result["sibling_refs"] == ["char.brother"]
    assert "secret.outsider" not in result["sibling_refs"]
    assert result["household_refs"] == ["family.household.tang"]
