"""Orchestration for /ask and /summary.

/ask: interpret (LLM or stub) -> validate parameters -> run deterministic tool -> render template.
Every path returns an AskResponse with one of four statuses; errors fail closed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pandas as pd
from pydantic import ValidationError

from . import templates
from .data import MissingFxError
from .fx import FxRateMissing
from .llm import Interpreter, InterpreterError
from .schemas import (
    TOOL_PARAMS, TOOL_ROUTE, AskResponse, Clarification, ClauseRef, ErrorInfo, Evidence, EvidenceRow,
    FxRateRef, Interpretation, PreviousTurn, Versions,
)
from .tools import TOOLS, Context, InvalidParams, NeedsClarification, NotFound, OutOfPeriod, ToolOutput

EVIDENCE_COLUMNS = list(EvidenceRow.model_fields)

# HTTP status per error code (the body is always an AskResponse).
ERROR_HTTP_STATUS = {"fx_rate_missing": 503, "internal_error": 500}  # llm_* and invalid_params -> 502


def http_status_for(resp: AskResponse) -> int:
    if resp.status != "error" or resp.error is None:
        return 200
    return ERROR_HTTP_STATUS.get(resp.error.code, 502)


class AssistantService:
    def __init__(self, ctx: Context, interpreter: Interpreter):
        self.ctx = ctx
        self.interpreter = interpreter

    @property
    def versions(self) -> Versions:
        return Versions(data=self.ctx.data.version, policy=self.ctx.policy.version_label, fx=self.ctx.fx.version)

    # ------------------------------------------------------------------ /ask

    def ask(self, question: str, context: PreviousTurn | None = None) -> AskResponse:
        question = question.strip()
        base: dict[str, Any] = {
            "trace_id": uuid.uuid4().hex[:12],
            "question": question,
            "versions": self.versions,
        }
        interp = Interpretation(interpreter=self.interpreter.label)

        try:
            call = self.interpreter.interpret(question, context)
        except InterpreterError as e:
            return self._error(base, interp, e.code, e.message)
        interp.tool, interp.route, interp.params, interp.latency_ms, interp.usage = (
            call.name, TOOL_ROUTE[call.name], call.arguments, call.latency_ms, call.usage)

        try:
            params = TOOL_PARAMS[call.name].model_validate(call.arguments)
        except ValidationError as e:
            return self._error(base, interp, "invalid_params",
                               f"The interpreter returned invalid parameters for '{call.name}': {_brief(e)}")
        interp.params = params.model_dump(mode="json", exclude_defaults=True, exclude={"follow_up"})
        if context is not None and getattr(params, "follow_up", False):  # counts only when context was sent
            interp.follow_up_to = context.question

        if call.name == "clarify":
            return AskResponse(**base, status="clarification_needed", answer=params.question, interpretation=interp,
                               clarification=Clarification(question=params.question, options=params.options))
        if call.name == "decline":
            return AskResponse(**base, status="declined", interpretation=interp,
                               answer=templates.DECLINE_TEXT[params.reason],
                               limitations=[f"Interpreter's reason: {params.explanation}"])

        try:
            out = TOOLS[call.name](self.ctx, params)
        except InvalidParams as e:
            return self._error(base, interp, "invalid_params", str(e))
        except OutOfPeriod as e:
            return AskResponse(**base, status="declined", interpretation=interp,
                               answer=templates.DECLINE_TEXT["outside_data_period"], limitations=[str(e)])
        except NotFound as e:
            return AskResponse(**base, status="declined", interpretation=interp, answer=str(e))
        except NeedsClarification as e:
            return AskResponse(**base, status="clarification_needed", interpretation=interp, answer=e.question,
                               clarification=Clarification(question=e.question, options=e.options))
        except (MissingFxError, FxRateMissing) as e:
            return self._error(base, interp, "fx_rate_missing",
                               f"{e} No result is returned because it would be incomplete.")

        return AskResponse(
            **base,
            status="answered",
            answer=templates.render(call.name, out.result, self.ctx.policy),
            result=out.result,
            interpretation=interp,
            evidence=self._evidence(out),
            assumptions=out.assumptions,
            limitations=out.limitations,
        )

    def _error(self, base: dict[str, Any], interp: Interpretation, code: str, message: str) -> AskResponse:
        return AskResponse(**base, status="error", interpretation=interp,
                           answer="No answer was produced. " + message, error=ErrorInfo(code=code, message=message))

    def _evidence(self, out: ToolOutput) -> Evidence:
        rows = out.rows if out.rows is not None else pd.DataFrame(columns=EVIDENCE_COLUMNS)
        rows = rows.sort_values(["invoice_date", "invoice_number"])
        records = rows[EVIDENCE_COLUMNS].astype(object).where(rows[EVIDENCE_COLUMNS].notna(), None).to_dict("records")
        fx_rates = [FxRateRef(currency=r.currency, rate_date=r.rate_date, rate=r.rate) for r in out.fx_rates]
        if not fx_rates and not rows.empty:
            conv = rows[rows["currency"] != "EUR"]
            g = conv.groupby(["currency", "fx_rate_date", "fx_rate"]).size().reset_index(name="n")
            fx_rates = [FxRateRef(currency=c, rate_date=d, rate=r, invoice_count=int(n))
                        for c, d, r, n in g.itertuples(index=False)]
        return Evidence(
            rows=[EvidenceRow(**r) for r in records],
            row_count=len(records),
            clauses=[ClauseRef(id=c.id, section=c.section, text=c.text)
                     for c in (self.ctx.policy.clauses[i] for i in out.clause_ids)],
            fx_rates=fx_rates,
            fx_source=self.ctx.fx.source if fx_rates else None,
        )

    # ------------------------------------------------------------------ /summary

    def summary(self) -> dict[str, Any]:
        inv = self.ctx.data.invoices
        missing_fx = sorted(inv.loc[inv["amount_eur"].isna(), "invoice_number"])
        if missing_fx:
            raise MissingFxError(missing_fx)
        pol = self.ctx.policy

        def block(df: pd.DataFrame) -> dict[str, Any]:
            return {"invoice_count": int(len(df)), "total_eur": round(float(df["amount_eur"].sum()), 2)}

        by_currency = [
            {"currency": c, **block(g), "native_total": round(float(g["amount"].sum()), 2)}
            for c, g in inv.groupby("currency")
        ]
        by_status = [
            {"vendor_status": s, **block(g), "vendor_count": int(g["vendor_id"].nunique())}
            for s, g in inv.groupby("vendor_status")
        ]
        credit = inv[inv["amount"] < 0]
        converted = inv[inv["currency"] != "EUR"]
        return {
            "period": {"start": self.ctx.data.period_start.isoformat(), "end": self.ctx.data.period_end.isoformat()},
            **block(inv),
            "vendor_count": int(inv["vendor_id"].nunique()),
            "credit_notes": block(credit),
            "by_currency": sorted(by_currency, key=lambda x: -x["total_eur"]),
            "by_vendor_status": sorted(by_status, key=lambda x: -x["total_eur"]),
            "control_flags": {
                "po_threshold_eur": pol.po_threshold_eur,
                "missing_po_at_or_above_threshold": int(((inv["po_number"].isna()) & (inv["amount_eur"] >= pol.po_threshold_eur)).sum()),
                "missing_approver": int(inv["approved_by"].isna().sum()),
                "non_active_vendor": int((inv["vendor_status"] != "ACTIVE").sum()),
            },
            "fx": {
                "source": self.ctx.fx.source,
                "converted_invoices": int(len(converted)),
                "prior_rate_used": int((converted["fx_rate_date"] != converted["invoice_date"]).sum()),
            },
            "definitions": [
                "Spend = net invoiced amount in EUR (credit notes included; all invoice and vendor statuses).",
                "Non-EUR invoices converted at the ECB reference rate on the invoice date; most recent prior rate on non-publication days.",
            ],
            "versions": self.versions.model_dump(),
        }


def _brief(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}" for err in e.errors()[:3])
