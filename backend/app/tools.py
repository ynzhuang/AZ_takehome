"""Deterministic tools. The LLM only chooses a tool and its parameters; all figures,
row selection and policy logic happen here (design §2.4).

Each tool returns a ToolOutput: a structured result (rendered by templates.py), the
invoice rows used, the policy clauses relied on, assumptions and limitations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from .data import Dataset, require_eur
from .fx import FxRate, FxTable, to_eur
from .policy import Policy
from .schemas import ApprovalTierParams, ExceptionsParams, InvoiceCheckParams, PolicyClausesParams, SpendParams

# --------------------------------------------------------------------------- outcomes other than an answer


class InvalidParams(Exception):
    """Parameters are well-formed but reference values that do not exist (e.g. an invented country)."""


class OutOfPeriod(Exception):
    """The requested period is not (fully) covered by the data."""


class NotFound(Exception):
    pass


class NeedsClarification(Exception):
    def __init__(self, question: str, options: list[str] | None = None):
        super().__init__(question)
        self.question = question
        self.options = options or []


@dataclass
class ToolOutput:
    result: dict[str, Any]
    rows: pd.DataFrame | None = None
    clause_ids: list[str] = field(default_factory=list)
    fx_rates: list[FxRate] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class Context:
    data: Dataset
    policy: Policy
    fx: FxTable


# Wording for invoice_check results. "ok" is deliberately not "compliant": only the listed fields were checked.
RESULT_LABELS = {
    "ok": "No issue detected in checked fields",
    "flag": "Flag for review",
    "cannot_determine": "Cannot determine from available data",
    "not_applicable": "Not applicable",
}


def eur(x: float) -> str:
    return f"EUR {x:,.2f}"


# --------------------------------------------------------------------------- shared helpers

_FILTER_COLUMNS = {
    "vendor_ids": ("vendor_id", "vendor_ids"),
    "countries": ("country", "countries"),
    "categories": ("category", "categories"),
    "vendor_statuses": ("vendor_status", "vendor_statuses"),
    "currencies": ("currency", "currencies"),
    "cost_centers": ("cost_center", "cost_centers"),
    "descriptions": ("description", "descriptions"),
}


def _apply_filters(ctx: Context, params: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Validate filter values against the data catalog and apply them. Returns (rows, applied filters)."""
    catalog = ctx.data.catalog()
    df = ctx.data.invoices
    applied: dict[str, Any] = {}
    for name, (column, catalog_key) in _FILTER_COLUMNS.items():
        values = getattr(params, name, None)
        if not values:
            continue
        unknown = sorted(set(values) - set(catalog[catalog_key]))
        if unknown:
            raise InvalidParams(f"Unknown {name} value(s): {unknown}")
        df = df[df[column].isin(values)]
        applied[name] = list(values)

    start, end = ctx.data.period_start, ctx.data.period_end
    d_from, d_to = getattr(params, "date_from", None), getattr(params, "date_to", None)
    if d_from and d_to and d_from > d_to:
        raise InvalidParams("date_from is after date_to")
    lo, hi = d_from or start, d_to or end
    if lo < start or hi > end:
        raise OutOfPeriod(
            f"The data covers invoices dated {start.isoformat()} to {end.isoformat()}; "
            f"the requested period {lo.isoformat()} to {hi.isoformat()} is not fully covered."
        )
    if d_from or d_to:
        df = df[(df["invoice_date"] >= lo) & (df["invoice_date"] <= hi)]
        applied["period"] = f"{lo.isoformat()} to {hi.isoformat()}"
    return df, applied


def _period_text(ctx: Context, applied: dict[str, Any]) -> str:
    return applied.get("period") or f"{ctx.data.period_start.isoformat()} to {ctx.data.period_end.isoformat()}"


def _fx_assumption(df: pd.DataFrame) -> str | None:
    converted = df[df["currency"] != "EUR"]
    if converted.empty:
        return None
    fallback = int((converted["fx_rate_date"] != converted["invoice_date"]).sum())
    text = f"{len(converted)} non-EUR invoice(s) converted at the ECB reference rate on the invoice date (§5.1)"
    if fallback:
        text += f"; {fallback} dated on a non-publication day used the most recent prior ECB rate"
    return text + "."


def _status_note(df: pd.DataFrame) -> str | None:
    non_active = df[df["vendor_status"] != "ACTIVE"]
    if non_active.empty:
        return None
    parts = ", ".join(f"{s}: {n}" for s, n in non_active["vendor_status"].value_counts().sort_index().items())
    return (
        f"Includes {len(non_active)} invoice(s) ({eur(non_active['amount_eur'].sum())}) from vendors that are not ACTIVE "
        f"in the vendor master ({parts}). Vendor status is an undated snapshot."
    )


def _group(df: pd.DataFrame, column: str, label) -> list[dict[str, Any]]:
    g = df.groupby(column, sort=False).agg(total_eur=("amount_eur", "sum"), invoice_count=("invoice_number", "count"))
    g = g.reset_index().sort_values(["total_eur", column], ascending=[False, True])
    return [
        {"key": k, "label": label(k), "total_eur": round(t, 2), "invoice_count": int(n)}
        for k, t, n in zip(g[column], g["total_eur"], g["invoice_count"])
    ]


# --------------------------------------------------------------------------- tools


_GROUP_PLURAL = {
    "vendor": "vendors", "country": "countries", "category": "vendor categories", "currency": "currencies",
    "vendor_status": "vendor statuses", "cost_center": "cost centres",
}


def spend(ctx: Context, p: SpendParams) -> ToolOutput:
    if p.top_n and p.group_by == "none":
        raise InvalidParams("top_n requires group_by")
    df, applied = _apply_filters(ctx, p)
    require_eur(df)
    total = round(float(df["amount_eur"].sum()), 2)

    groups: list[dict[str, Any]] = []
    rows = df
    if p.group_by != "none":
        column = {"vendor": "vendor_id"}.get(p.group_by, p.group_by)
        label = ctx.data.vendor_label if p.group_by == "vendor" else str
        groups = _group(df, column, label)
        group_count = len(groups)
        if p.top_n:
            groups = groups[: p.top_n]
            rows = df[df[column].isin([g["key"] for g in groups])]
    else:
        group_count = 0

    # Notes describe the rows behind the figures shown (`rows`). With top_n these are only the
    # top groups; the ranking and the overall total still cover every invoice in scope (`df`).
    assumptions = [
        "Spend = net invoiced amount in EUR: all invoices regardless of PO, approver or vendor status; "
        "credit notes reduce spend. Not approved or paid amounts (no payment data).",
        f"Period: invoice dates {_period_text(ctx, applied)}.",
    ]
    truncated = len(rows) < len(df)
    if truncated:
        plural = _GROUP_PLURAL[p.group_by]
        assumptions.append(
            f"These notes describe the {len(rows)} invoices of the {len(groups)} {plural} shown. "
            f"The ranking and the {eur(total)} total cover all {len(df)} invoices of all {group_count} {plural}."
        )
    if fx_note := _fx_assumption(rows):
        assumptions.append(fx_note)
    credit = rows[rows["amount"] < 0]
    if not credit.empty:
        assumptions.append(
            f"Includes {len(credit)} credit note(s) totalling {eur(credit['amount_eur'].sum())} "
            f"({', '.join(credit['invoice_number'])})."
        )
    other_credit = df[(df["amount"] < 0) & ~df.index.isin(rows.index)]
    if truncated and not other_credit.empty:
        assumptions.append(
            f"The {eur(total)} total also includes {len(other_credit)} credit note(s) totalling "
            f"{eur(other_credit['amount_eur'].sum())} ({', '.join(other_credit['invoice_number'])}) "
            f"from {_GROUP_PLURAL[p.group_by]} not shown."
        )
    limitations = [n for n in [_status_note(rows)] if n]
    if df.empty:
        limitations.append("No invoices match these filters.")

    return ToolOutput(
        result={
            "total_eur": total,
            "invoice_count": int(len(df)),
            "group_by": p.group_by,
            "top_n": p.top_n,
            "group_count": group_count,
            "groups": groups,
            "filters": applied,
            "shown_total_eur": round(float(rows["amount_eur"].sum()), 2),
        },
        rows=rows,
        clause_ids=["5.1"] if (df["currency"] != "EUR").any() else [],
        assumptions=assumptions,
        limitations=limitations,
    )


def exceptions(ctx: Context, p: ExceptionsParams) -> ToolOutput:
    df, applied = _apply_filters(ctx, p)
    pol = ctx.policy
    limitations: list[str] = []
    assumptions = [f"Period: invoice dates {_period_text(ctx, applied)}."]

    if p.check == "missing_po":
        require_eur(df)
        no_po = df[df["po_number"].isna()]
        sel = no_po[no_po["amount_eur"] >= pol.po_threshold_eur]
        below = no_po[no_po["amount_eur"] < pol.po_threshold_eur]
        recurring = sel[sel["description"].isin(["Cloud hosting", "Facilities services"])]
        groups = _group(sel, "cost_center", str)
        clause_ids = ["2.1", "2.2", "2.3", "9.1"]
        action = "Flag each invoice to its cost-centre owner and include it in the quarterly maverick-spend report (§2.2)."
        assumptions.append(f"Threshold applied to the EUR amount: {eur(pol.po_threshold_eur)} or above (§2.1).")
        limitations += [
            "These are potential exceptions, not confirmed violations: blanket POs (§2.3) and emergency "
            "exceptions (§9.1) cannot be checked because there is no PO master or Exception Register.",
            f"{len(recurring)} of these are recurring services (cloud hosting, facilities) that may be covered by a blanket PO.",
            "PO validity and whether the PO was issued before work started cannot be checked (no PO data).",
            "Cost-centre owners are not in the data; the cost centre to flag is listed for each invoice.",
        ]
        if not below.empty:
            assumptions.append(f"{len(below)} invoice(s) without a PO are below the threshold and not listed.")
        extra = {"recurring_service_count": int(len(recurring))}
    elif p.check == "missing_approver":
        sel = df[df["approved_by"].isna()]
        require_eur(sel)
        groups = _group(sel, "cost_center", str)
        clause_ids = ["3.1"]
        action = "Hold these invoices; they may not be paid until an approval is recorded (§3.1)."
        also_no_po = sel[sel["po_number"].isna()]
        limitations.append("Payment status is not in the data, so it cannot be checked whether any were already paid.")
        extra = {"also_missing_po": sorted(also_no_po["invoice_number"])}
    else:  # non_active_vendor
        sel = df[df["vendor_status"] != "ACTIVE"]
        require_eur(sel)
        groups = _group(sel, "vendor_id", lambda v: f"{ctx.data.vendor_label(v)}, {sel.loc[sel['vendor_id'] == v, 'vendor_status'].iat[0]}")
        clause_ids = ["4.1"]
        action = "Escalate to Procurement Excellence before processing (§4.1)."
        limitations += [
            "Vendor status is an undated snapshot: it cannot be determined whether the vendor was non-ACTIVE "
            "when each invoice was issued or processed.",
            "Processing and payment status are not in the data.",
        ]
        extra = {}

    if fx_note := _fx_assumption(sel):
        assumptions.append(fx_note)
    return ToolOutput(
        result={
            "check": p.check,
            "count": int(len(sel)),
            "total_eur": round(float(sel["amount_eur"].sum()), 2),
            "groups": groups,
            "action": action,
            "filters": applied,
            **extra,
        },
        rows=sel,
        clause_ids=clause_ids,
        assumptions=assumptions,
        limitations=limitations,
    )


def approval_tier(ctx: Context, p: ApprovalTierParams) -> ToolOutput:
    pol, fx = ctx.policy, ctx.fx
    assumptions: list[str] = []
    currency = (p.currency or "EUR").upper()
    if p.currency is None:
        assumptions.append("No currency stated; EUR assumed.")
    supported = sorted(fx.currencies | {"EUR"})
    if currency not in supported:
        raise InvalidParams(f"Unsupported currency {currency}; supported: {supported}")

    rates: list[FxRate] = []
    if currency == "EUR":
        amount_eur, amount_eur_range = round(p.amount, 2), None
    elif p.invoice_date:
        rate = fx.lookup(currency, p.invoice_date)  # FxRateMissing -> error
        rates.append(rate)
        amount_eur, amount_eur_range = to_eur(p.amount, rate), None
        note = f"Converted at the ECB rate of {rate.rate} {currency}/EUR"
        note += f" from {rate.rate_date} (most recent prior to {p.invoice_date})." if rate.is_fallback else f" on {rate.rate_date}."
        assumptions.append(note)
    else:
        lo, hi = fx.rate_range(currency)
        eur_low, eur_high = to_eur(p.amount, hi), to_eur(p.amount, lo)
        t_low, t_high = pol.tier_for(eur_low), pol.tier_for(eur_high)
        if t_low.role != t_high.role:
            raise NeedsClarification(
                f"Which invoice date should be used to convert {currency} {p.amount:,.2f}? Across the ECB rates on file "
                f"it converts to {eur(eur_low)}–{eur(eur_high)}, which spans two approval tiers "
                f"({t_low.role} and {t_high.role})."
            )
        rates += [hi, lo]
        amount_eur, amount_eur_range = None, [eur_low, eur_high]
        assumptions.append(
            f"No invoice date stated. At the highest and lowest ECB rates on file ({hi.rate} on {hi.rate_date}, "
            f"{lo.rate} on {lo.rate_date}) the amount converts to {eur(eur_low)}–{eur(eur_high)}, all in the same tier."
        )

    tier = pol.tier_for(amount_eur if amount_eur is not None else amount_eur_range[0])
    return ToolOutput(
        result={
            "amount": p.amount,
            "currency": currency,
            "amount_eur": amount_eur,
            "amount_eur_range": amount_eur_range,
            "required_role": tier.role,
            "tier_label": tier.label,
        },
        clause_ids=["3"] + (["5.1"] if rates else []),
        fx_rates=rates,
        assumptions=assumptions,
        limitations=[
            "The policy assigns approval to roles. The data does not map people to roles, so no individual can be "
            "named as authorised."
        ],
    )


def _text(value: Any) -> str | None:
    """A text field, or None when missing. Missing values load as NaN, and NaN is truthy in Python."""
    return value if isinstance(value, str) and value.strip() else None


def invoice_check(ctx: Context, p: InvoiceCheckParams) -> ToolOutput:
    df = ctx.data.invoices
    row_df = df[df["invoice_number"] == p.invoice_number]
    if row_df.empty:
        raise NotFound(f"{p.invoice_number} is not in the invoice data.")
    require_eur(row_df)
    r = row_df.iloc[0]
    po, approver = _text(r["po_number"]), _text(r["approved_by"])  # a missing value loads as NaN, which is truthy
    pol = ctx.policy
    checks: list[dict[str, str]] = []
    required_role = None

    if r["amount"] < 0:
        checks.append({
            "clause": "6.1", "rule": "Credit note matched to original invoice", "result": "cannot_determine",
            "detail": "The data has no reference from a credit note to its original invoice.",
        })
    else:
        required_role = pol.tier_for(r["amount_eur"]).role
        if r["amount_eur"] >= pol.po_threshold_eur:
            checks.append({
                "clause": "2.1", "rule": f"PO required at {eur(pol.po_threshold_eur)} or above",
                "result": "ok" if po else "flag",
                "detail": f"PO number recorded: {po} (validity and issue date cannot be checked)."
                if po else
                "No PO number: potential maverick spend. Blanket PO (§2.3) or emergency exception (§9.1) cannot be checked.",
            })
        else:
            checks.append({"clause": "2.1", "rule": "PO required", "result": "not_applicable",
                           "detail": f"Below the {eur(pol.po_threshold_eur)} PO threshold."})
        checks.append({
            "clause": "3.1", "rule": "Approver recorded", "result": "ok" if approver else "flag",
            "detail": f"Recorded approver: {approver}." if approver else
            "No approver recorded: hold, may not be paid.",
        })
        checks.append({
            "clause": "3", "rule": f"Approver has authority (required role: {required_role})", "result": "cannot_determine",
            "detail": "The data does not map approvers to roles, so the approver's authority cannot be verified.",
        })
    checks.append({
        "clause": "4.1", "rule": "Vendor is ACTIVE",
        "result": "ok" if r["vendor_status"] == "ACTIVE" else "flag",
        "detail": f"Vendor status {r['vendor_status']} in the vendor master (undated snapshot)."
        + ("" if r["vendor_status"] == "ACTIVE" else " Escalate to Procurement Excellence before processing."),
    })
    checks.append({
        "clause": "7.2", "rule": "No self-approval / segregation of duties", "result": "cannot_determine",
        "detail": "The data has no requester, so segregation of duties cannot be checked.",
    })

    for c in checks:
        c["label"] = RESULT_LABELS[c["result"]]

    assumptions = []
    rates = []
    if r["currency"] != "EUR":
        rate = ctx.fx.lookup(r["currency"], r["invoice_date"])
        rates.append(rate)
        assumptions.append(
            f"Converted {r['currency']} {r['amount']:,.2f} at the ECB rate of {rate.rate} on {rate.rate_date}"
            + (f" (most recent prior to {r['invoice_date']})." if rate.is_fallback else ".")
        )
    return ToolOutput(
        result={
            "invoice_number": r["invoice_number"],
            "amount": float(r["amount"]),
            "currency": r["currency"],
            "amount_eur": float(r["amount_eur"]),
            "approved_by": approver,
            "required_role": required_role,
            "po_threshold_eur": pol.po_threshold_eur,
            "checks": checks,
        },
        rows=row_df,
        clause_ids=list(dict.fromkeys(c["clause"] for c in checks)) + (["5.1"] if rates else []),
        fx_rates=rates,
        assumptions=assumptions,
        limitations=[c["detail"] for c in checks if c["result"] == "cannot_determine"],
    )


def policy_clauses(ctx: Context, p: PolicyClausesParams) -> ToolOutput:
    unknown = [c for c in p.clause_ids if c not in ctx.policy.clauses]
    if unknown:
        raise InvalidParams(f"Unknown policy clause id(s): {unknown}")
    ids = list(dict.fromkeys(p.clause_ids))
    return ToolOutput(result={"clause_ids": ids}, clause_ids=ids)


TOOLS = {
    "spend": spend,
    "exceptions": exceptions,
    "approval_tier": approval_tier,
    "invoice_check": invoice_check,
    "policy_clauses": policy_clauses,
}
