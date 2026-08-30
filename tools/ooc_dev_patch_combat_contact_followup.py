from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tests/current/test_combat_geometry.py"
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one combat-geometry match, found {count}: {old[:120]!r}")
    text = text.replace(old, new, 1)


replace_once(
    '''    assert trace['moved'] is False\n    assert trace['reason']=='target_moved_beyond_committed_approach'\n    assert moved.x_mm==combat['positions'][attacker['person_id']]['x_mm']\n''',
    '''    assert trace['moved'] is True\n    assert trace['reason']=='partial_committed_approach'\n    assert int(trace['distance_mm'])==int(params['approach_distance_mm'])\n    assert int(trace['remaining_mm'])>0\n    assert moved.x_mm>combat['positions'][attacker['person_id']]['x_mm']\n''',
)

replace_once(
    '''    assert c.offense==0 and c.control==0 and c.defense==0 and c.mobility==0 and c.reaction==0\n''',
    '''    assert 0<c.offense<b.offense and 0<c.control<b.control and 0<c.defense<b.defense\n    assert 0<c.mobility<b.mobility and 0<c.reaction<b.reaction\n    assert 0<c.capture<b.capture and 0<c.escape<b.escape\n''',
)

replace_once(
    '''    assert sum(c.values()) == 0\n''',
    '''    assert 0 < sum(c.values()) < sum(b.values())\n''',
)

PATH.write_text(text, encoding="utf-8")
print("stale combat regressions corrected")
