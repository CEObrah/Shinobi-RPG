from __future__ import annotations

import pytest

from shinobi_runtime.martial_world.frontier_support import route_lookup
from shinobi_runtime.martial_world.geography import load_static_geography
from shinobi_runtime.martial_world.travel import shortest_route


REPAIRED_CORRIDORS = (
    ("luoyang", "dengfeng", "route.luoyang.dengfeng", 72.8),
    ("zhengzhou", "xuchang", "route.zhengzhou.xuchang", 97.7),
    ("kaifeng", "hefei", "route.kaifeng.hefei", 522.4),
    ("jinan", "nanjing", "route.jinan.nanjing", 657.4),
    ("changan", "wudangshan", "route.changan.wudangshan", 394.7),
    ("changan", "chengdu", "route.changan.chengdu", 864.3),
    ("lanzhou", "chengdu", "route.lanzhou.chengdu", 851.6),
    ("hangzhou", "wuyi", "route.hangzhou.wuyi", 473.3),
    ("jingzhou", "hengyang", "route.jingzhou.hengyang", 483.1),
)


@pytest.mark.parametrize("start,end,edge_id,distance_km", REPAIRED_CORRIDORS)
@pytest.mark.parametrize("mode", ("foot", "convoy"))
def test_repaired_corridors_are_the_preferred_registered_route(start, end, edge_id, distance_km, mode):
    plan = shortest_route(start=start, end=end, mode=mode)
    assert plan["nodes"] == [start, end]
    assert plan["edges"] == [edge_id]
    assert plan["distance_km"] == distance_km


def test_frontier_route_index_and_planner_share_all_repaired_corridors():
    geography = load_static_geography()
    routes = route_lookup(geography)
    for start, end, edge_id, distance_km in REPAIRED_CORRIDORS:
        edge = routes[edge_id]
        assert {edge["from"], edge["to"]} == {start, end}
        assert edge["distance_km"] == distance_km
        assert "foot" in edge["allowed_modes"]
        assert "convoy" in edge["allowed_modes"]


def test_corridor_repair_keeps_registered_route_ids_unique():
    geography = load_static_geography()
    route_ids = [row["id"] for row in geography["routes"]]
    assert len(route_ids) == len(set(route_ids))


def test_existing_major_trunks_remain_preferred_after_network_expansion():
    expected = {
        ("luoyang", "changan"): "route.luoyang.changan",
        ("xiangyang", "wuhan"): "route.xiangyang.wuhan",
        ("chongqing", "chengdu"): "route.chongqing.chengdu",
        ("nanjing", "suzhou"): "route.nanjing.suzhou",
    }
    for (start, end), edge_id in expected.items():
        for mode in ("foot", "convoy"):
            plan = shortest_route(start=start, end=end, mode=mode)
            assert plan["nodes"] == [start, end]
            assert plan["edges"] == [edge_id]
