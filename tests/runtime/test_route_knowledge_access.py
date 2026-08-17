from __future__ import annotations

import json

from shinobi_runtime.information import InformationStore
from shinobi_runtime.security.route_access import actor_knows_route


class _Repository:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def read_optional_bytes(self, path: str):
        return self.files.get(path)


def test_public_route_requires_no_information_claim() -> None:
    repo = _Repository()
    route = {"id": "route_public", "knowledge_classification": "public"}
    assert actor_knows_route(repo, "actor.a", route) is True


def test_restricted_route_requires_subject_indexed_holder_knowledge() -> None:
    repo = _Repository()
    route_ref = "route_secret"
    actor_ref = "actor.a"
    route = {"id": route_ref, "knowledge_classification": "restricted"}
    assert actor_knows_route(repo, actor_ref, route) is False

    path = InformationStore.subject_shard_path(actor_ref, route_ref)
    holder_hash = InformationStore.holder_hash(actor_ref)
    bucket = path.rsplit("subject-", 1)[1].split(".json", 1)[0]
    repo.files[path] = json.dumps(
        {
            "schema": "information-knowledge-subject-shard",
            "holder_ref": actor_ref,
            "holder_hash": holder_hash,
            "bucket": bucket,
            "subjects": {route_ref: ["claim.route.secret"]},
        }
    ).encode("utf-8")

    assert actor_knows_route(repo, actor_ref, route) is True
