"""Shape C security boundary: sanitized handoff, read-only investigators, no leaks in traces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import BY_ID, CLAIMS, WORLD
from lab8.db import connect
from lab8.durability import get_refund
from lab8.handoff import (
    ALLOWED_FIELDS,
    FACT_KEYS,
    READONLY_TOOLS,
    WRITE_TOOLS,
    HandoffError,
    make_handoff,
    sanitize,
)
from lab8.runtime import ToolRejected, dispatch, run_readonly_investigators
from lab8.schemas import Budget
from lab8.shapes import orchestrator

PII_KEYS = ("phone", "address", "internal_note", "item_name")


def _events(trace_path: str) -> list[dict]:
    return json.loads(Path(trace_path).read_text(encoding="utf-8"))["events"]


def _forbidden(claim_id: str) -> list[str]:
    tokens = ["CANARY_SECRET_", "parent_secret", "raw_headers", "Bearer "]
    tokens += BY_ID[claim_id]["expected"]["forbid_substrings"]
    for order in WORLD["orders"]:
        tokens += [str(order[key]) for key in PII_KEYS]
    return tokens


@pytest.mark.parametrize("claim_id", [row["claim_id"] for row in CLAIMS])
def test_orchestrator_trace_has_only_sanitized_handoff_and_no_leaks(public_db, tmp_path, monkeypatch, claim_id):
    monkeypatch.chdir(tmp_path)
    result = orchestrator.run_claim(claim_id, seed=11, store_path=str(public_db))

    blob = Path(result.trace_path).read_text(encoding="utf-8")
    leaked = [token for token in _forbidden(claim_id) if token in blob]
    assert leaked == [], f"{claim_id} trace leaked {leaked}"

    for event in _events(result.trace_path):
        if event["kind"] != "handoff":
            continue
        handoff = event["payload"]
        assert set(handoff) <= ALLOWED_FIELDS
        assert set(handoff["facts"]) <= FACT_KEYS
        assert set(handoff["allowed_tools"]) <= READONLY_TOOLS
        assert not set(handoff["allowed_tools"]) & WRITE_TOOLS


def test_sanitize_drops_fields_outside_allowlist():
    cleaned = sanitize(
        {
            "contract_version": "handoff.v1",
            "request_id": "REQ-1",
            "claim_id": "C-001",
            "principal_scope": "owner_order_only",
            "task": "t",
            "facts": {},
            "allowed_tools": ["lookup_order"],
            "max_output_tokens": 400,
            "ticket": "Đơn O-107 giao trễ 4 ngày",
            "order_row": {"phone": "0901000107"},
        }
    )
    assert set(cleaned) <= ALLOWED_FIELDS
    assert "ticket" not in cleaned and "order_row" not in cleaned


def test_make_handoff_drops_unknown_facts_and_refuses_canary_in_allowed_fact():
    handoff = make_handoff(
        request_id="REQ-1",
        claim_id="C-001",
        task="t",
        facts={"delay_days": 4, "phone": "0901000107", "parent_secret": "CANARY_SECRET_PARENT_C-001"},
        allowed_tools=["lookup_order"],
    )
    assert handoff["facts"] == {"delay_days": 4}

    with pytest.raises(HandoffError):
        make_handoff(
            request_id="REQ-1",
            claim_id="C-001",
            task="t",
            facts={"carrier": "CANARY_SECRET_PARENT_C-001"},
            allowed_tools=["lookup_order"],
        )


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_make_handoff_rejects_write_tools(tool):
    with pytest.raises(HandoffError):
        make_handoff(request_id="REQ-1", claim_id="C-001", task="t", facts={}, allowed_tools=["lookup_order", tool])


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_investigator_cannot_call_write_tools(public_db, tool):
    conn = connect(public_db)
    try:
        with pytest.raises(ToolRejected):
            dispatch(conn, Budget(), tool, {"claim_id": "C-001"}, worker=True)

        # Even a handoff tampered with after sanitize cannot make an investigator write.
        tampered = make_handoff(
            request_id="REQ-C-001", claim_id="C-001", task="t", facts={}, allowed_tools=["lookup_order"]
        )
        tampered["allowed_tools"].append(tool)
        with pytest.raises(ToolRejected):
            run_readonly_investigators(conn, Budget(), tampered, principal_id="P-100")
        assert get_refund(conn, "C-001") is None
    finally:
        conn.close()
