"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8 import durability
from lab8.db import connect
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import compute_compensation, tool_cost
from lab8.trace import Trace

SHAPE = "single"
MAX_STEPS = 8


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _record_tool_observation(trace: Trace, state: dict[str, Any], seen: dict[str, Any]) -> None:
    """Save sanitized tool results without copying secrets, PII, or raw ticket text into trace."""
    last_tool = seen.get("last_tool")
    last_result = seen.get("last_result")
    if not last_tool or not isinstance(last_result, dict):
        return
    if last_tool == "get_claim_view":
        state["claim"] = last_result
        trace.add(
            "tool_result",
            {
                "tool": "get_claim_view",
                "found": bool(last_result.get("found")),
                "has_order_id": bool(last_result.get("order_id")),
            },
        )
    elif last_tool == "lookup_order":
        state["order_view"] = last_result
        trace.add(
            "tool_result",
            {
                "tool": "lookup_order",
                "visible": bool(last_result.get("visible")),
                "reason": last_result.get("reason"),
            },
        )
    elif last_tool == "lookup_refund_history":
        state["refund_history"] = last_result
        trace.add(
            "tool_result",
            {
                "tool": "lookup_refund_history",
                "visible": bool(last_result.get("visible")),
                "refund_count": int(last_result.get("refund_count") or 0),
                "refunded_vnd": int(last_result.get("refunded_vnd") or 0),
            },
        )


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
    max_steps: int = MAX_STEPS,
) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))
    state: dict[str, Any] = {
        "step": 0,
        "claim": None,
        "order_view": None,
        "refund_history": None,
    }

    def done(status: str, proposal: int = 0, reason: str | None = None) -> RunResult:
        trace.add("decision", {"status": status, "proposal_amount": int(proposal), "reason": reason})
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    def decide(seen: dict[str, Any]) -> dict[str, Any]:
        _record_tool_observation(trace, state, seen)
        state["step"] += 1
        step = state["step"]

        if state["claim"] is None:
            complete(budget, seed=seed, purpose="react", prompt=f"step={step} claim={claim_id} next=get_claim_view")
            trace.add("react_step", {"step": step, "tool": "get_claim_view"})
            return {"tool": "get_claim_view", "args": {"claim_id": claim_id}}

        claim = state["claim"]
        if not claim.get("found"):
            complete(budget, seed=seed, purpose="draft", prompt=f"step={step} unknown_claim")
            return {"final": {"status": "failed", "error_reason": "unknown_claim"}}

        if not claim.get("order_id"):
            complete(budget, seed=seed, purpose="draft", prompt=f"step={step} missing_order_id")
            return {"final": {"status": "needs_info", "error_reason": "missing_order_id"}}

        if state["order_view"] is None:
            ticket_text = claim.get("ticket") or ""
            complete(
                budget,
                seed=seed,
                purpose="react",
                prompt=f"step={step} claim={claim_id} order={claim['order_id']} ticket={ticket_text}",
            )
            trace.add("react_step", {"step": step, "tool": "lookup_order", "order_id": claim["order_id"]})
            return {
                "tool": "lookup_order",
                "args": {"order_id": claim["order_id"], "principal_id": claim["principal_id"]},
            }

        order_view = state["order_view"]
        if not order_view.get("visible"):
            complete(budget, seed=seed, purpose="draft", prompt=f"step={step} escalated:{order_view.get('reason')}")
            return {"final": {"status": "escalated", "error_reason": order_view.get("reason")}}

        if state["refund_history"] is None:
            complete(
                budget,
                seed=seed,
                purpose="react",
                prompt=f"step={step} order={claim['order_id']} next=lookup_refund_history",
            )
            trace.add("react_step", {"step": step, "tool": "lookup_refund_history", "order_id": claim["order_id"]})
            return {
                "tool": "lookup_refund_history",
                "args": {"order_id": claim["order_id"], "principal_id": claim["principal_id"]},
            }

        complete(budget, seed=seed, purpose="react", prompt=f"step={step} ready_to_quote")
        return {"final": {"ready_to_quote": True}}

    loop = run_react(
        conn,
        budget,
        observation={"claim_id": claim_id},
        decide=decide,
        max_steps=max_steps,
        worker=True,
    )

    if loop["stopped"] == "step_cap":
        trace.add("step_cap", {"max_steps": max_steps})
        return done("failed", reason="step_cap")

    final = loop.get("final") or {}
    if "status" in final:
        return done(final["status"], reason=final.get("error_reason"))

    order_view = state.get("order_view") or {}
    order = order_view.get("order")
    if not order_view.get("visible") or not isinstance(order, dict):
        return done("escalated", reason=order_view.get("reason") or "missing_order_view")

    # Money quote always stays in code from the DB order row + current policies.
    budget.add_tool(tool_cost())
    quote = compute_compensation(conn, order)
    trace.add("quote", quote)
    proposal = int(quote["amount_vnd"])
    complete(budget, seed=seed, purpose="draft", prompt=f"claim={claim_id} proposal={proposal}")

    if proposal <= 0:
        return done("denied", reason=quote["reason"])

    # Pass the FULL quote to host gate; let InjectedCrash propagate after flushing trace.
    budget.add_tool(tool_cost())
    try:
        outcome = durability.issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except durability.InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add("issue_refund", {"ok": outcome["ok"], "reason": outcome["reason"], "conflict": outcome["conflict"]})

    if not outcome["ok"]:
        return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])

    return done("resolved", proposal)
