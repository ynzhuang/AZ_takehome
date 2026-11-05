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
        assert body["control_flags"] == {"missing_po_at_or_above_threshold": 21, "missing_approver": 25, "non_active_vendor": 29}
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
                                   "interpreter": "fake", "latency_ms": 1}
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
