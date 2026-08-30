from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if new in text:
        return False
    if text.count(old) != 1:
        raise SystemExit(f"anchor mismatch for {path}: {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


# Add bounded immutable Git provenance reads used only by the repair service.
git_anchor = '''    def head(self) -> str:\n        completed = self._run_bytes(("rev-parse", "HEAD"))\n        if completed.returncode:\n            raise GitStageError(\n                completed.returncode,\n                completed.stderr.decode("utf-8", errors="replace"),\n            )\n        return completed.stdout.decode("ascii").strip()\n'''
git_replacement = git_anchor + '''\n    def is_ancestor(self, ancestor_commit: str, descendant_commit: str) -> bool:\n        completed = self._run_bytes(\n            ("merge-base", "--is-ancestor", ancestor_commit, descendant_commit)\n        )\n        if completed.returncode == 0:\n            return True\n        if completed.returncode == 1:\n            return False\n        raise GitStageError(\n            completed.returncode,\n            completed.stderr.decode("utf-8", errors="replace"),\n        )\n\n    def first_parent(self, commit_hash: str) -> str:\n        completed = self._run_bytes(("rev-list", "--parents", "-n", "1", commit_hash))\n        if completed.returncode:\n            raise GitStageError(\n                completed.returncode,\n                completed.stderr.decode("utf-8", errors="replace"),\n            )\n        parts = completed.stdout.decode("ascii", errors="strict").strip().split()\n        if len(parts) != 2 or parts[0] != commit_hash:\n            raise GitStageError(1, "transaction commit must have exactly one parent")\n        return parts[1]\n\n    def tree_oid(self, commit_hash: str, relative_path: str) -> str:\n        normalized = normalize_relative_path(relative_path)\n        completed = self._run_bytes(("rev-parse", f"{commit_hash}:{normalized}"))\n        if completed.returncode:\n            raise GitStageError(\n                completed.returncode,\n                completed.stderr.decode("utf-8", errors="replace"),\n            )\n        return completed.stdout.decode("ascii", errors="strict").strip()\n\n    def read_path_at(self, commit_hash: str, relative_path: str) -> Optional[bytes]:\n        normalized = normalize_relative_path(relative_path)\n        completed = self._run_bytes(("show", f"{commit_hash}:{normalized}"))\n        if completed.returncode:\n            return None\n        return completed.stdout\n'''
replace_once("runtime/shinobi_runtime/tx/git.py", git_anchor, git_replacement)

# Register the dedicated repair service and two explicit MCP tools.
replace_once(
    "runtime/shinobi_runtime/api/mcp.py",
    "from shinobi_runtime.api.operations import CampaignOperations, OperationError\n",
    "from shinobi_runtime.api.operations import CampaignOperations, OperationError\nfrom shinobi_runtime.api.repair import CampaignRepairService, REPAIR_COMMAND_TYPE, REPAIR_MODE\n",
)

mcp_service_anchor = '''    write_security_meta = {\n        "securitySchemes": [\n            {\n                "type": "oauth2",\n                "scopes": [oauth.read_scope, oauth.write_scope],\n            }\n        ]\n    }\n\n    read_annotations = ToolAnnotations(\n'''
mcp_service_replacement = mcp_service_anchor.replace(
    "\n    read_annotations",
    "\n    repair_service = CampaignRepairService(operations)\n\n    read_annotations",
)
replace_once("runtime/shinobi_runtime/api/mcp.py", mcp_service_anchor, mcp_service_replacement)

mcp_tools_anchor = '''    @server.tool(\n        name="ooc_audit",\n'''
mcp_tools = '''    @server.tool(\n        name="preview_campaign_repair",\n        title="Preview one forward campaign repair",\n        description=(\n            "Privileged OOC DEV read-only preview for repairing exactly one already-committed "\n            "damaged transaction. The restore snapshot is derived internally from immutable "\n            "transaction Git provenance; callers cannot choose repository paths or restore commits."\n        ),\n        annotations=read_annotations,\n        meta=read_security_meta,\n        structured_output=True,\n    )\n    def preview_campaign_repair(\n        request_id: str,\n        expected_revision: int,\n        damaged_transaction_id: str,\n    ) -> PreviewToolOutput:\n        if (\n            not isinstance(request_id, str)\n            or len(request_id) > 128\n            or not _SAFE_ID.fullmatch(request_id)\n            or isinstance(expected_revision, bool)\n            or not isinstance(expected_revision, int)\n            or expected_revision < 1\n            or not isinstance(damaged_transaction_id, str)\n            or len(damaged_transaction_id) > 160\n            or not _SAFE_ID.fullmatch(damaged_transaction_id)\n        ):\n            return _failure(OperationError(422, "repair_preview_input_invalid"))\n        try:\n            campaign = _command_identity(operations)\n            if expected_revision != campaign["revision"]:\n                raise OperationError(409, "stale_revision")\n            command = CommandEnvelope(\n                campaign_id=campaign["campaign_id"],\n                request_id=request_id,\n                actor_id=campaign["player_id"],\n                command_type=REPAIR_COMMAND_TYPE,\n                expected_revision=expected_revision,\n                submitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),\n                payload={"damaged_transaction_id": damaged_transaction_id},\n                mode=REPAIR_MODE,\n            )\n            preview = repair_service.preview(command)\n        except OperationError as exc:\n            return _failure(exc)\n        except (TypeError, ValueError):\n            return _failure(OperationError(422, "repair_preview_input_invalid"))\n        except Exception:\n            return _internal_failure("preview_campaign_repair")\n        return _success(\n            preview=preview,\n            command=command.to_record(),\n            preview_attestation=_preview_attestation(command, oauth),\n        )\n\n    @server.tool(\n        name="execute_campaign_repair",\n        title="Execute an exact previewed campaign repair",\n        description=(\n            "Privileged OOC DEV write tool. Requires the exact repair command and short-lived "\n            "attestation returned by preview_campaign_repair. The repair is forward-only, "\n            "revision-advancing, and preserves the damaged commit as immutable evidence."\n        ),\n        annotations=ToolAnnotations(\n            readOnlyHint=False,\n            destructiveHint=True,\n            idempotentHint=True,\n            openWorldHint=False,\n        ),\n        meta=write_security_meta,\n        structured_output=True,\n    )\n    def execute_campaign_repair(\n        command: dict[str, Any],\n        preview_attestation: Optional[str] = None,\n    ) -> ExecuteToolOutput:\n        scope_failure = _require_write_scope(oauth.write_scope)\n        if scope_failure is not None:\n            return _scope_challenge(oauth)  # type: ignore[return-value]\n        try:\n            validate_bounded_json(command, label="repair command envelope")\n            envelope = CommandEnvelope.from_record(command)\n            if (\n                envelope.to_record() != command\n                or envelope.mode != REPAIR_MODE\n                or envelope.command_type != REPAIR_COMMAND_TYPE\n            ):\n                raise ValueError("command is not an exact canonical repair record")\n            existing = repair_service.lookup_receipt(envelope)\n            if existing is not None:\n                return _success(receipt=existing)\n            if not _verify_preview_attestation(\n                envelope,\n                preview_attestation,\n                oauth,\n            ):\n                raise OperationError(409, "preview_attestation_invalid_or_expired")\n            receipt = repair_service.execute(envelope)\n        except OperationError as exc:\n            return _failure(exc)\n        except (TypeError, ValueError):\n            return _failure(OperationError(422, "invalid_repair_command_envelope"))\n        except Exception:\n            return _internal_failure("execute_campaign_repair")\n        return _success(receipt=receipt)\n\n'''
replace_once("runtime/shinobi_runtime/api/mcp.py", mcp_tools_anchor, mcp_tools + mcp_tools_anchor)

# Repair-base validation must permit exact receipt lookup after the repair has
# already advanced the live revision, while new execution still requires the
# exact current base revision.
repair_path = ROOT / "runtime/shinobi_runtime/api/repair.py"
repair_text = repair_path.read_text(encoding="utf-8")
repair_text = repair_text.replace(
    "    def _require_base(self, command: CommandEnvelope) -> str:\n",
    "    def _require_base(self, command: CommandEnvelope, *, require_revision: bool = True) -> str:\n",
)
repair_text = repair_text.replace(
    '''        try:\n            self.repository.require_campaign(command.campaign_id, _META_PATH)\n            self.repository.require_revision(command.expected_revision, _META_PATH)\n        except ValueError as exc:\n            if "revision" in str(exc).lower():\n                raise OperationError(409, "stale_revision") from exc\n            raise OperationError(409, "repair_campaign_mismatch") from exc\n''',
    '''        try:\n            self.repository.require_campaign(command.campaign_id, _META_PATH)\n            if require_revision:\n                self.repository.require_revision(command.expected_revision, _META_PATH)\n        except StaleRevisionError as exc:\n            raise OperationError(409, "stale_revision") from exc\n        except (TypeError, ValueError) as exc:\n            raise OperationError(409, "repair_campaign_mismatch") from exc\n''',
)
repair_text = repair_text.replace(
    "        self._require_base(command)\n        try:\n            existing = self.coordinator.lookup_receipt(command)\n",
    "        self._require_base(command, require_revision=False)\n        try:\n            existing = self.coordinator.lookup_receipt(command)\n",
    1,
)
repair_text = repair_text.replace(
    '''    def execute(self, command: CommandEnvelope) -> Mapping[str, Any]:\n        self._require_base(command)\n        self._require_fresh_deployment()\n        try:\n            existing = self.coordinator.lookup_receipt(command)\n''',
    '''    def execute(self, command: CommandEnvelope) -> Mapping[str, Any]:\n        self._require_base(command, require_revision=False)\n        try:\n            existing = self.coordinator.lookup_receipt(command)\n            if existing is not None:\n                return self._receipt_response("duplicate", existing)\n            self._require_fresh_deployment()\n''',
)
# Remove the duplicated idempotency return left by the preceding structural replacement.
repair_text = repair_text.replace(
    '''            self._require_fresh_deployment()\n            if existing is not None:\n                return self._receipt_response("duplicate", existing)\n            with self.operations._locked():\n''',
    '''            self._require_fresh_deployment()\n            with self.operations._locked():\n''',
)
repair_path.write_text(repair_text, encoding="utf-8")
