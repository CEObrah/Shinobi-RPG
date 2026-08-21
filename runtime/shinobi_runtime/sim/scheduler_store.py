"""Deterministic sharded persistence for the causal scheduler.

The scheduler root owns the world-time frontier and compact routing metadata.
Exact host state is hash-sharded. Exact events are stored by due date, with a
small per-year day index. This lets time advancement retrieve only work that can
become due before the requested horizon instead of deserializing lifetime host
and event collections.

The in-memory :class:`CausalSchedulerRegistry` remains the reducer-facing model.
This module only changes persistence and retrieval shape; domain facts still
live in their own authorities.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence

from .events import CampaignTime, EventQueue, ScheduledEvent
from .scheduler import CausalSchedulerRegistry, SchedulerHost, _validate_scheduled_event_kind

ROOT_SCHEMA = "causal-scheduler-index"
HOST_SHARD_SCHEMA = "causal-scheduler-host-shard"
EVENT_DAY_SCHEMA = "causal-scheduler-event-day"
EVENT_YEAR_INDEX_SCHEMA = "causal-scheduler-event-year-index"
ROOT_OWNER_ID = "runtime.causal_scheduler"
ROOT_OWNER_TYPE = "causal_scheduler"
STORAGE_VERSION = 2
HOST_BUCKET_HEX = 2
BASE_DIR = "state/time/causal-scheduler"
HOST_DIR = BASE_DIR + "/hosts"
EVENT_DIR = BASE_DIR + "/events"
EVENT_INDEX_DIR = BASE_DIR + "/event-index"


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _host_bucket(host_id: str) -> str:
    if not isinstance(host_id, str) or not host_id:
        raise ValueError("scheduler host id must be non-empty text")
    return hashlib.sha256(host_id.encode("utf-8")).hexdigest()[:HOST_BUCKET_HEX]


def host_shard_path(host_id: str) -> str:
    return f"{HOST_DIR}/{_host_bucket(host_id)}.json"


def event_day_key(value: CampaignTime) -> str:
    return f"SE-{value.year:04d}-{value.month:02d}-{value.day:02d}"


def event_day_path(value: CampaignTime) -> str:
    return f"{EVENT_DIR}/{value.year:04d}/{value.year:04d}-{value.month:02d}-{value.day:02d}.json"


def event_year_index_path(year: int) -> str:
    return f"{EVENT_INDEX_DIR}/{year:04d}.json"


def _event_route(event: ScheduledEvent) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "due_at": str(event.due_at),
        "path": event_day_path(event.due_at),
        "dedupe_key": event.dedupe_key,
    }


def _validate_root(record: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("causal scheduler root must be an object")
    if (
        record.get("schema") != ROOT_SCHEMA
        or record.get("owner_id") != ROOT_OWNER_ID
        or record.get("owner_type") != ROOT_OWNER_TYPE
        or record.get("authority") is not True
        or record.get("storage_version") != STORAGE_VERSION
    ):
        raise ValueError("invalid sharded causal scheduler root")
    for key in ("host_buckets", "event_years", "metrics"):
        if not isinstance(record.get(key), Mapping):
            raise ValueError(f"causal scheduler root {key} must be an object")
    for key in ("host_count", "pending_event_count"):
        value = record.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"causal scheduler root {key} must be non-negative")
    CampaignTime.parse(record.get("world_time"))
    CampaignTime.parse(record.get("seeded_at"))
    next_due = record.get("next_due")
    if next_due is not None:
        CampaignTime.parse(next_due)
    return copy.deepcopy(dict(record))


def _read_optional_json(reader: Any, path: str) -> Optional[Dict[str, Any]]:
    raw = reader.read_optional_bytes(path)
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid scheduler JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"scheduler JSON must be an object: {path}")
    return value


@dataclass
class SchedulerSnapshot:
    root: Dict[str, Any]
    initial_hosts: Dict[str, Dict[str, Any]]
    initial_events: Dict[str, ScheduledEvent]
    initial_host_rows: Dict[str, Dict[str, Any]]
    full: bool


class SchedulerStore:
    """Read/write facade over sharded causal scheduler authority."""

    def __init__(self, reader: Any, root_path: str = "state/time/causal-scheduler.json") -> None:
        self.reader = reader
        self.root_path = root_path

    def root(self) -> Dict[str, Any]:
        raw = _read_optional_json(self.reader, self.root_path)
        if raw is None:
            raise FileNotFoundError(self.root_path)
        return _validate_root(raw)

    @staticmethod
    def _blank_host_shard(bucket: str) -> Dict[str, Any]:
        return {
            "schema": HOST_SHARD_SCHEMA,
            "bucket": bucket,
            "authority": True,
            "hosts": {},
        }

    @staticmethod
    def _blank_event_day(day: str) -> Dict[str, Any]:
        return {
            "schema": EVENT_DAY_SCHEMA,
            "day": day,
            "authority": True,
            "events": [],
        }

    @staticmethod
    def _blank_year_index(year: int) -> Dict[str, Any]:
        return {
            "schema": EVENT_YEAR_INDEX_SCHEMA,
            "year": year,
            "authority": False,
            "days": {},
        }

    def _host_shard(self, bucket: str) -> Dict[str, Any]:
        path = f"{HOST_DIR}/{bucket}.json"
        value = _read_optional_json(self.reader, path)
        if value is None:
            return self._blank_host_shard(bucket)
        if (
            value.get("schema") != HOST_SHARD_SCHEMA
            or value.get("bucket") != bucket
            or value.get("authority") is not True
            or not isinstance(value.get("hosts"), Mapping)
        ):
            raise ValueError("causal scheduler host shard invalid")
        return copy.deepcopy(value)

    def _event_day(self, path: str, day: str) -> Dict[str, Any]:
        value = _read_optional_json(self.reader, path)
        if value is None:
            return self._blank_event_day(day)
        if (
            value.get("schema") != EVENT_DAY_SCHEMA
            or value.get("day") != day
            or value.get("authority") is not True
            or not isinstance(value.get("events"), list)
        ):
            raise ValueError("causal scheduler event day invalid")
        # Parsing here also validates event shape and the closed scheduler kind vocabulary.
        parsed = [ScheduledEvent.from_record(item) for item in value["events"]]
        for event in parsed:
            _validate_scheduled_event_kind(event)
            if event_day_key(event.due_at) != day:
                raise ValueError("causal scheduler event stored in wrong day shard")
        if len({event.event_id for event in parsed}) != len(parsed):
            raise ValueError("duplicate scheduler event in day shard")
        return copy.deepcopy(value)

    def _year_index(self, year: int) -> Dict[str, Any]:
        path = event_year_index_path(year)
        value = _read_optional_json(self.reader, path)
        if value is None:
            return self._blank_year_index(year)
        if (
            value.get("schema") != EVENT_YEAR_INDEX_SCHEMA
            or value.get("year") != year
            or value.get("authority") is not False
            or not isinstance(value.get("days"), Mapping)
        ):
            raise ValueError("causal scheduler event year index invalid")
        return copy.deepcopy(value)

    def _load_host_rows(self, host_ids: Iterable[str]) -> tuple[Dict[str, SchedulerHost], Dict[str, Dict[str, Any]]]:
        hosts: Dict[str, SchedulerHost] = {}
        rows: Dict[str, Dict[str, Any]] = {}
        by_bucket: Dict[str, list[str]] = {}
        for host_id in sorted(set(host_ids)):
            by_bucket.setdefault(_host_bucket(host_id), []).append(host_id)
        for bucket, wanted in by_bucket.items():
            shard = self._host_shard(bucket)
            mapping = shard.get("hosts")
            if not isinstance(mapping, Mapping):
                raise ValueError("causal scheduler host shard invalid")
            for host_id in wanted:
                row = mapping.get(host_id)
                if not isinstance(row, Mapping):
                    raise ValueError(f"scheduler event targets unknown host: {host_id}")
                host_record = row.get("host")
                event_routes = row.get("event_routes")
                if not isinstance(host_record, Mapping) or not isinstance(event_routes, list):
                    raise ValueError("causal scheduler host row invalid")
                wrapper = SchedulerHost.from_record(host_record)
                if wrapper.state.host_id != host_id:
                    raise ValueError("causal scheduler host row key mismatch")
                hosts[host_id] = wrapper
                rows[host_id] = copy.deepcopy(dict(row))
        return hosts, rows

    def _event_records_for_days(self, day_rows: Sequence[tuple[str, Mapping[str, Any]]]) -> list[ScheduledEvent]:
        events: list[ScheduledEvent] = []
        seen: set[str] = set()
        for day, meta in day_rows:
            path = meta.get("path")
            if not isinstance(path, str) or not path:
                raise ValueError("causal scheduler day route invalid")
            shard = self._event_day(path, day)
            for item in shard["events"]:
                event = ScheduledEvent.from_record(item)
                if event.event_id in seen:
                    raise ValueError("duplicate scheduler event across day shards")
                seen.add(event.event_id)
                events.append(event)
        return events

    def load_host(self, host_id: str) -> Optional[SchedulerHost]:
        """Load one exact host without scanning unrelated scheduler shards."""
        shard = self._host_shard(_host_bucket(host_id))
        mapping = shard.get("hosts")
        if not isinstance(mapping, Mapping):
            raise ValueError("causal scheduler host shard invalid")
        row = mapping.get(host_id)
        if row is None:
            return None
        if not isinstance(row, Mapping):
            raise ValueError("causal scheduler host row invalid")
        host_record = row.get("host")
        event_routes = row.get("event_routes")
        if not isinstance(host_record, Mapping) or not isinstance(event_routes, list):
            raise ValueError("causal scheduler host row invalid")
        wrapper = SchedulerHost.from_record(host_record)
        if wrapper.state.host_id != host_id:
            raise ValueError("causal scheduler host row key mismatch")
        return wrapper

    def load_hosts(self, host_ids: Iterable[str]) -> Dict[str, SchedulerHost]:
        """Load an exact bounded host set through deterministic hash routing."""
        hosts, _ = self._load_host_rows(host_ids)
        return hosts

    def load(self, *, target: Optional[CampaignTime] = None, full: bool = False) -> CausalSchedulerRegistry:
        root = self.root()
        world_time = CampaignTime.parse(root["world_time"])
        seeded_at = CampaignTime.parse(root["seeded_at"])
        event_years = root["event_years"]
        day_rows: list[tuple[str, Mapping[str, Any]]] = []

        if full:
            for year_key, year_meta in sorted(event_years.items()):
                if not isinstance(year_meta, Mapping):
                    raise ValueError("causal scheduler year route invalid")
                year = int(year_key)
                index = self._year_index(year)
                for day, meta in sorted(index["days"].items()):
                    if isinstance(meta, Mapping) and int(meta.get("count", 0)) > 0:
                        day_rows.append((day, meta))
        elif target is not None:
            next_due = root.get("next_due")
            if next_due is not None and CampaignTime.parse(next_due) <= target:
                for year_key, year_meta in sorted(event_years.items()):
                    if not isinstance(year_meta, Mapping):
                        raise ValueError("causal scheduler year route invalid")
                    earliest = year_meta.get("earliest_due")
                    if earliest is None or CampaignTime.parse(earliest) > target:
                        continue
                    year = int(year_key)
                    index = self._year_index(year)
                    for day, meta in sorted(index["days"].items()):
                        if not isinstance(meta, Mapping) or int(meta.get("count", 0)) <= 0:
                            continue
                        day_earliest = meta.get("earliest_due")
                        if day_earliest is not None and CampaignTime.parse(day_earliest) <= target:
                            day_rows.append((day, meta))
        else:
            raise ValueError("scheduler load requires target or full=True")

        events = self._event_records_for_days(day_rows)
        if not full and target is not None:
            # A loaded day may contain later same-day events. Retain them so any
            # due host's exact local frontier remains coherent, but never load a
            # later day merely because it shares a year with the target.
            relevant_host_ids = {event.target_host for event in events if event.due_at <= target}
            events = [event for event in events if event.target_host in relevant_host_ids]
        else:
            relevant_host_ids = {event.target_host for event in events}

        if full:
            host_ids: list[str] = []
            for bucket, meta in sorted(root["host_buckets"].items()):
                if not isinstance(meta, Mapping):
                    raise ValueError("causal scheduler host bucket route invalid")
                shard = self._host_shard(bucket)
                host_ids.extend(str(host_id) for host_id in shard["hosts"])
            hosts, host_rows = self._load_host_rows(host_ids)
        else:
            hosts, host_rows = self._load_host_rows(relevant_host_ids)

        # A partial window only validates loaded host frontiers against loaded
        # events. All loaded hosts are hosts whose next_due is within the target
        # window, so their earliest event must be present.
        queue = EventQueue(events)
        for host_id, wrapper in hosts.items():
            due = min((e.due_at for e in events if e.target_host == host_id), default=None)
            if wrapper.state.next_due != due:
                raise ValueError(f"scheduler host {host_id} next_due does not match loaded due window")

        registry = CausalSchedulerRegistry(
            world_time=world_time,
            hosts=hosts,
            queue=queue,
            seeded_at=seeded_at,
            bootstrap_source=root.get("bootstrap_source"),
            metrics=copy.deepcopy(dict(root.get("metrics", {}))),
        )
        registry.metrics["host_count"] = root["host_count"]
        registry.metrics["pending_event_count"] = root["pending_event_count"]
        setattr(registry, "_scheduler_snapshot", SchedulerSnapshot(
            root=root,
            initial_hosts={host_id: copy.deepcopy(wrapper.to_record()) for host_id, wrapper in hosts.items()},
            initial_events={event.event_id: event for event in events},
            initial_host_rows=host_rows,
            full=full,
        ))
        return registry

    def _update_day_file(
        self,
        *,
        path: str,
        removed_ids: set[str],
        additions: Mapping[str, ScheduledEvent],
    ) -> tuple[Dict[str, Any], str]:
        # Day is derivable from any addition; otherwise derive from the path.
        existing = _read_optional_json(self.reader, path)
        if existing is None:
            if additions:
                first = next(iter(additions.values()))
                day = event_day_key(first.due_at)
            else:
                raise ValueError("cannot remove scheduler events from missing day shard")
            record = self._blank_event_day(day)
        else:
            day = existing.get("day")
            if not isinstance(day, str):
                raise ValueError("causal scheduler event day invalid")
            record = self._event_day(path, day)
        by_id: Dict[str, ScheduledEvent] = {
            event.event_id: event
            for event in (ScheduledEvent.from_record(item) for item in record["events"])
        }
        for event_id in removed_ids:
            by_id.pop(event_id, None)
        for event_id, event in additions.items():
            if event_day_path(event.due_at) != path:
                raise ValueError("scheduler event addition routed to wrong day")
            existing_event = by_id.get(event_id)
            if existing_event is not None and existing_event.fingerprint != event.fingerprint:
                raise ValueError("scheduler event id conflict")
            # Global dedupe is host-local in production. Host route validation
            # below rejects reusing one active kind/dedupe identity differently.
            by_id[event_id] = event
        ordered = sorted(by_id.values())
        EventQueue(ordered)  # exact ID and dedupe validation within the day shard
        record["events"] = [event.to_record() for event in ordered]
        return record, day

    def write_images(self, registry: CausalSchedulerRegistry) -> Dict[str, bytes]:
        snapshot = getattr(registry, "_scheduler_snapshot", None)
        if not isinstance(snapshot, SchedulerSnapshot):
            raise ValueError("scheduler registry lacks persistence snapshot")
        root = copy.deepcopy(snapshot.root)
        initial_events = snapshot.initial_events
        final_events = {event.event_id: event for event in registry.queue.snapshot()}
        removed_event_ids = set(initial_events) - set(final_events)
        additions: Dict[str, ScheduledEvent] = {}
        for event_id, event in final_events.items():
            prior = initial_events.get(event_id)
            if prior is None or prior.fingerprint != event.fingerprint:
                additions[event_id] = event
                if prior is not None:
                    removed_event_ids.add(event_id)

        affected_day_paths: set[str] = {
            event_day_path(initial_events[event_id].due_at)
            for event_id in removed_event_ids
            if event_id in initial_events
        }
        affected_day_paths.update(event_day_path(event.due_at) for event in additions.values())
        writes: Dict[str, bytes] = {}
        year_records: Dict[int, Dict[str, Any]] = {}

        for path in sorted(affected_day_paths):
            removed_here = {
                event_id for event_id in removed_event_ids
                if event_id in initial_events and event_day_path(initial_events[event_id].due_at) == path
            }
            additions_here = {
                event_id: event for event_id, event in additions.items()
                if event_day_path(event.due_at) == path
            }
            record, day = self._update_day_file(path=path, removed_ids=removed_here, additions=additions_here)
            writes[path] = _json_bytes(record)
            year = int(day[3:7])
            year_record = year_records.setdefault(year, self._year_index(year))
            parsed = [ScheduledEvent.from_record(item) for item in record["events"]]
            meta = {
                "path": path,
                "count": len(parsed),
                "earliest_due": None if not parsed else str(min(e.due_at for e in parsed)),
                "latest_due": None if not parsed else str(max(e.due_at for e in parsed)),
            }
            year_record["days"][day] = meta

        # Persist changed year routing indexes and refresh compact root year metadata.
        for year, year_record in sorted(year_records.items()):
            path = event_year_index_path(year)
            writes[path] = _json_bytes(year_record)
            active_days = [
                meta for meta in year_record["days"].values()
                if isinstance(meta, Mapping) and int(meta.get("count", 0)) > 0
            ]
            root["event_years"][f"{year:04d}"] = {
                "path": path,
                "count": sum(int(meta.get("count", 0)) for meta in active_days),
                "earliest_due": None if not active_days else min(str(meta["earliest_due"]) for meta in active_days),
                "latest_due": None if not active_days else max(str(meta["latest_due"]) for meta in active_days),
            }

        # Host shards retain exact event routes, including any future events that
        # were not part of a partial time window.
        initial_host_ids = set(snapshot.initial_hosts)
        final_host_ids = set(registry.hosts)
        changed_host_ids: set[str] = set()
        for host_id in initial_host_ids | final_host_ids:
            before = snapshot.initial_hosts.get(host_id)
            after = registry.hosts[host_id].to_record() if host_id in registry.hosts else None
            if before != after:
                changed_host_ids.add(host_id)
        for event_id in removed_event_ids:
            event = initial_events.get(event_id)
            if event is not None:
                changed_host_ids.add(event.target_host)
        for event in additions.values():
            changed_host_ids.add(event.target_host)

        buckets: Dict[str, list[str]] = {}
        for host_id in sorted(changed_host_ids):
            buckets.setdefault(_host_bucket(host_id), []).append(host_id)
        for bucket, host_ids in sorted(buckets.items()):
            path = f"{HOST_DIR}/{bucket}.json"
            shard = self._host_shard(bucket)
            mapping: MutableMapping[str, Any] = shard["hosts"]
            for host_id in host_ids:
                if host_id not in registry.hosts:
                    mapping.pop(host_id, None)
                    continue
                existing_row = mapping.get(host_id)
                routes = []
                if isinstance(existing_row, Mapping) and isinstance(existing_row.get("event_routes"), list):
                    routes = copy.deepcopy(existing_row["event_routes"])
                by_event = {
                    row.get("event_id"): row
                    for row in routes
                    if isinstance(row, Mapping) and isinstance(row.get("event_id"), str)
                }
                for event_id in removed_event_ids:
                    event = initial_events.get(event_id)
                    if event is not None and event.target_host == host_id:
                        by_event.pop(event_id, None)
                for event_id, event in additions.items():
                    if event.target_host == host_id:
                        by_event[event_id] = _event_route(event)
                # Validate active dedupe identities across all routed events for the host.
                identities: set[tuple[str, str]] = set()
                for route in by_event.values():
                    dedupe = route.get("dedupe_key")
                    event_id = route.get("event_id")
                    identity = ("dedupe", dedupe) if isinstance(dedupe, str) else ("event", event_id)
                    if identity in identities:
                        raise ValueError("duplicate scheduler host event route identity")
                    identities.add(identity)
                ordered_routes = sorted(by_event.values(), key=lambda row: (str(row.get("due_at")), str(row.get("event_id"))))
                mapping[host_id] = {
                    "host": registry.hosts[host_id].to_record(),
                    "event_routes": ordered_routes,
                }
            writes[path] = _json_bytes(shard)
            root["host_buckets"][bucket] = {
                "path": path,
                "count": len(mapping),
            }

        root["world_time"] = str(registry.world_time)
        root["host_count"] = sum(
            int(meta.get("count", 0))
            for meta in root["host_buckets"].values()
            if isinstance(meta, Mapping)
        )
        root["pending_event_count"] = sum(
            int(meta.get("count", 0))
            for meta in root["event_years"].values()
            if isinstance(meta, Mapping)
        )
        due_values = [
            meta.get("earliest_due")
            for meta in root["event_years"].values()
            if isinstance(meta, Mapping) and isinstance(meta.get("earliest_due"), str)
        ]
        root["next_due"] = min(due_values) if due_values else None
        metrics = copy.deepcopy(dict(registry.metrics))
        metrics["host_count"] = root["host_count"]
        metrics["pending_event_count"] = root["pending_event_count"]
        root["metrics"] = metrics
        writes[self.root_path] = _json_bytes(root)
        return writes


def registry_record_to_shards(record: Mapping[str, Any]) -> Dict[str, bytes]:
    """Serialize one logical scheduler registry record into current sharded after-images."""
    registry = CausalSchedulerRegistry.from_record(record)
    host_shards: Dict[str, Dict[str, Any]] = {}
    day_shards: Dict[str, Dict[str, Any]] = {}
    year_indexes: Dict[int, Dict[str, Any]] = {}

    events_by_host: Dict[str, list[ScheduledEvent]] = {}
    for event in registry.queue.snapshot():
        events_by_host.setdefault(event.target_host, []).append(event)
        path = event_day_path(event.due_at)
        day = event_day_key(event.due_at)
        shard = day_shards.setdefault(path, SchedulerStore._blank_event_day(day))
        shard["events"].append(event.to_record())

    host_buckets: Dict[str, Dict[str, Any]] = {}
    for host_id, wrapper in sorted(registry.hosts.items()):
        bucket = _host_bucket(host_id)
        path = f"{HOST_DIR}/{bucket}.json"
        shard = host_shards.setdefault(path, SchedulerStore._blank_host_shard(bucket))
        shard["hosts"][host_id] = {
            "host": wrapper.to_record(),
            "event_routes": [_event_route(event) for event in sorted(events_by_host.get(host_id, []))],
        }

    event_years: Dict[str, Dict[str, Any]] = {}
    for path, shard in sorted(day_shards.items()):
        events = sorted(ScheduledEvent.from_record(item) for item in shard["events"])
        shard["events"] = [event.to_record() for event in events]
        if not events:
            continue
        year = events[0].due_at.year
        day = shard["day"]
        index = year_indexes.setdefault(year, SchedulerStore._blank_year_index(year))
        index["days"][day] = {
            "path": path,
            "count": len(events),
            "earliest_due": str(events[0].due_at),
            "latest_due": str(events[-1].due_at),
        }

    for year, index in sorted(year_indexes.items()):
        metas = list(index["days"].values())
        event_years[f"{year:04d}"] = {
            "path": event_year_index_path(year),
            "count": sum(int(meta["count"]) for meta in metas),
            "earliest_due": min(str(meta["earliest_due"]) for meta in metas),
            "latest_due": max(str(meta["latest_due"]) for meta in metas),
        }

    for path, shard in host_shards.items():
        host_buckets[shard["bucket"]] = {"path": path, "count": len(shard["hosts"])}

    next_due = None
    queued = registry.queue.snapshot()
    if queued:
        next_due = str(queued[0].due_at)
    root = {
        "schema": ROOT_SCHEMA,
        "owner_id": ROOT_OWNER_ID,
        "owner_type": ROOT_OWNER_TYPE,
        "authority": True,
        "storage_version": STORAGE_VERSION,
        "world_time": str(registry.world_time),
        "seeded_at": str(registry.seeded_at),
        "bootstrap_source": registry.bootstrap_source,
        "host_count": len(registry.hosts),
        "pending_event_count": len(queued),
        "next_due": next_due,
        "host_buckets": dict(sorted(host_buckets.items())),
        "event_years": event_years,
        "metrics": {
            **dict(registry.metrics),
            "host_count": len(registry.hosts),
            "pending_event_count": len(queued),
        },
    }
    writes: Dict[str, bytes] = {"state/time/causal-scheduler.json": _json_bytes(root)}
    writes.update({path: _json_bytes(shard) for path, shard in host_shards.items()})
    writes.update({path: _json_bytes(shard) for path, shard in day_shards.items()})
    writes.update({event_year_index_path(year): _json_bytes(index) for year, index in year_indexes.items()})
    return writes
