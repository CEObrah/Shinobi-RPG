#!/usr/bin/env python3
"""Validate that rules advertised as live have current consumers and regressions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtime/contracts/live-rule-consumers.json"
VALID_CLASSIFICATIONS = {"live", "partial", "reference_only", "deferred", "obsolete"}


def fail(message: str) -> None:
    print(f"LIVE RULE CONSUMER FAIL: {message}")
    raise SystemExit(1)


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if data.get("schema") != "live-rule-consumer-registry" or not isinstance(data.get("rules"), list):
        fail("registry_shape")
    seen: set[str] = set()
    live = 0
    for row in data["rules"]:
        if not isinstance(row, dict):
            fail("rule_row_shape")
        rule_path = row.get("rule_path")
        classification = row.get("classification")
        if not isinstance(rule_path, str) or not rule_path or rule_path in seen:
            fail(f"rule_path:{rule_path}")
        seen.add(rule_path)
        if not (ROOT / rule_path).is_file():
            fail(f"rule_missing:{rule_path}")
        if classification not in VALID_CLASSIFICATIONS:
            fail(f"classification:{rule_path}:{classification}")
        consumers = row.get("consumers", [])
        tests = row.get("tests", [])
        if classification == "live":
            live += 1
            if not isinstance(consumers, list) or not consumers:
                fail(f"live_without_consumer:{rule_path}")
            if not isinstance(tests, list) or not tests:
                fail(f"live_without_test:{rule_path}")
        for consumer in consumers:
            if not isinstance(consumer, dict):
                fail(f"consumer_shape:{rule_path}")
            path = consumer.get("path")
            symbol = consumer.get("symbol")
            if not isinstance(path, str) or not isinstance(symbol, str) or not path or not symbol:
                fail(f"consumer_fields:{rule_path}")
            target = ROOT / path
            if not target.is_file():
                fail(f"consumer_missing:{rule_path}:{path}")
            if symbol not in target.read_text(encoding="utf-8"):
                fail(f"consumer_symbol_missing:{rule_path}:{path}:{symbol}")
        for test in tests:
            if not isinstance(test, str) or not test or not (ROOT / test).is_file():
                fail(f"test_missing:{rule_path}:{test}")
    print(f"LIVE RULE CONSUMERS OK: {live} live rules; {len(seen)} classified rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
