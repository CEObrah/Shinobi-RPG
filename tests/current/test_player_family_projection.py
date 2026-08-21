from pathlib import Path

from shinobi_runtime.martial_world.live_state import player_view_from_person
from shinobi_runtime.people.repository import RepositoryPersonSheetResolver
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def test_player_household_kinship_is_exposed_without_hidden_family_reads():
    resolver = RepositoryPersonSheetResolver(RepositoryStore(ROOT))

    wei = resolver("pc_wei_tang")
    zhu = resolver("char.zhu")
    ling = resolver("char.ling")
    kai = resolver("char.kai")

    assert wei is not None
    assert zhu is not None
    assert ling is not None
    assert kai is not None

    assert wei["known_family_relations"] == {
        "char.zhu": "father",
        "char.ling": "mother",
        "char.kai": "younger_brother",
    }
    assert zhu["kinship_to_player"] == "father"
    assert ling["kinship_to_player"] == "mother"
    assert kai["kinship_to_player"] == "younger_brother"

    player_view = player_view_from_person(wei)
    assert player_view["known_family_relations"] == wei["known_family_relations"]
