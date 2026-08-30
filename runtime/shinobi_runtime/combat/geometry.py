"""Deterministic bounded local-space geometry for exact combat.

Local exact combat owns integer millimetre coordinates.  Side membership and
intended target identity are legality/intent inputs only; physical contact is
determined by spatial intersection with participant footprints and obstacles.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

RANGE_BAND_CENTERS_MM = {0: 1_500, 1: 8_000, 2: 25_000, 3: 60_000}
RANGE_BAND_THRESHOLDS_MM = (3_000, 15_000, 40_000)
DEFAULT_BODY_RADIUS_MM = 300
DEFAULT_LATERAL_SPACING_MM = 1_800
DEFAULT_ATTACK_WIDTH_MM = 600
DEFAULT_RETREAT_DISTANCE_MM = 5_000


def _z_mm(row: Mapping[str, Any]) -> int:
    raw = row.get("elevation_mm", 0)
    return int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else 0


def range_band_from_distance_mm(distance_mm: int) -> int:
    distance = max(0, int(distance_mm))
    if distance <= RANGE_BAND_THRESHOLDS_MM[0]:
        return 0
    if distance <= RANGE_BAND_THRESHOLDS_MM[1]:
        return 1
    if distance <= RANGE_BAND_THRESHOLDS_MM[2]:
        return 2
    return 3


def distance_mm(a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
    dx = int(b.get("x_mm", 0)) - int(a.get("x_mm", 0))
    dy = int(b.get("y_mm", 0)) - int(a.get("y_mm", 0))
    dz = _z_mm(b) - _z_mm(a)
    return math.isqrt(dx * dx + dy * dy + dz * dz)


def planar_distance_mm(a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
    dx = int(b.get("x_mm", 0)) - int(a.get("x_mm", 0))
    dy = int(b.get("y_mm", 0)) - int(a.get("y_mm", 0))
    return math.isqrt(dx * dx + dy * dy)


def facing_to_target_mdeg(a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
    dx = int(b.get("x_mm", 0)) - int(a.get("x_mm", 0))
    dy = int(b.get("y_mm", 0)) - int(a.get("y_mm", 0))
    if dx == 0 and dy == 0:
        return int(a.get("facing_mdeg", 0)) % 360_000
    return int(round(math.degrees(math.atan2(dy, dx)) * 1000)) % 360_000


def angular_difference_mdeg(a_mdeg: int, b_mdeg: int) -> int:
    raw = abs((int(a_mdeg) - int(b_mdeg)) % 360_000)
    return min(raw, 360_000 - raw)


def initial_positions(
    *,
    side_by_participant: Mapping[str, str],
    zone_ref: str,
    initial_range_band: int = 1,
) -> dict[str, dict[str, int | str]]:
    """Create deterministic opposed ranks with real lateral spacing."""
    band = max(0, min(3, int(initial_range_band)))
    separation = RANGE_BAND_CENTERS_MM[band]
    sides = sorted(set(side_by_participant.values()))
    groups = {side: sorted(ref for ref, value in side_by_participant.items() if value == side) for side in sides}
    result: dict[str, dict[str, int | str]] = {}

    if len(sides) <= 2:
        anchors = [(-separation // 2, 0, 0), (separation // 2, 0, 180_000)]
    else:
        directions = [
            (-1000, 0, 0), (1000, 0, 180_000), (0, -1000, 90_000), (0, 1000, 270_000),
            (-707, -707, 45_000), (707, 707, 225_000), (-707, 707, 315_000), (707, -707, 135_000),
        ]
        if len(sides) > len(directions):
            raise ValueError("exact combat supports at most eight independent sides")
        anchors = [
            (dx * separation // 1000, dy * separation // 1000, facing)
            for dx, dy, facing in directions[: len(sides)]
        ]

    for side_index, side in enumerate(sides):
        members = groups[side]
        ax, ay, facing = anchors[side_index]
        vertical_rank = abs(ax) >= abs(ay)
        for index, ref in enumerate(members):
            lateral2 = (2 * index - (len(members) - 1)) * DEFAULT_LATERAL_SPACING_MM
            lateral = lateral2 // 2
            x = ax
            y = ay
            if vertical_rank:
                y += lateral
            else:
                x += lateral
            result[ref] = {
                "zone_ref": zone_ref,
                "elevation_mm": 0,
                "cover_milli": 0,
                "x_mm": int(x),
                "y_mm": int(y),
                "facing_mdeg": int(facing),
                "body_radius_mm": DEFAULT_BODY_RADIUS_MM,
                "vx_mmps": 0,
                "vy_mmps": 0,
                "stance": "ready",
            }
    return result


def nearest_target(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    actor_ref: str,
    candidate_refs: Sequence[str],
) -> str | None:
    actor = positions.get(actor_ref)
    if not isinstance(actor, Mapping):
        return min((ref for ref in candidate_refs if isinstance(ref, str) and ref != actor_ref), default=None)
    ranked: list[tuple[int, str]] = []
    for ref in candidate_refs:
        target = positions.get(ref)
        if not isinstance(ref, str) or ref == actor_ref or not isinstance(target, Mapping):
            continue
        if target.get("zone_ref") != actor.get("zone_ref"):
            continue
        ranked.append((distance_mm(actor, target), ref))
    return min(ranked)[1] if ranked else None


def _aim_vector(actor: Mapping[str, Any], aim: Mapping[str, Any] | None) -> tuple[int, int]:
    ax, ay = int(actor.get("x_mm", 0)), int(actor.get("y_mm", 0))
    if isinstance(aim, Mapping):
        dx = int(aim.get("x_mm", 0)) - ax
        dy = int(aim.get("y_mm", 0)) - ay
        if dx != 0 or dy != 0:
            return dx, dy
    facing = int(actor.get("facing_mdeg", 0)) / 1000.0
    return int(round(math.cos(math.radians(facing)) * 1000)), int(round(math.sin(math.radians(facing)) * 1000))


def _aim_vector_3d(actor: Mapping[str, Any], aim: Mapping[str, Any] | None) -> tuple[int, int, int]:
    ax, ay, az = int(actor.get("x_mm", 0)), int(actor.get("y_mm", 0)), _z_mm(actor)
    if isinstance(aim, Mapping):
        dx = int(aim.get("x_mm", 0)) - ax
        dy = int(aim.get("y_mm", 0)) - ay
        dz = _z_mm(aim) - az
        if dx != 0 or dy != 0 or dz != 0:
            return dx, dy, dz
    dx, dy = _aim_vector(actor, aim)
    return dx, dy, 0


def _lane_contact_metrics(
    *, actor: Mapping[str, Any], aim: Mapping[str, Any] | None, target: Mapping[str, Any], width_mm: int, length_mm: int,
) -> dict[str, int] | None:
    """3D swept-capsule contact against the target's compact body footprint."""
    ax, ay, az = int(actor.get("x_mm", 0)), int(actor.get("y_mm", 0)), _z_mm(actor)
    tx, ty, tz = int(target.get("x_mm", 0)), int(target.get("y_mm", 0)), _z_mm(target)
    dx, dy, dz = _aim_vector_3d(actor, aim)
    vx, vy, vz = tx - ax, ty - ay, tz - az
    len2 = dx * dx + dy * dy + dz * dz
    if len2 <= 0:
        return None
    dot = vx * dx + vy * dy + vz * dz
    if dot < 0:
        return None
    if dot * dot > length_mm * length_mm * len2:
        return None
    v2 = vx * vx + vy * vy + vz * vz
    perpendicular_num2 = max(0, v2 * len2 - dot * dot)
    radius = max(0, int(target.get("body_radius_mm", DEFAULT_BODY_RADIUS_MM)))
    half_width = max(1, width_mm // 2) + radius
    if perpendicular_num2 > half_width * half_width * len2:
        return None
    direction_len = max(1, math.isqrt(len2))
    along_mm = max(0, dot // direction_len)
    centerline_mm = math.isqrt(perpendicular_num2) // direction_len
    chord_sq = max(0, half_width * half_width - centerline_mm * centerline_mm)
    half_chord = math.isqrt(chord_sq)
    return {
        "along_mm": along_mm,
        "distance_to_centerline_mm": centerline_mm,
        "entry_mm": max(0, along_mm - half_chord),
        "exit_mm": along_mm + half_chord,
        "vertical_offset_mm": abs(tz - az),
    }


def _rect_segment_entry_milli(
    start_x: int, start_y: int, end_x: int, end_y: int, obstacle: Mapping[str, Any]
) -> int | None:
    """Integer Liang-Barsky style test, returning approximate t in 0..1,000,000."""
    try:
        min_x = int(obstacle["min_x_mm"]); max_x = int(obstacle["max_x_mm"])
        min_y = int(obstacle["min_y_mm"]); max_y = int(obstacle["max_y_mm"])
    except (KeyError, TypeError, ValueError):
        return None
    if min_x > max_x or min_y > max_y:
        return None
    dx, dy = end_x - start_x, end_y - start_y
    low, high = 0.0, 1.0
    for p, q in ((-dx, start_x - min_x), (dx, max_x - start_x), (-dy, start_y - min_y), (dy, max_y - start_y)):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            low = max(low, r)
        else:
            high = min(high, r)
        if low > high:
            return None
    if high < 0 or low > 1:
        return None
    return max(0, min(1_000_000, int(round(max(0.0, low) * 1_000_000))))


def _circle_segment_entry_milli(
    start_x: int, start_y: int, end_x: int, end_y: int, obstacle: Mapping[str, Any]
) -> int | None:
    try:
        cx = int(obstacle["x_mm"]); cy = int(obstacle["y_mm"]); radius = max(0, int(obstacle["radius_mm"]))
    except (KeyError, TypeError, ValueError):
        return None
    dx, dy = end_x - start_x, end_y - start_y
    len2 = dx * dx + dy * dy
    if len2 <= 0:
        return 0 if (start_x-cx)**2 + (start_y-cy)**2 <= radius*radius else None
    vx, vy = cx - start_x, cy - start_y
    dot = vx * dx + vy * dy
    t = max(0.0, min(1.0, dot / len2))
    px = start_x + dx * t; py = start_y + dy * t
    if (px-cx)**2 + (py-cy)**2 > radius * radius:
        return None
    # Entry-point approximation is deliberately conservative: obstacle contact
    # can occur no later than closest approach.
    return max(0, min(1_000_000, int(round(t * 1_000_000))))


def obstacle_segment_entry_milli(
    start: Mapping[str, Any], end: Mapping[str, Any], obstacle: Mapping[str, Any]
) -> int | None:
    if obstacle.get("zone_ref") not in (None, start.get("zone_ref"), end.get("zone_ref")):
        return None
    sx, sy = int(start.get("x_mm", 0)), int(start.get("y_mm", 0))
    ex, ey = int(end.get("x_mm", 0)), int(end.get("y_mm", 0))
    shape = str(obstacle.get("shape", "rect"))
    if shape == "circle":
        return _circle_segment_entry_milli(sx, sy, ex, ey, obstacle)
    return _rect_segment_entry_milli(sx, sy, ex, ey, obstacle)


def first_blocking_obstacle(
    start: Mapping[str, Any],
    end: Mapping[str, Any],
    obstacles: Sequence[Mapping[str, Any]],
    *,
    channel: str,
) -> Mapping[str, Any] | None:
    flag = {
        "los": "blocks_los",
        "projectile": "blocks_projectiles",
        "movement": "blocks_movement",
        "melee": "blocks_melee",
    }.get(channel, "blocks_projectiles")
    hits: list[tuple[int, str, Mapping[str, Any]]] = []
    for row in obstacles:
        if not isinstance(row, Mapping) or row.get(flag) is not True:
            continue
        t = obstacle_segment_entry_milli(start, end, row)
        if t is None:
            continue
        raw_height = row.get("height_mm")
        if isinstance(raw_height, int) and not isinstance(raw_height, bool) and raw_height > 0:
            base_raw = row.get("elevation_mm", 0)
            base_z = int(base_raw) if isinstance(base_raw, int) and not isinstance(base_raw, bool) else 0
            start_z = _z_mm(start); end_z = _z_mm(end)
            segment_z = start_z + (end_z - start_z) * t // 1_000_000
            if segment_z < base_z or segment_z > base_z + raw_height:
                continue
        hits.append((t, str(row.get("obstacle_ref", "")), row))
    return min(hits, default=(0, "", None))[2]


def line_of_sight_clear(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    actor_ref: str,
    target_ref: str,
    obstacles: Sequence[Mapping[str, Any]] = (),
) -> bool:
    actor = positions.get(actor_ref); target = positions.get(target_ref)
    if not isinstance(actor, Mapping) or not isinstance(target, Mapping):
        return False
    if actor.get("zone_ref") != target.get("zone_ref"):
        return False
    return first_blocking_obstacle(actor, target, obstacles, channel="los") is None


def cover_milli_between(
    positions: Mapping[str, Mapping[str, Any]],
    *, actor_ref: str, target_ref: str, obstacles: Sequence[Mapping[str, Any]] = ()
) -> int:
    actor = positions.get(actor_ref); target = positions.get(target_ref)
    if not isinstance(actor, Mapping) or not isinstance(target, Mapping):
        return 0
    cover = 0
    for row in obstacles:
        if not isinstance(row, Mapping):
            continue
        if obstacle_segment_entry_milli(actor, target, row) is None:
            continue
        raw = row.get("cover_milli", 0)
        if isinstance(raw, int) and not isinstance(raw, bool):
            cover = max(cover, max(0, min(1000, raw)))
    return cover


def _geometry_length_mm(
    geometry: Mapping[str, Any] | None, maximum_range_m: float | int | None, actor: Mapping[str, Any], aim: Mapping[str, Any] | None
) -> int:
    value = 0
    if isinstance(maximum_range_m, (int, float)) and not isinstance(maximum_range_m, bool) and maximum_range_m > 0:
        value = int(round(float(maximum_range_m) * 1000))
    if isinstance(geometry, Mapping):
        for key in ("length_m", "maximum_path_m", "maximum_range_m"):
            raw = geometry.get(key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
                value = max(value, int(round(float(raw) * 1000)))
    if value <= 0 and isinstance(aim, Mapping):
        value = max(1, planar_distance_mm(actor, aim))
    return max(1, value)


def _geometry_width_mm(geometry: Mapping[str, Any] | None) -> int:
    width = DEFAULT_ATTACK_WIDTH_MM
    if isinstance(geometry, Mapping):
        raw = geometry.get("width_m")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
            width = int(round(float(raw) * 1000))
        dims = geometry.get("dimensions_m")
        if isinstance(dims, Sequence) and not isinstance(dims, (str, bytes, bytearray)) and dims:
            first = dims[0]
            if isinstance(first, (int, float)) and not isinstance(first, bool) and first > 0:
                width = int(round(float(first) * 1000))
    return max(1, width)


def trace_attack_geometry(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    actor_ref: str,
    aim_ref: str | None,
    body_refs: Sequence[str],
    geometry: Mapping[str, Any] | None,
    obstacles: Sequence[Mapping[str, Any]] = (),
    target_limit: int = 1,
    maximum_range_m: float | int | None = None,
    channel: str = "melee",
    trajectory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Trace one physical attack and explain spatial contacts.

    `aim_ref` establishes intent/direction. `body_refs` establishes physical
    occupants, including allies. The first physical obstruction therefore wins
    over intended-target identity.
    """
    actor = positions.get(actor_ref)
    if not isinstance(actor, Mapping):
        return {"shape": "invalid", "contacts": [], "blocked_by": None}
    # A supplied trajectory freezes a launch/aim lane. Projectiles always use
    # this after release; committed melee actions may use it when tracking is
    # insufficient to redirect during remaining startup. Target identity never
    # bends a frozen physical lane.
    if isinstance(trajectory, Mapping):
        try:
            actor_snapshot = dict(actor)
            actor_snapshot["x_mm"] = int(trajectory["launch_x_mm"])
            actor_snapshot["y_mm"] = int(trajectory["launch_y_mm"])
            actor_snapshot["elevation_mm"] = int(trajectory.get("launch_elevation_mm", actor.get("elevation_mm", 0)))
            aim = dict(actor_snapshot)
            aim["x_mm"] = int(trajectory["aim_x_mm"])
            aim["y_mm"] = int(trajectory["aim_y_mm"])
            aim["elevation_mm"] = int(trajectory.get("aim_elevation_mm", actor_snapshot.get("elevation_mm", 0)))
            actor = actor_snapshot
        except (KeyError, TypeError, ValueError):
            aim = positions.get(aim_ref) if isinstance(aim_ref, str) else None
    else:
        aim = positions.get(aim_ref) if isinstance(aim_ref, str) else None
    if not isinstance(aim, Mapping):
        aim = None
    shape = str(geometry.get("shape", "direct") if isinstance(geometry, Mapping) else "direct").lower()
    limit = max(1, int(target_limit))
    length_mm = _geometry_length_mm(geometry, maximum_range_m, actor, aim)
    width_mm = _geometry_width_mm(geometry)

    contacts: list[dict[str, Any]] = []
    blocked_by: str | None = None

    if shape in {"radius", "circle", "area_radius"}:
        radius_m = geometry.get("radius_m", 0) if isinstance(geometry, Mapping) else 0
        radius_mm = int(round(float(radius_m) * 1000)) if isinstance(radius_m, (int, float)) and not isinstance(radius_m, bool) else 0
        center = aim if isinstance(aim, Mapping) else actor
        if radius_mm <= 0:
            return {"shape": shape, "contacts": [], "blocked_by": None, "radius_mm": 0}
        for ref in body_refs:
            target = positions.get(ref)
            if ref == actor_ref or not isinstance(target, Mapping) or target.get("zone_ref") != center.get("zone_ref"):
                continue
            d = distance_mm(center, target)
            body_radius = max(0, int(target.get("body_radius_mm", DEFAULT_BODY_RADIUS_MM)))
            if d <= radius_mm + body_radius:
                contacts.append({"participant_ref": ref, "along_mm": d, "distance_to_centerline_mm": 0, "entry_mm": max(0, d-body_radius), "exit_mm": d+body_radius, "intended": ref == aim_ref})
        contacts.sort(key=lambda r: (r["along_mm"], r["participant_ref"]))
        return {"shape": shape, "contacts": contacts[:limit], "blocked_by": None, "radius_mm": radius_mm}

    if shape in {"cone", "fan"}:
        half_angle = 30_000
        if isinstance(geometry, Mapping):
            raw = geometry.get("half_angle_deg", geometry.get("angle_deg", 60) / 2 if isinstance(geometry.get("angle_deg"), (int, float)) else 30)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                half_angle = max(1, int(round(float(raw) * 1000)))
        aim_facing = facing_to_target_mdeg(actor, aim) if isinstance(aim, Mapping) else int(actor.get("facing_mdeg", 0))
        for ref in body_refs:
            target = positions.get(ref)
            if ref == actor_ref or not isinstance(target, Mapping) or target.get("zone_ref") != actor.get("zone_ref"):
                continue
            d = distance_mm(actor, target)
            body_radius = max(0, int(target.get("body_radius_mm", DEFAULT_BODY_RADIUS_MM)))
            if d > length_mm + body_radius:
                continue
            angle = angular_difference_mdeg(facing_to_target_mdeg(actor, target), aim_facing)
            angular_allowance = int(round(math.degrees(math.atan2(body_radius, max(1, d))) * 1000))
            if angle <= half_angle + angular_allowance:
                contacts.append({"participant_ref": ref, "along_mm": d, "distance_to_centerline_mm": angle, "entry_mm": max(0, d-body_radius), "exit_mm": d+body_radius, "intended": ref == aim_ref})
        contacts.sort(key=lambda r: (r["along_mm"], r["distance_to_centerline_mm"], r["participant_ref"]))
        return {"shape": shape, "contacts": contacts[:limit], "blocked_by": None, "length_mm": length_mm, "half_angle_mdeg": half_angle}

    if shape in {"arc", "sweep", "swept_arc"}:
        half_angle = 70_000
        min_range_mm = 0
        if isinstance(geometry, Mapping):
            raw = geometry.get("half_angle_deg", 70)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                half_angle = max(1, int(round(float(raw) * 1000)))
            raw_min = geometry.get("minimum_range_m", 0)
            if isinstance(raw_min, (int, float)) and not isinstance(raw_min, bool):
                min_range_mm = max(0, int(round(float(raw_min) * 1000)))
        aim_facing = facing_to_target_mdeg(actor, aim) if isinstance(aim, Mapping) else int(actor.get("facing_mdeg", 0))
        vertical_half_mm = 1100
        if isinstance(geometry, Mapping):
            raw_height = geometry.get("height_m", 2.2)
            if isinstance(raw_height, (int, float)) and not isinstance(raw_height, bool) and raw_height > 0:
                vertical_half_mm = max(100, int(round(float(raw_height) * 500)))
        for ref in body_refs:
            target = positions.get(ref)
            if ref == actor_ref or not isinstance(target, Mapping) or target.get("zone_ref") != actor.get("zone_ref"):
                continue
            d = planar_distance_mm(actor, target)
            body_radius = max(0, int(target.get("body_radius_mm", DEFAULT_BODY_RADIUS_MM)))
            if abs(_z_mm(target) - _z_mm(actor)) > vertical_half_mm + body_radius:
                continue
            if d + body_radius < min_range_mm or d - body_radius > length_mm:
                continue
            angle = angular_difference_mdeg(facing_to_target_mdeg(actor, target), aim_facing)
            if angle <= half_angle:
                contacts.append({"participant_ref": ref, "along_mm": d, "distance_to_centerline_mm": angle, "entry_mm": max(0, d-body_radius), "exit_mm": d+body_radius, "intended": ref == aim_ref})
        contacts.sort(key=lambda r: (r["along_mm"], r["distance_to_centerline_mm"], r["participant_ref"]))
        return {"shape": shape, "contacts": contacts[:limit], "blocked_by": None, "length_mm": length_mm, "half_angle_mdeg": half_angle}

    # Direct, line, lane, thrust and projectile contacts all use the same swept
    # capsule. This makes intervening bodies and friendly fire emerge naturally.
    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for ref in body_refs:
        target = positions.get(ref)
        if ref == actor_ref or not isinstance(target, Mapping) or target.get("zone_ref") != actor.get("zone_ref"):
            continue
        metrics = _lane_contact_metrics(actor=actor, aim=aim, target=target, width_mm=width_mm, length_mm=length_mm)
        if metrics is None:
            continue
        row = {"participant_ref": ref, **metrics, "intended": ref == aim_ref}
        ranked.append((metrics["entry_mm"], metrics["distance_to_centerline_mm"], ref, row))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))

    # Obstacles are evaluated on the same physical segment. A blocker before a
    # body prevents contacts behind it for channels it blocks.
    dx, dy = _aim_vector(actor, aim)
    dlen = max(1, math.isqrt(dx*dx + dy*dy))
    end = dict(actor)
    end["x_mm"] = int(actor.get("x_mm", 0)) + dx * length_mm // dlen
    end["y_mm"] = int(actor.get("y_mm", 0)) + dy * length_mm // dlen
    blocker = first_blocking_obstacle(actor, end, obstacles, channel=channel)
    blocker_entry_mm: int | None = None
    if isinstance(blocker, Mapping):
        t = obstacle_segment_entry_milli(actor, end, blocker)
        if t is not None:
            blocker_entry_mm = length_mm * t // 1_000_000
            blocked_by = str(blocker.get("obstacle_ref", "obstacle"))

    for entry_mm, _cross, _ref, row in ranked:
        if blocker_entry_mm is not None and entry_mm >= blocker_entry_mm:
            break
        contacts.append(row)
        if len(contacts) >= limit:
            break
    return {
        "shape": shape,
        "contacts": contacts,
        "blocked_by": blocked_by,
        "length_mm": length_mm,
        "width_mm": width_mm,
        "aim_ref": aim_ref,
        "channel": channel,
        "trajectory": dict(trajectory) if isinstance(trajectory, Mapping) else None,
    }


def targets_intersecting_geometry(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    actor_ref: str,
    candidate_refs: Sequence[str],
    geometry: Mapping[str, Any] | None,
    target_limit: int = 1,
    maximum_range_m: float | int | None = None,
    aim_ref: str | None = None,
    obstacles: Sequence[Mapping[str, Any]] = (),
    channel: str = "melee",
    trajectory: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    candidates = tuple(dict.fromkeys(ref for ref in candidate_refs if isinstance(ref, str) and ref != actor_ref))
    if not candidates:
        return ()
    if aim_ref not in candidates:
        aim_ref = nearest_target(positions, actor_ref=actor_ref, candidate_refs=candidates)
    if aim_ref is None:
        return ()
    trace = trace_attack_geometry(
        positions,
        actor_ref=actor_ref,
        aim_ref=aim_ref,
        body_refs=candidates,
        geometry=geometry,
        obstacles=obstacles,
        target_limit=target_limit,
        maximum_range_m=maximum_range_m,
        channel=channel,
        trajectory=trajectory,
    )
    return tuple(str(row["participant_ref"]) for row in trace["contacts"])


def path_clear(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    actor_ref: str,
    end_x_mm: int,
    end_y_mm: int,
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]] = (),
    clearance_mm: int | None = None,
) -> bool:
    actor = positions.get(actor_ref)
    if not isinstance(actor, Mapping):
        return False
    radius = max(50, int(actor.get("body_radius_mm", DEFAULT_BODY_RADIUS_MM)))
    width = max(100, (clearance_mm if clearance_mm is not None else radius * 2))
    end = dict(actor); end["x_mm"] = int(end_x_mm); end["y_mm"] = int(end_y_mm)
    if first_blocking_obstacle(actor, end, obstacles, channel="movement") is not None:
        return False
    dx = int(end_x_mm) - int(actor.get("x_mm", 0)); dy = int(end_y_mm) - int(actor.get("y_mm", 0))
    length = max(1, math.isqrt(dx*dx+dy*dy))
    for ref in body_refs:
        if ref == actor_ref:
            continue
        target = positions.get(ref)
        if not isinstance(target, Mapping) or target.get("zone_ref") != actor.get("zone_ref"):
            continue
        metrics = _lane_contact_metrics(actor=actor, aim=end, target=target, width_mm=width, length_mm=length)
        if metrics is not None:
            return False
    return True


def open_retreat_corridors(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    actor_ref: str,
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]] = (),
    distance_mm_value: int = DEFAULT_RETREAT_DISTANCE_MM,
    direction_count: int = 16,
) -> tuple[dict[str, int], ...]:
    actor = positions.get(actor_ref)
    if not isinstance(actor, Mapping) or direction_count < 4:
        return ()
    rows: list[dict[str, int]] = []
    ax, ay = int(actor.get("x_mm", 0)), int(actor.get("y_mm", 0))
    for index in range(direction_count):
        angle_mdeg = index * 360_000 // direction_count
        rad = math.radians(angle_mdeg / 1000.0)
        ex = ax + int(round(math.cos(rad) * distance_mm_value))
        ey = ay + int(round(math.sin(rad) * distance_mm_value))
        if path_clear(positions, actor_ref=actor_ref, end_x_mm=ex, end_y_mm=ey, body_refs=body_refs, obstacles=obstacles):
            rows.append({"angle_mdeg": angle_mdeg, "end_x_mm": ex, "end_y_mm": ey})
    return tuple(rows)


def surrounding_state(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    actor_ref: str,
    hostile_refs: Sequence[str],
    all_body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]] = (),
    control_radius_mm: int = 4_000,
) -> dict[str, Any]:
    actor = positions.get(actor_ref)
    if not isinstance(actor, Mapping):
        return {"surrounded": False, "controlled_sectors": 0, "open_corridors": 0}
    sectors: set[int] = set()
    for ref in hostile_refs:
        target = positions.get(ref)
        if not isinstance(target, Mapping) or target.get("zone_ref") != actor.get("zone_ref"):
            continue
        if planar_distance_mm(actor, target) > control_radius_mm + int(target.get("body_radius_mm", DEFAULT_BODY_RADIUS_MM)):
            continue
        angle = facing_to_target_mdeg(actor, target)
        sectors.add((angle * 8 // 360_000) % 8)
    corridors = open_retreat_corridors(positions, actor_ref=actor_ref, body_refs=all_body_refs, obstacles=obstacles, direction_count=16)
    # Surrounding is a physical state: pressure from at least four directional
    # sectors and no usable 5 m corridor. A large open lane defeats the label.
    return {
        "surrounded": len(sectors) >= 4 and len(corridors) == 0,
        "controlled_sectors": len(sectors),
        "open_corridors": len(corridors),
        "open_corridor_angles_mdeg": [row["angle_mdeg"] for row in corridors],
    }


__all__ = [
    "DEFAULT_BODY_RADIUS_MM",
    "DEFAULT_LATERAL_SPACING_MM",
    "RANGE_BAND_CENTERS_MM",
    "angular_difference_mdeg",
    "cover_milli_between",
    "distance_mm",
    "facing_to_target_mdeg",
    "first_blocking_obstacle",
    "initial_positions",
    "line_of_sight_clear",
    "nearest_target",
    "open_retreat_corridors",
    "path_clear",
    "planar_distance_mm",
    "range_band_from_distance_mm",
    "surrounding_state",
    "targets_intersecting_geometry",
    "trace_attack_geometry",
]
