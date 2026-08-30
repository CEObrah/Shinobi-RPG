from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"anchor mismatch for {path}: {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


preview_old = '''            command = CommandEnvelope(\n                campaign_id=campaign["campaign_id"],\n                request_id=request_id,\n                actor_id=campaign["player_id"],\n                command_type=command_type,\n                expected_revision=expected_revision,\n                submitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),\n                payload=payload,\n                mode="gameplay",\n            )\n            preview = operations.preview_command(command)\n'''
preview_new = '''            is_repair = command_type == REPAIR_COMMAND_TYPE\n            command = CommandEnvelope(\n                campaign_id=campaign["campaign_id"],\n                request_id=request_id,\n                actor_id=campaign["player_id"],\n                command_type=command_type,\n                expected_revision=expected_revision,\n                submitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),\n                payload=payload,\n                mode=REPAIR_MODE if is_repair else "gameplay",\n            )\n            preview = (\n                repair_service.preview(command)\n                if is_repair\n                else operations.preview_command(command)\n            )\n'''
replace_once("runtime/shinobi_runtime/api/mcp.py", preview_old, preview_new)

execute_old = '''            if envelope.to_record() != command:\n                raise ValueError("command is not its canonical complete record")\n            existing = operations.lookup_command_receipt(envelope)\n            if existing is not None:\n                return _success(receipt=existing)\n            if not _verify_preview_attestation(\n                envelope,\n                preview_attestation,\n                oauth,\n            ):\n                raise OperationError(\n                    409,\n                    "preview_attestation_invalid_or_expired",\n                )\n            receipt = operations.execute_command(envelope)\n'''
execute_new = '''            if envelope.to_record() != command:\n                raise ValueError("command is not its canonical complete record")\n            is_repair = (\n                envelope.mode == REPAIR_MODE\n                and envelope.command_type == REPAIR_COMMAND_TYPE\n            )\n            if envelope.mode == REPAIR_MODE and not is_repair:\n                raise ValueError("unsupported repair command")\n            existing = (\n                repair_service.lookup_receipt(envelope)\n                if is_repair\n                else operations.lookup_command_receipt(envelope)\n            )\n            if existing is not None:\n                return _success(receipt=existing)\n            if not _verify_preview_attestation(\n                envelope,\n                preview_attestation,\n                oauth,\n            ):\n                raise OperationError(\n                    409,\n                    "preview_attestation_invalid_or_expired",\n                )\n            receipt = (\n                repair_service.execute(envelope)\n                if is_repair\n                else operations.execute_command(envelope)\n            )\n'''
replace_once("runtime/shinobi_runtime/api/mcp.py", execute_old, execute_new)
