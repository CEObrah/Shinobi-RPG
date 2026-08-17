from shinobi_runtime.commands.promotion_exam_attendance import _place_anchor


class Repo:
    def read_json(self, path):
        assert path == "state/world/routes-and-settlements.json"
        return {
            "payload": {
                "places": [
                    {"id": "place.konoha.nara_compound", "route_anchor_ref": "place.konoha"},
                    {"id": "place.konoha.academy.assignment.hall", "route_anchor_ref": "place.konoha"},
                    {"id": "place.konoha", "route_anchor_ref": "place.konoha"},
                ]
            }
        }


def test_exam_attendance_uses_saved_route_anchor_not_teleport_semantics():
    repo = Repo()
    assert _place_anchor(repo, "place.konoha.nara_compound") == "place.konoha"
    assert _place_anchor(repo, "place.konoha.academy.assignment.hall") == "place.konoha"
