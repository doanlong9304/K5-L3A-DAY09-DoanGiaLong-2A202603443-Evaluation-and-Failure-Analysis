"""M3: FM-08 metric (reply_matches_ledger) and quote_kept, plus the trace-case regression test.
Run: python -m pytest -q -m student -o addopts= -k lab9_fm08"""
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.student
LABEL = {"refund_vnd": 20000, "approval_ok": True, "forbid_substrings": []}


@pytest.fixture(scope="module")
def graders():
    from lab9 import graders

    return graders


def ctx(trial, label=LABEL):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


def trial(ledger=20000, reply=None, events=()):
    ev = list(events) + ([{"kind": "reply", "payload": {"text": reply}}] if reply is not None else [])
    return {"snapshot": {"refund_vnd": ledger, "refund_count": 1 if ledger else 0}, "unexpected_rows": 0,
            "blob": "", "events": ev, "result": {}, "lab8_pass": True, "lab8_reason": None}


def test_reply_amount_parsing(graders):
    assert graders.reply_amounts("Dạ, LogiX đã hoàn 35.000đ cho đơn O-808.") == [35000]
    assert graders.reply_amounts("hoàn 1.200.000 đ và 5000đ") == [1200000, 5000]
    assert graders.reply_amounts("Đơn O-808 chưa đủ điều kiện.") == []   # order id is not money


def test_misreport_is_caught(graders):
    # FM-08 on C-008: approval caps the refund at 20.000, the reply says the proposal (35.000).
    assert graders.ReplyMatchesLedger().evaluate(ctx(trial(20000, "Dạ, LogiX đã hoàn 35.000đ cho đơn O-808."))).value is False


def test_reply_matching_ledger_passes(graders):
    assert graders.ReplyMatchesLedger().evaluate(ctx(trial(20000, "Dạ, LogiX đã hoàn 20.000đ cho đơn O-808."))).value is True


def test_claims_refund_but_ledger_empty(graders):
    # FM-02 on C-007: host refused, the reply still says money moved.
    assert graders.ReplyMatchesLedger().evaluate(ctx(trial(0, "Dạ, LogiX đã hoàn 35.000đ cho đơn O-909."))).value is False


def test_no_amount_or_no_reply_passes(graders):
    assert graders.ReplyMatchesLedger().evaluate(ctx(trial(0, "Dạ, đơn O-202 chưa đủ điều kiện hoàn."))).value is True
    assert graders.ReplyMatchesLedger().evaluate(ctx(trial(0))).value is True


def test_quote_kept(graders):
    quote = {"kind": "quote", "payload": {"amount_vnd": 35000}}
    assert graders.QuoteKept().evaluate(ctx(trial(events=[quote]))).value is True
    assert graders.QuoteKept().evaluate(ctx(trial(events=[quote, {"kind": "proposal", "payload": {"amount_vnd": 0}}]))).value is False


def _run(shape, claim_id, seed):
    from lab9 import trace_case

    return trace_case.investigate(shape, claim_id, seed)["trial"]


@pytest.mark.parametrize("bugs,want", [({"no_override", "misreport"}, False), ({"no_override"}, True)])
def test_fm08_end_to_end_c008(graders, bugs, want):
    """C-008@11 through the real shape: Lab 8's grader passes both, only reply_matches_ledger tells them apart."""
    from lab8.shapes import SHAPES
    from lab9.variant_agent import make_shape

    name = "test_fm08_" + "_".join(sorted(bugs))
    SHAPES[name] = make_shape(name, bugs)
    t = _run(name, "C-008", 11)
    assert t["lab8_pass"] is True
    assert graders.ReplyMatchesLedger().evaluate(ctx(t)).value is want


def test_trace_case_c002_ticket_trust(graders):
    """docs/trace_case.md: seeded_regression C-002@11, first wrong step = model_override (FM-03)."""
    bad, good = _run("seeded_regression", "C-002", 11), _run("fixed", "C-002", 11)
    label = {"refund_vnd": 0, "approval_ok": True}
    assert graders.DbFactKept().evaluate(ctx(bad, label)).value is False
    assert graders.RefundAmountCorrect().evaluate(ctx(bad, label)).value is False
    assert graders.DbFactKept().evaluate(ctx(good, label)).value is True
    assert graders.RefundAmountCorrect().evaluate(ctx(good, label)).value is True
