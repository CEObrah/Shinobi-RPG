from shinobi_runtime.martial_world.health import lethal_state


def _state(wound):
    return lethal_state(wounds=[wound], blood_loss_fraction_milli=0, shock=0, consciousness=100)


def test_generic_neck_contact_is_not_automatically_exact_throat_destruction():
    assert _state({
        "zone": "neck", "severity": 40, "organ_trauma": 20,
        "structure_ref": None, "structure_damage": 0,
    }) == "alive"


def test_destroyed_major_neck_vessel_is_immediately_catastrophic():
    assert _state({
        "zone": "neck", "severity": 60, "organ_trauma": 50,
        "structure_ref": "carotid_artery", "structure_damage": 120,
    }) == "dead"
    assert _state({
        "zone": "neck", "severity": 60, "organ_trauma": 50,
        "structure_ref": "jugular_vein", "structure_damage": 120,
    }) == "dead"


def test_destroyed_unstabilized_trachea_is_dying_not_generic_neck_alive():
    assert _state({
        "zone": "neck", "severity": 55, "organ_trauma": 45,
        "structure_ref": "trachea", "structure_damage": 130,
        "stabilized": False,
    }) == "dying"

def test_destroyed_legacy_throat_target_is_an_airway_emergency_not_a_vessel_alias():
    assert _state({
        "zone": "neck", "severity": 70, "organ_trauma": 55,
        "structure_ref": "throat", "structure_damage": 130,
        "stabilized": False,
    }) == "dying"
    assert _state({
        "zone": "neck", "severity": 70, "organ_trauma": 55,
        "structure_ref": "throat", "structure_damage": 130,
        "stabilized": True,
    }) != "dead"

