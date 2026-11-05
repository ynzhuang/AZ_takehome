"""Question interpretation: an OpenAI-compatible chat-completions client with tool calling,
plus a clearly labelled keyword stub for running without an LLM.

The LLM must return exactly one tool call. Anything else (timeout, HTTP error, no tool
call, several tool calls, unknown tool, unparsable arguments) raises InterpreterError
and the API returns status "error": it fails closed and never guesses.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import Settings
from .data import Dataset
from .policy import Policy
from .schemas import TOOL_PARAMS, PreviousTurn

PROMPT_PATH = Path(__file__).parent / "prompts" / "interpreter_system.md"

_CATALOG_ENUMS = {  # tool parameter -> catalog key, injected as JSON-schema enums
    "vendor_ids": "vendor_ids", "countries": "countries", "categories": "categories",
    "vendor_statuses": "vendor_statuses", "currencies": "currencies", "cost_centers": "cost_centers",
    "descriptions": "descriptions",
}


class InterpreterError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    latency_ms: int
    usage: dict[str, Any] | None = None  # tokens (and cost, if the provider reports it)


class Interpreter(Protocol):
    label: str

    def interpret(self, question: str, context: PreviousTurn | None = None) -> ToolCall: ...


def build_user_message(question: str, context: PreviousTurn | None) -> str:
    """The LLM sees at most one previous turn, as marked data. Without context: just the question."""
    if context is None:
        return question
    return (
        "<previous_turn>\n"
        f"question: {context.question}\n"
        f"interpretation: {context.tool} {json.dumps(context.params, sort_keys=True)}\n"
        "</previous_turn>\n"
        f"Current question: {question}"
    )


# --------------------------------------------------------------------------- prompt and tool schemas


def build_system_prompt(data: Dataset, policy: Policy, today: date | None = None) -> str:
    vendors = ", ".join(f"{i} = {n} ({st})" for i, n, st in
                        zip(data.vendors["vendor_id"], data.vendors["vendor_name"], data.vendors["status"]))
    catalog = "\n".join(f"  - {k}: {vendors if k == 'vendor_ids' else ', '.join(v)}" for k, v in data.catalog().items())
    return PROMPT_PATH.read_text(encoding="utf-8").format(
        today=(today or date.today()).isoformat(),
        period_start=data.period_start.isoformat(),
        period_end=data.period_end.isoformat(),
        period_year=data.period_start.year,
        catalog=catalog,
        policy_version=policy.version_label,
        policy_text=policy.text,
    )


def _clean(schema: Any) -> Any:
    """Simplify Pydantic JSON schema for broad provider compatibility (no titles, no nullable anyOf)."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            non_null = [s for s in schema["anyOf"] if s.get("type") != "null"]
            if len(non_null) == 1:
                merged = {**{k: v for k, v in schema.items() if k != "anyOf"}, **non_null[0]}
                return _clean(merged)
        return {k: _clean(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_clean(s) for s in schema]
    return schema


def build_tools(data: Dataset) -> list[dict[str, Any]]:
    catalog = data.catalog()
    tools = []
    for name, model in TOOL_PARAMS.items():
        schema = _clean(model.model_json_schema())
        schema.pop("description", None)
        for prop, key in _CATALOG_ENUMS.items():
            if prop in schema.get("properties", {}):
                schema["properties"][prop]["items"] = {"type": "string", "enum": catalog[key]}
        tools.append({
            "type": "function",
            "function": {"name": name, "description": (model.__doc__ or "").strip(), "parameters": schema},
        })
    return tools


# --------------------------------------------------------------------------- real LLM


class OpenAICompatibleInterpreter:
    def __init__(self, settings: Settings, system_prompt: str, tools: list[dict[str, Any]],
                 transport: httpx.BaseTransport | None = None):
        self.label = settings.llm_label
        self._settings = settings
        self._system_prompt = system_prompt
        self._tools = tools
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:  # omitted when a network proxy injects credentials
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        self._client = httpx.Client(base_url=settings.llm_base_url, headers=headers,
                                    timeout=settings.llm_timeout_seconds, transport=transport)

    def interpret(self, question: str, context: PreviousTurn | None = None) -> ToolCall:
        body = {
            "model": self._settings.llm_model,
            "temperature": 0,
            "max_tokens": 800,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": build_user_message(question, context)},
            ],
            "tools": self._tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,  # exactly one tool call per question, enforced by the API
        }
        start = time.monotonic()
        try:
            resp = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException:
            raise InterpreterError("llm_timeout", f"The LLM did not respond within {self._settings.llm_timeout_seconds:.0f}s.")
        except httpx.HTTPError as e:
            raise InterpreterError("llm_unreachable", f"Could not reach the LLM endpoint ({type(e).__name__}).")
        latency_ms = int((time.monotonic() - start) * 1000)
        if resp.status_code != 200:
            raise InterpreterError("llm_http_error", f"The LLM endpoint returned HTTP {resp.status_code}.")
        try:
            payload = resp.json()
            message = payload["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise InterpreterError("llm_invalid_response", "The LLM response was not in the expected format.")
        calls = message.get("tool_calls") or []
        if len(calls) != 1:
            raise InterpreterError("llm_tool_call_count", f"Expected exactly one tool call, got {len(calls)}.")
        fn = calls[0].get("function") or {}
        name = fn.get("name")
        if name not in TOOL_PARAMS:
            raise InterpreterError("llm_unknown_tool", f"The LLM called an unknown tool: {name!r}.")
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            raise InterpreterError("llm_invalid_arguments", "The LLM returned tool arguments that are not a JSON object.")
        u = payload.get("usage") or {}
        usage = {k: u[k] for k in ("prompt_tokens", "completion_tokens", "cost") if isinstance(u.get(k), (int, float))}
        return ToolCall(name=name, arguments=args, latency_ms=latency_ms, usage=usage or None)


# --------------------------------------------------------------------------- stub (no LLM)


_FOLLOW_UP = re.compile(r"^\s*(how about|what about|and for|same for|just|only)\b|\b(those|these|them|instead)\b")


class StubInterpreter:
    """Keyword rules for running without an LLM. Clearly labelled; not evaluated as the LLM."""

    label = "stub (keyword rules, no LLM)"

    def __init__(self, data: Dataset):
        self._data = data

    def interpret(self, question: str, context: PreviousTurn | None = None) -> ToolCall:
        return ToolCall(*self._rules(question, context), latency_ms=0)

    def _rules(self, question: str, context: PreviousTurn | None = None) -> tuple[str, dict[str, Any]]:
        q = question.lower()
        catalog = self._data.catalog()
        if _FOLLOW_UP.search(q):
            if context is None:
                return "clarify", {
                    "question": "This seems to refer to an earlier question. Please ask it as a complete question.",
                    "options": [],
                }
            args: dict[str, Any] = dict(context.params)
            if ccs := [c for c in catalog["cost_centers"] if c.lower() in q]:
                args["cost_centers"] = ccs
            if countries := [c for c in catalog["countries"] if c.lower() in q]:
                args["countries"] = countries
            return context.tool, {**args, "follow_up": True}
        years = {int(y) for y in re.findall(r"\b(20\d\d)\b", q)}
        if years - {self._data.period_start.year}:
            return "decline", {"reason": "outside_data_period", "explanation": "Only 2025 invoices are available."}
        if m := re.search(r"\binv-\d+\b", q):
            return "invoice_check", {"invoice_number": m.group(0).upper()}
        if "approve" in q or "sign off" in q or "sign-off" in q:
            if m := re.search(r"(usd|gbp|eur|€|\$|£)?\s*([\d][\d,.]*)\s*(k\b)?\s*(usd|gbp|eur)?", q):
                amount = float(m.group(2).replace(",", "")) * (1000 if m.group(3) else 1)
                sym = {"€": "EUR", "$": "USD", "£": "GBP"}
                ccy = (m.group(1) or m.group(4) or "")
                ccy = sym.get(ccy, ccy.upper()) or None
                return "approval_tier", {"amount": amount, **({"currency": ccy} if ccy else {})}
        if re.search(r"(no|without|missing)\s+(a\s+)?po\b", q):
            return "exceptions", {"check": "missing_po"}
        if re.search(r"(no|without|missing)\s+(an\s+)?approver", q):
            return "exceptions", {"check": "missing_approver"}
        if re.search(r"inactive|on hold|not active|non-active", q):
            return "exceptions", {"check": "non_active_vendor"}
        if "consult" in q:
            return "clarify", {
                "question": "Do you mean vendors categorised as Consulting, or invoices described as consulting services?",
                "options": ["What is our net spend with vendors in the Consulting category?",
                            "What is our net spend on invoices described as consulting services?"],
            }
        if "spend" in q or "spent" in q or "vendors" in q or "suppliers" in q:
            args: dict[str, Any] = {}
            countries = [c for c in catalog["countries"] if c.lower() in q]
            if countries:
                args["countries"] = countries
            if m := re.search(r"top\s+(\d+)", q):
                args.update(group_by="vendor", top_n=int(m.group(1)))
            return "spend", args
        return "decline", {"reason": "unsupported_question", "explanation": "The stub could not match this question."}
