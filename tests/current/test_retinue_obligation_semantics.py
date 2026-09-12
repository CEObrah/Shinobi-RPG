import copy

from shinobi_runtime.martial_world.institutional_obligations import member_transition_bound_person_refs


def _reader(deployments):
    docs = {
        'state/martial-world/projects.json': {'projects': {}},
        'state/martial-world/deployments.json': {'deployments': deployments},
        'state/martial-world/route-operations.json': {'movements': {}},
        'state/martial-world/custody.json': {'records': []},
        'state/martial-world/contracts/index.json': {'active': {}},
        'state/martial-world/tournaments.json': {'tournaments': {}},
    }
    def read(path):
        if path not in docs:
            raise FileNotFoundError(path)
        return copy.deepcopy(docs[path])
    return read


def test_active_retinue_chooser_is_provenance_not_a_deployed_member_binding():
    read = _reader({
        'retinue.wei': {
            'operation_kind': 'standing_retinue', 'status': 'active',
            'leader_ref': 'wei', 'member_refs': ['han'], 'chooser_refs': ['zhu', 'ling'],
        }
    })
    bound = member_transition_bound_person_refs(read)
    assert {'wei', 'han'} <= bound
    assert 'zhu' not in bound
    assert 'ling' not in bound


def test_pending_retinue_still_binds_assignment_chooser_until_review_settles():
    read = _reader({
        'retinue.wei': {
            'operation_kind': 'standing_retinue', 'status': 'assignment_pending',
            'leader_ref': 'wei', 'member_refs': [], 'chooser_refs': ['zhu', 'ling'],
        }
    })
    bound = member_transition_bound_person_refs(read)
    assert {'wei', 'zhu', 'ling'} <= bound
