from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{rel}: expected one match, found {count}")
    write(rel, text.replace(old, new, 1))


# Add bounded read-only Git object discovery to the existing transaction adapter.
replace_once(
    "runtime/shinobi_runtime/tx/git.py",
    '''    def head(self) -> str:
        completed = self._run_bytes(("rev-parse", "HEAD"))
        if completed.returncode:
            raise GitStageError(
                completed.returncode,
                completed.stderr.decode("utf-8", errors="replace"),
            )
        return completed.stdout.decode("ascii").strip()

    def is_ancestor''',
    '''    def head(self) -> str:
        completed = self._run_bytes(("rev-parse", "HEAD"))
        if completed.returncode:
            raise GitStageError(
                completed.returncode,
                completed.stderr.decode("utf-8", errors="replace"),
            )
        return completed.stdout.decode("ascii").strip()

    def root_commits(self) -> Tuple[str, ...]:
        """Return reachable root commits for bounded provenance diagnostics."""
        completed = self._run_bytes(("rev-list", "--max-parents=0", "HEAD"))
        if completed.returncode:
            raise GitStageError(
                completed.returncode,
                completed.stderr.decode("utf-8", errors="replace"),
            )
        return tuple(
            commit
            for commit in completed.stdout.decode("ascii", errors="strict").splitlines()
            if commit
        )

    def unreachable_commits(self, max_count: int = 512) -> Tuple[str, ...]:
        """Return bounded local unreachable commit objects without changing refs."""
        if isinstance(max_count, bool) or not isinstance(max_count, int) or max_count <= 0:
            raise ValueError("max_count must be a positive integer")
        completed = self._run_bytes(("fsck", "--unreachable", "--no-reflogs", "--no-progress"))
        if completed.returncode:
            raise GitStageError(
                completed.returncode,
                completed.stderr.decode("utf-8", errors="replace"),
            )
        commits = []
        for raw_line in completed.stdout.decode("ascii", errors="strict").splitlines():
            parts = raw_line.split()
            if len(parts) == 3 and parts[0] in {"unreachable", "dangling"} and parts[1] == "commit":
                commits.append(parts[2])
                if len(commits) >= max_count:
                    break
        return tuple(commits)

    def is_ancestor''',
)

# Extend the OOC audit with a very narrow legacy-lineage probe. A candidate is
# accepted as the severed canonical anchor only if its exact state tree equals
# the reachable release-root state tree and its Shinobi campaign/revision
# trailers agree. That lets us inspect the old first-parent lineage without
# treating arbitrary dangling commits as authority.
ooc_path = "runtime/shinobi_runtime/api/ooc.py"
ooc = read(ooc_path)
ooc = ooc.replace(
    "from shinobi_runtime.tx import WriteAheadLog\nfrom shinobi_runtime.tx.errors import WalError\n",
    "from shinobi_runtime.tx import WriteAheadLog\nfrom shinobi_runtime.tx.errors import GitStageError, WalError\nfrom shinobi_runtime.tx.git import CAMPAIGN_TRAILER, REVISION_TRAILER, GitStager\n",
    1,
)
insert_before = '''class RepositoryOocAudit:\n'''
helpers = r'''

def _git_json(git: GitStager, commit: str, path: str) -> Mapping[str, Any] | None:
    raw = git.read_path_at(commit, path)
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _combat_elapsed_from_git(git: GitStager, commit: str) -> int | None:
    state = _git_json(git, commit, "state/martial-world/combats.json")
    combats = state.get("combats", {}) if state is not None else {}
    if not isinstance(combats, Mapping):
        return None
    active = [row for row in combats.values() if isinstance(row, Mapping) and row.get("status") == "active"]
    if len(active) != 1:
        return None
    value = active[0].get("elapsed_ms")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _wei_fatigue_from_git(git: GitStager, commit: str, player_id: str) -> int | None:
    state = _git_json(git, commit, "state/martial-world/people/house_tang.json")
    rows = state.get("people", []) if state is not None else []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, Mapping) and row.get("person_id") == player_id:
            value = row.get("fatigue_milli")
            return int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    return None

'''
if ooc.count(insert_before) != 1:
    raise RuntimeError("OOC class insertion point not unique")
ooc = ooc.replace(insert_before, helpers + insert_before, 1)
method_needle = '''    def _wal_combat_diagnostics(self, campaign_id: str) -> list[str]:\n'''
legacy_method = r'''    def _legacy_lineage_diagnostics(self, campaign_id: str, player_id: str) -> list[str]:
        """Inspect a severed local first-parent lineage without promoting arbitrary dangling Git."""
        try:
            git = GitStager(self.repository.root)
            roots = git.root_commits()
            if len(roots) != 1:
                return [f"legacy_lineage:unavailable reason=reachable_root_count count={len(roots)}"]
            release_root = roots[0]
            root_meta = _git_json(git, release_root, "state/meta.json")
            if root_meta is None or root_meta.get("campaign_id") != campaign_id:
                return ["legacy_lineage:unavailable reason=release_root_meta_invalid"]
            root_revision = root_meta.get("revision")
            if isinstance(root_revision, bool) or not isinstance(root_revision, int):
                return ["legacy_lineage:unavailable reason=release_root_revision_invalid"]
            root_state_tree = git.tree_oid(release_root, "state")
            anchors = []
            for candidate in git.unreachable_commits(max_count=512):
                meta = _git_json(git, candidate, "state/meta.json")
                if meta is None or meta.get("campaign_id") != campaign_id or meta.get("revision") != root_revision:
                    continue
                try:
                    record = git.get_commit(candidate)
                    if (
                        record.trailers.get(CAMPAIGN_TRAILER) != campaign_id
                        or record.trailers.get(REVISION_TRAILER) != str(root_revision)
                        or git.tree_oid(candidate, "state") != root_state_tree
                    ):
                        continue
                except GitStageError:
                    continue
                anchors.append(candidate)
            if not anchors:
                return [f"legacy_lineage:none release_root_revision={root_revision}"]
            anchor = sorted(anchors)[0]
            diagnostics = [
                f"legacy_lineage_anchor:release_root={release_root} revision={root_revision} severed_commit={anchor} state_tree_match=true"
            ]
            cursor = anchor
            seen_revisions: set[int] = set()
            for _index in range(128):
                meta = _git_json(git, cursor, "state/meta.json")
                if meta is not None and meta.get("campaign_id") == campaign_id:
                    revision = meta.get("revision")
                    if isinstance(revision, int) and not isinstance(revision, bool) and revision not in seen_revisions:
                        seen_revisions.add(revision)
                        diagnostics.append(
                            "legacy_world:"
                            f"rev={revision} commit={cursor} time={meta.get('time')} "
                            f"combat_elapsed_ms={_combat_elapsed_from_git(git, cursor)} "
                            f"player_fatigue_milli={_wei_fatigue_from_git(git, cursor, player_id)}"
                        )
                        if len(diagnostics) >= 33:
                            break
                try:
                    cursor = git.first_parent(cursor)
                except GitStageError:
                    break
            return diagnostics
        except (OSError, GitStageError, ValueError):
            return ["legacy_lineage:unavailable reason=git_object_probe_failed"]

'''
if ooc.count(method_needle) != 1:
    raise RuntimeError("OOC WAL method insertion point not unique")
ooc = ooc.replace(method_needle, legacy_method + method_needle, 1)
trigger_old = '''        if meta is not None and any(token in normalized_focus for token in ('wal provenance','repair provenance','combat provenance')):
            diagnostics.extend(self._wal_combat_diagnostics(str(meta.get('campaign_id') or '')))
'''
trigger_new = '''        if meta is not None and any(token in normalized_focus for token in ('wal provenance','repair provenance','combat provenance')):
            diagnostics.extend(self._wal_combat_diagnostics(str(meta.get('campaign_id') or '')))
        if meta is not None and any(token in normalized_focus for token in ('legacy lineage','pre-root','severed lineage')):
            diagnostics.extend(self._legacy_lineage_diagnostics(str(meta.get('campaign_id') or ''), str(meta.get('player_id') or '')))
'''
if ooc.count(trigger_old) != 1:
    raise RuntimeError("OOC focus trigger not unique")
ooc = ooc.replace(trigger_old, trigger_new, 1)
write(ooc_path, ooc)

# Add regressions for bounded Git discovery and exact-state-tree anchor rules.
test_path = "tests/current/test_ooc_wal_combat_provenance.py"
replace_once(
    test_path,
    '            return {"campaign_id": "c", "game": "jianghu", "revision": 143, "time": "T1"}',
    '            return {"campaign_id": "c", "game": "jianghu", "revision": 143, "time": "T1", "player_id": "pc.test"}',
)
with (ROOT / test_path).open("a", encoding="utf-8") as handle:
    handle.write(r'''


class _LegacyGit:
    def __init__(self, _root):
        pass

    def root_commits(self):
        return ("root143",)

    def unreachable_commits(self, max_count=512):
        return ("noise", "old143")

    def read_path_at(self, commit, path):
        meta = {
            "root143": {"campaign_id": "c", "revision": 143, "time": "T143", "player_id": "pc.test"},
            "noise": {"campaign_id": "c", "revision": 143, "time": "TN", "player_id": "pc.test"},
            "old143": {"campaign_id": "c", "revision": 143, "time": "T143", "player_id": "pc.test"},
            "old142": {"campaign_id": "c", "revision": 142, "time": "T142", "player_id": "pc.test"},
            "old141": {"campaign_id": "c", "revision": 141, "time": "T141", "player_id": "pc.test"},
        }
        if path == "state/meta.json" and commit in meta:
            return (json.dumps(meta[commit]) + "\n").encode()
        if path == "state/martial-world/combats.json" and commit in {"old143", "old142", "old141"}:
            elapsed = {"old143": 6_212_079, "old142": 12_000, "old141": 0}[commit]
            return (json.dumps({"combats": {"combat:test": {"status": "active", "elapsed_ms": elapsed}}}) + "\n").encode()
        if path == "state/martial-world/people/house_tang.json" and commit in {"old143", "old142", "old141"}:
            fatigue = {"old143": 3265, "old142": 120, "old141": 0}[commit]
            return (json.dumps({"people": [{"person_id": "pc.test", "fatigue_milli": fatigue}]}) + "\n").encode()
        return None

    def tree_oid(self, commit, path):
        assert path == "state"
        return {"root143": "state-same", "old143": "state-same", "noise": "state-other"}.get(commit, "older-state")

    def get_commit(self, commit):
        trailers = {
            "old143": {"Shinobi-Campaign": "c", "Shinobi-World-Revision": "143"},
            "noise": {"Shinobi-Campaign": "c", "Shinobi-World-Revision": "143"},
        }.get(commit, {})
        return type("Record", (), {"trailers": trailers})()

    def first_parent(self, commit):
        mapping = {"old143": "old142", "old142": "old141"}
        if commit not in mapping:
            from shinobi_runtime.tx.errors import GitStageError
            raise GitStageError(1, "no parent")
        return mapping[commit]


def test_legacy_lineage_audit_requires_exact_release_root_state_tree_match(monkeypatch, tmp_path):
    monkeypatch.setattr(ooc, "GitStager", _LegacyGit)
    monkeypatch.setattr(ooc, "_derived_person_routes", lambda _repo: {})
    monkeypatch.setattr(ooc, "civilian_population_total", lambda _value: 0)
    monkeypatch.setattr(ooc, "inspect_deployment_freshness", lambda _root: type("D", (), {"healthy": True, "diagnostic": lambda self: "deployment:ok"})())
    result = ooc.RepositoryOocAudit(_Repo(), tmp_path)("legacy lineage pre-root", ())
    joined = "\n".join(result.diagnostics)
    assert "legacy_lineage_anchor:" in joined
    assert "severed_commit=old143" in joined
    assert "state_tree_match=true" in joined
    assert "legacy_world:rev=142 commit=old142" in joined
    assert "combat_elapsed_ms=12000" in joined
    assert "player_fatigue_milli=120" in joined
    assert "severed_commit=noise" not in joined
''')

print("legacy lineage audit patch applied")
