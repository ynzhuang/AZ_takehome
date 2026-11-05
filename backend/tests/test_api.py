from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.data import load_dataset
from app.fx import FxTable
from app.llm import InterpreterError, OpenAICompatibleInterpreter, build_tools
from app.main import create_app
from app.service import AssistantService
from app.tools import Context

from .conftest import FakeInterpreter, stub_settings


def client(interpreter=None, **settings) -> TestClient:
    return TestClient(create_app(stub_settings(**settings), interpreter))


def ask(c: TestClient, q: str = "q"):
    r = c.post("/ask", json={"question": q})
    return r.status_code, r.json()


def test_summary_needs_no_llm():
    failing = FakeInterpreter(error=InterpreterError("llm_timeout", "timeout"))
    with client(failing) as c:
        s = c.get("/summary")
        assert s.status_code == 200
        body = s.json()
        assert body["invoice_count"] == 140
        assert body["control_flags"] == {"po_threshold_eur": 1000.0, "missing_po_at_or_above_threshold": 21, "missing_approver": 25, "non_active_vendor": 29}
        assert {x["currency"] for x in body["by_currency"]} == {"EUR", "GBP", "USD"}
        assert {x["vendor_status"] for x in body["by_vendor_status"]} == {"ACTIVE", "INACTIVE", "ON_HOLD"}
        code, resp = ask(c)
        assert code == 502 and resp["status"] == "error" and resp["error"]["code"] == "llm_timeout"
        assert resp["result"] is None and resp["evidence"]["row_count"] == 0


def test_answered_response_carries_interpretation_evidence_and_versions():
    with client(FakeInterpreter("spend", {"countries": ["Spain"]})) as c:
        code, r = ask(c, "Spain spend?")
    assert code == 200 and r["status"] == "answered"
    assert r["interpretation"] == {"tool": "spend", "route": "data", "params": {"countries": ["Spain"]},
                                   "interpreter": "fake", "latency_ms": 1, "usage": None, "follow_up_to": None}
    assert r["evidence"]["row_count"] == 49 == len(r["evidence"]["rows"])
    assert [c["id"] for c in r["evidence"]["clauses"]] == ["5.1"]
    assert r["evidence"]["fx_rates"] and r["versions"]["policy"] == "FIN-POL-014 v3.2"
    assert f"{r['result']['total_eur']:,.2f}" in r["answer"]  # answer text is rendered from the result


@pytest.mark.parametrize("interp, status, http", [
    (FakeInterpreter("clarify", {"question": "Category or description?", "options": ["A?", "B?"]}), "clarification_needed", 200),
    (FakeInterpreter("decline", {"reason": "outside_data_period", "explanation": "2024"}), "declined", 200),
    (FakeInterpreter("spend", {"date_from": "2024-01-01", "date_to": "2024-12-31"}), "declined", 200),
    (FakeInterpreter("invoice_check", {"invoice_number": "INV-99999"}), "declined", 200),
    (FakeInterpreter("approval_tier", {"amount": 55000, "currency": "USD"}), "clarification_needed", 200),
    (FakeInterpreter("spend", {"countries": ["Atlantis"]}), "error", 502),  # invented filter value
    (FakeInterpreter("spend", {"group_by": "planet"}), "error", 502),  # schema violation
    (FakeInterpreter("spend", {"sql": "DROP TABLE"}), "error", 502),  # extra parameter
    (FakeInterpreter("policy_clauses", {"clause_ids": ["12.9"]}), "error", 502),  # invented clause
])
def test_status_mapping(interp, status, http):
    with client(interp) as c:
        code, r = ask(c)
    assert (code, r["status"]) == (http, status)
    if status != "answered":
        assert r["result"] is None and r["evidence"]["row_count"] == 0


def test_missing_fx_rate_fails_closed(ctx):
    rates = {c: {d: v for d, v in ctx.fx._rates[c].items() if d.month != 2} for c in ctx.fx._rates}
    fx = FxTable(rates, version="test", source="test")
    broken = Context(data=load_dataset(stub_settings().data_dir, fx), policy=ctx.policy, fx=fx)
    svc = AssistantService(broken, FakeInterpreter("spend", {}))
    r = svc.ask("total spend?")
    assert r.status == "error" and r.error.code == "fx_rate_missing" and r.result is None
    # a filter that avoids the affected invoices still works
    svc.interpreter = FakeInterpreter("spend", {"currencies": ["EUR"]})
    assert svc.ask("EUR spend?").status == "answered"


def test_request_validation():
    with client(FakeInterpreter()) as c:
        assert c.post("/ask", json={"question": ""}).status_code == 422
        assert c.post("/ask", json={"question": "x" * 501}).status_code == 422


# ----------------------------------------------------------------------------- LLM client (mocked HTTP)


def _llm(ctx, handler, api_key=""):
    settings = stub_settings(llm_provider="openai_compatible", llm_api_key=api_key)
    return OpenAICompatibleInterpreter(settings, "system", build_tools(ctx.data), transport=httpx.MockTransport(handler))


def _completion(tool_calls):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "tool_calls": tool_calls}}]})


def _call(name, args):
    return {"id": "1", "type": "function", "function": {"name": name, "arguments": args}}


def test_llm_client_parses_single_tool_call_and_omits_auth_without_key(ctx):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return _completion([_call("spend", '{"countries": ["Spain"]}')])

    call = _llm(ctx, handler).interpret("Spain?")
    assert (call.name, call.arguments) == ("spend", {"countries": ["Spain"]})
    assert seen["auth"] is None
    assert seen["body"]["tool_choice"] == "required" and seen["body"]["temperature"] == 0
    assert seen["body"]["parallel_tool_calls"] is False


def test_llm_client_sends_key_when_configured(ctx):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return _completion([_call("decline", '{"reason": "unsupported_question", "explanation": "x"}')])

    _llm(ctx, handler, api_key="test-key").interpret("q")
    assert seen["auth"] == "Bearer test-key"


@pytest.mark.parametrize("handler, code", [
    (lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), "llm_timeout"),
    (lambda req: httpx.Response(500, text="boom"), "llm_http_error"),
    (lambda req: httpx.Response(200, text="not json"), "llm_invalid_response"),
    (lambda req: _completion([]), "llm_tool_call_count"),
    (lambda req: _completion([_call("spend", "{}"), _call("spend", "{}")]), "llm_tool_call_count"),
    (lambda req: _completion([_call("run_sql", "{}")]), "llm_unknown_tool"),
    (lambda req: _completion([_call("spend", "{not json")]), "llm_invalid_arguments"),
])
def test_llm_client_fails_closed(ctx, handler, code):
    with pytest.raises(InterpreterError) as e:
        _llm(ctx, handler).interpret("q")
    assert e.value.code == code


def test_tool_schemas_are_simple_and_constrained(ctx):
    tools = {t["function"]["name"]: t["function"] for t in build_tools(ctx.data)}
    assert set(tools) == {"spend", "exceptions", "approval_tier", "invoice_check", "policy_clauses", "clarify", "decline"}
    text = json.dumps(tools)
    assert "anyOf" not in text and "$defs" not in text
    assert "Spain" in json.dumps(tools["spend"]["parameters"]["properties"]["countries"])


# ----------------------------------------------------------------------------- follow-ups (one previous turn as context)

PREV = {"question": "Which invoices of EUR 1,000 or more have no PO?", "tool": "exceptions", "params": {"check": "missing_po"}}
FOLLOW = "How about just the invoices from CC-FIN-100?"


def test_follow_up_context_reaches_interpreter_and_is_labelled():
    fake = FakeInterpreter("exceptions", {"check": "missing_po", "cost_centers": ["CC-FIN-100"], "follow_up": True})
    with client(fake) as c:
        r = c.post("/ask", json={"question": FOLLOW, "context": PREV})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "answered"
    assert fake.received_context.question == PREV["question"] and fake.received_context.tool == "exceptions"
    assert body["interpretation"]["follow_up_to"] == PREV["question"]
    assert body["interpretation"]["params"] == {"check": "missing_po", "cost_centers": ["CC-FIN-100"]}  # flag stripped
    assert body["result"]["count"] == 10 and body["result"]["total_eur"] == 383083.04


def test_follow_up_flag_ignored_without_context():
    fake = FakeInterpreter("spend", {"cost_centers": ["CC-FIN-100"], "follow_up": True})
    with client(fake) as c:
        body = c.post("/ask", json={"question": FOLLOW}).json()
    assert fake.received_context is None and body["interpretation"]["follow_up_to"] is None


@pytest.mark.parametrize("context", [
    {"question": "q", "tool": "clarify", "params": {}},  # only answered tools can be context
    {"question": "q", "tool": "run_sql", "params": {}},
    {"question": "q", "tool": "exceptions", "params": {"check": "everything"}},  # invalid params for the tool
    {"question": "q", "tool": "spend", "params": {"sql": "x"}},
    {"question": "", "tool": "spend", "params": {}},
    {"question": "q", "tool": "spend", "params": {}, "history": []},  # extra fields rejected
])
def test_invalid_context_is_rejected(context):
    with client(FakeInterpreter("spend", {})) as c:
        assert c.post("/ask", json={"question": FOLLOW, "context": context}).status_code == 422


def test_llm_receives_previous_turn_only_when_given(ctx):
    from app.schemas import PreviousTurn
    seen = []

    def handler(req):
        seen.append(json.loads(req.content)["messages"])
        return _completion([_call("spend", "{}")])

    llm = _llm(ctx, handler)
    llm.interpret(FOLLOW)
    llm.interpret(FOLLOW, PreviousTurn(**PREV))
    assert seen[0][-1] == {"role": "user", "content": FOLLOW}  # unchanged single-question behaviour
    content = seen[1][-1]["content"]
    assert len(seen[1]) == 2  # system + one user message: no chat history
    assert "<previous_turn>" in content and PREV["question"] in content and '"check": "missing_po"' in content
    assert content.endswith(f"Current question: {FOLLOW}")


def test_stub_follow_up():
    from app.schemas import PreviousTurn
    with client() as c:  # stub interpreter
        alone = c.post("/ask", json={"question": FOLLOW}).json()
        with_ctx = c.post("/ask", json={"question": FOLLOW, "context": PREV}).json()
    assert alone["status"] == "clarification_needed"
    assert with_ctx["status"] == "answered" and with_ctx["result"]["count"] == 10
    assert with_ctx["interpretation"]["follow_up_to"] == PREV["question"]
    assert PreviousTurn(**PREV).tool == "exceptions"


def test_real_system_prompt_renders(ctx):
    """The prompt template is filled with str.format; literal braces must be escaped (regression)."""
    from app.llm import build_system_prompt
    prompt = build_system_prompt(ctx.data, ctx.policy)
    assert "FIN-POL-014 v3.2" in prompt and "CC-FIN-100" in prompt
    assert 'exceptions {"check": "missing_po"}' in prompt and "{period_start}" not in prompt


def test_invoice_check_answer_never_prints_nan():
    """Third cold review: INV-20005 has no approver; the answer said 'Recorded approver: nan'."""
    with client(FakeInterpreter("invoice_check", {"invoice_number": "INV-20005"})) as c:
        code, r = ask(c, "Was INV-20005 properly approved?")
    assert r["status"] == "answered" and "nan" not in r["answer"].lower().replace(".", " ").split()
    assert {c["clause"]: c["result"] for c in r["result"]["checks"]}["3.1"] == "flag"
