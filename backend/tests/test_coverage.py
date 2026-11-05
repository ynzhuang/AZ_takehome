"""Dropped-condition guard (cold-review regression): a recognisable condition in the question must be expressed in
the parameters, otherwise the answer is a clarification or a decline, never a broader answer. No LLM involved: the
interpreter is replaced by a fixed tool call, including the exact wrong calls the real model made."""

from __future__ import annotations

import pytest

from app.coverage import missed_conditions
from app.schemas import ApprovalTierParams, ExceptionsParams, SpendParams

from .test_api import ask, client
from .conftest import FakeInterpreter

BIOREAGENT = "How much did we spend with BioReagent Direct?"
OVER_50K = "Show me invoices over EUR 50,000 without a PO"


# ----------------------------------------------------------------------------- the two failed questions


def test_vendor_name_dropped_is_clarified_not_answered():
    with client(FakeInterpreter("spend", {})) as c:  # what the model did: total spend, vendor dropped
        code, r = ask(c, BIOREAGENT)
    assert (code, r["status"]) == (200, "clarification_needed")
    assert r["result"] is None and r["evidence"]["row_count"] == 0
    assert "BioReagent Direct (V-1008)" in r["limitations"][0]


def test_vendor_name_applied_is_answered():
    with client(FakeInterpreter("spend", {"vendor_ids": ["V-1008"]})) as c:
        code, r = ask(c, BIOREAGENT)
    assert (code, r["status"]) == (200, "answered")
    assert r["result"]["total_eur"] == 1131552.52 and r["result"]["invoice_count"] == 19


def test_amount_threshold_is_declined_not_answered():
    with client(FakeInterpreter("exceptions", {"check": "missing_po"})) as c:  # what the model did: all 21
        code, r = ask(c, OVER_50K)
    assert (code, r["status"]) == (200, "declined")
    assert r["result"] is None and r["evidence"]["row_count"] == 0
    assert "over eur 50,000" in r["limitations"][0] and "can't be applied" in r["limitations"][0]


# ----------------------------------------------------------------------------- no false alarms on correct interpretations

CORRECT = [
    ("What are our top 5 vendors by total spend in 2025?", "spend", {"group_by": "vendor", "top_n": 5}),
    ("Which 5 suppliers did we spend the most with last year?", "spend", {"group_by": "vendor", "top_n": 5}),
    ("What is our total spend with vendors based in Spain?", "spend", {"countries": ["Spain"]}),
    ("Which invoices of EUR 1,000 or more have no PO, and who should they be flagged to?", "exceptions", {"check": "missing_po"}),
    ("List the invoices with no PO at or above EUR 1,000 (missing_po exceptions) and the cost centre owner each should "
     "be flagged to.", "exceptions", {"check": "missing_po"}),
    ("Which invoices over EUR 1,000 have no PO?", "exceptions", {"check": "missing_po"}),
    ("How about just the invoices from CC-FIN-100?", "exceptions", {"check": "missing_po", "cost_centers": ["CC-FIN-100"]}),
    ("Which invoices are missing an approver?", "exceptions", {"check": "missing_approver"}),
    ("What is our total spend on GBP invoices?", "spend", {"currencies": ["GBP"]}),
    ("What was our spend over 2025?", "spend", {}),
    ("What is our total net spend with vendors in the Consulting vendor category across the whole 2025 data period?",
     "spend", {"categories": ["Consulting"]}),
    ("How does BioReagent Direct compare with our other vendors?", "spend", {"group_by": "vendor"}),
    ("Is PharmaPrint Studios an active vendor?", "exceptions", {"check": "non_active_vendor", "vendor_ids": ["V-1006"]}),
]


@pytest.mark.parametrize("question, tool, params", CORRECT)
def test_correct_interpretations_pass(ctx, question, tool, params):
    model = SpendParams if tool == "spend" else ExceptionsParams
    assert missed_conditions(question, tool, model(**params), ctx) == []


# ----------------------------------------------------------------------------- other recognised conditions


@pytest.mark.parametrize("question, tool, params, expressible", [
    ("What is our total spend with vendors based in Spain?", "spend", {}, True),  # filter exists, not applied
    ("Which missing-PO invoices are from Spain?", "exceptions", {"check": "missing_po"}, False),  # no country filter
    ("What is our total spend on GBP invoices?", "spend", {}, True),
    ("Which invoices without a PO are in CC-FIN-100?", "exceptions", {"check": "missing_po"}, True),
    ("Which invoices without a PO are in CC-XYZ-999?", "exceptions", {"check": "missing_po"}, False),  # unknown
    ("What did r.okafor approve?", "spend", {}, False),  # no approver filter
    ("What is the spend for INV-20008 and INV-20010?", "spend", {}, False),  # no invoice filter
    ("Which vendors have more than 5 invoices?", "spend", {"group_by": "vendor"}, False),  # count threshold
    ("Top 3 vendors by spend: is BioReagent in it?", "spend", {"group_by": "vendor", "top_n": 3}, True),
])
def test_recognised_conditions_are_missed(ctx, question, tool, params, expressible):
    model = SpendParams if tool == "spend" else ExceptionsParams
    missed = missed_conditions(question, tool, model(**params), ctx)
    assert missed and all(m.expressible is expressible for m in missed)


def test_year_outside_data_is_declined():
    with client(FakeInterpreter("spend", {})) as c:  # a model that ignored the year
        code, r = ask(c, "What was our total spend in 2024?")
    assert r["status"] == "declined" and r["result"] is None and "2025 only" in r["limitations"][0]


def test_non_data_tools_are_not_checked(ctx):
    # The approval tier does not depend on the vendor, and a single-invoice check cannot be broader.
    assert missed_conditions("Who approves a EUR 60,000 invoice from BioReagent?", "approval_tier",
                             ApprovalTierParams(amount=60000), ctx) == []


def test_invented_value_is_still_an_error_not_a_clarification():
    """Ordering regression (found by evaluation run 11): the tool's validation runs before the coverage check."""
    with client(FakeInterpreter("spend", {"countries": ["Atlantis"]})) as c:
        code, r = ask(c, "What is our total spend with vendors based in Spain?")
    assert (code, r["status"], r["error"]["code"]) == (502, "error", "invalid_params")
