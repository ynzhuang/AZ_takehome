"""Pydantic models: LLM tool parameters (validated before any tool runs) and API responses."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- tool parameters


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpendParams(_Params):
    """Net invoiced spend in EUR (credit notes included, all invoice and vendor statuses)."""

    group_by: Literal["none", "vendor", "country", "category", "currency", "vendor_status", "cost_center"] = Field(
        "none", description="Dimension to group totals by. Use 'none' for a single total."
    )
    top_n: int | None = Field(None, ge=1, le=50, description="Return only the N largest groups (requires group_by).")
    vendor_ids: list[str] = Field(default_factory=list, description="Filter: vendor IDs.")
    countries: list[str] = Field(default_factory=list, description="Filter: vendor country (from the vendor master).")
    categories: list[str] = Field(default_factory=list, description="Filter: vendor category (from the vendor master).")
    vendor_statuses: list[str] = Field(default_factory=list, description="Filter: vendor status.")
    currencies: list[str] = Field(default_factory=list, description="Filter: invoice currency.")
    cost_centers: list[str] = Field(default_factory=list, description="Filter: cost centre code.")
    descriptions: list[str] = Field(default_factory=list, description="Filter: exact invoice description values.")
    date_from: date | None = Field(None, description="Filter: invoice date on or after (YYYY-MM-DD). Omit for the whole data period.")
    date_to: date | None = Field(None, description="Filter: invoice date on or before (YYYY-MM-DD). Omit for the whole data period.")


class ExceptionsParams(_Params):
    """List invoices that need follow-up under a policy rule."""

    check: Literal["missing_po", "missing_approver", "non_active_vendor"] = Field(
        description="missing_po: no PO at or above the EUR PO threshold (§2.1-2.2); "
        "missing_approver: no approver recorded (§3.1); "
        "non_active_vendor: vendor status INACTIVE or ON_HOLD (§4.1)."
    )
    vendor_ids: list[str] = Field(default_factory=list, description="Filter: vendor IDs.")
    cost_centers: list[str] = Field(default_factory=list, description="Filter: cost centre code.")
    date_from: date | None = Field(None, description="Filter: invoice date on or after (YYYY-MM-DD).")
    date_to: date | None = Field(None, description="Filter: invoice date on or before (YYYY-MM-DD).")


class ApprovalTierParams(_Params):
    """Required approver role for an amount, from the policy approval matrix (§3)."""

    amount: float = Field(gt=0, description="Invoice amount as stated in the question (e.g. '60k' -> 60000).")
    currency: str | None = Field(None, description="Currency code, only if stated in the question.")
    invoice_date: date | None = Field(None, description="Invoice date (YYYY-MM-DD), only if stated in the question.")


class InvoiceCheckParams(_Params):
    """Check one invoice against the policy rules that the data can support."""

    invoice_number: str = Field(pattern=r"^INV-\d+$", description="Invoice number, e.g. INV-20008.")


class PolicyClausesParams(_Params):
    """Quote policy clauses verbatim for general policy questions."""

    clause_ids: list[str] = Field(min_length=1, max_length=6, description="Clause IDs from the policy, e.g. ['6.1'].")


class ClarifyParams(_Params):
    """Ask the user to clarify a materially ambiguous question."""

    question: str = Field(min_length=5, max_length=300, description="Short clarification question.")
    options: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Each option is a complete, unambiguous rewrite of the user's question that can be asked as-is.",
    )


class DeclineParams(_Params):
    """Decline a question that cannot be answered from the available data and policy."""

    reason: Literal["outside_data_period", "data_not_available", "unsupported_question", "not_finance_related"]
    explanation: str = Field(max_length=300, description="One sentence: what is missing or out of scope.")


TOOL_PARAMS: dict[str, type[_Params]] = {
    "spend": SpendParams,
    "exceptions": ExceptionsParams,
    "approval_tier": ApprovalTierParams,
    "invoice_check": InvoiceCheckParams,
    "policy_clauses": PolicyClausesParams,
    "clarify": ClarifyParams,
    "decline": DeclineParams,
}

# Route is derived from the tool deterministically, not claimed by the LLM.
TOOL_ROUTE: dict[str, str] = {
    "spend": "data",
    "exceptions": "data+policy",
    "approval_tier": "policy",
    "invoice_check": "data+policy",
    "policy_clauses": "policy",
    "clarify": "none",
    "decline": "none",
}

# --------------------------------------------------------------------------- API models

Status = Literal["answered", "clarification_needed", "declined", "error"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class Interpretation(BaseModel):
    tool: str | None = None
    route: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    interpreter: str  # which model / stub produced it
    latency_ms: int | None = None
    usage: dict[str, Any] | None = None  # LLM tokens / cost for this question


class EvidenceRow(BaseModel):
    invoice_number: str
    invoice_date: date
    vendor_id: str
    vendor_name: str
    country: str
    vendor_status: str
    description: str
    amount: float
    currency: str
    fx_rate: float | None
    fx_rate_date: date | None
    amount_eur: float | None
    cost_center: str
    po_number: str | None
    approved_by: str | None


class ClauseRef(BaseModel):
    id: str
    section: str
    text: str


class FxRateRef(BaseModel):
    currency: str
    rate_date: date
    rate: float
    invoice_count: int | None = None


class Evidence(BaseModel):
    rows: list[EvidenceRow] = Field(default_factory=list)
    row_count: int = 0
    clauses: list[ClauseRef] = Field(default_factory=list)
    fx_rates: list[FxRateRef] = Field(default_factory=list)
    fx_source: str | None = None


class Clarification(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)


class ErrorInfo(BaseModel):
    code: str
    message: str


class Versions(BaseModel):
    data: str
    policy: str
    fx: str


class AskResponse(BaseModel):
    trace_id: str
    status: Status
    question: str
    answer: str
    result: dict[str, Any] | None = None
    interpretation: Interpretation
    evidence: Evidence = Field(default_factory=Evidence)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    clarification: Clarification | None = None
    error: ErrorInfo | None = None
    versions: Versions
