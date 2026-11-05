"""Deterministic answer text. No LLM writes answer prose: every figure comes from tool output."""

from __future__ import annotations

from typing import Any

from .policy import Policy
from .tools import eur

# (singular, plural) for each group_by value
_GROUP_NAMES = {
    "vendor": ("vendor", "vendors"), "country": ("country", "countries"),
    "category": ("vendor category", "vendor categories"), "currency": ("currency", "currencies"),
    "vendor_status": ("vendor status", "vendor statuses"), "cost_center": ("cost centre", "cost centres"),
}


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _filters_text(filters: dict[str, Any]) -> str:
    parts = [f"{k.replace('_', ' ')} = {', '.join(v)}" for k, v in filters.items() if k != "period"]
    return f" ({'; '.join(parts)})" if parts else ""


def render_spend(r: dict[str, Any]) -> str:
    scope = _filters_text(r["filters"])
    if r["invoice_count"] == 0:
        return f"No invoices match{scope}. Net spend: {eur(0)}."
    if r["group_by"] == "none":
        return f"Net spend{scope}: {eur(r['total_eur'])} across {_n(r['invoice_count'], 'invoice')}."
    singular, name = _GROUP_NAMES[r["group_by"]]
    head = f"Top {len(r['groups'])} {name} by net spend" if r["top_n"] else f"Net spend by {singular}"
    lines = [f"{head}{scope}:"]
    for i, g in enumerate(r["groups"], 1):
        lines.append(f"{i}. {g['label']}: {eur(g['total_eur'])} ({_n(g['invoice_count'], 'invoice')})")
    if r["top_n"]:
        share = r["shown_total_eur"] / r["total_eur"] * 100 if r["total_eur"] else 0
        lines.append(
            f"These {len(r['groups'])} of {r['group_count']} {name} account for {eur(r['shown_total_eur'])} "
            f"of {eur(r['total_eur'])} total net spend ({share:.1f}%)."
        )
    else:
        lines.append(f"Total: {eur(r['total_eur'])} across {_n(r['invoice_count'], 'invoice')}.")
    return "\n".join(lines)


_CHECK_TITLES = {
    "missing_po": "invoices at or above the PO threshold with no PO number (potential maverick spend)",
    "missing_approver": "invoices with no approver recorded",
    "non_active_vendor": "invoices from vendors that are not ACTIVE",
}


def render_exceptions(r: dict[str, Any]) -> str:
    scope = _filters_text(r["filters"])
    if r["count"] == 0:
        return f"No {_CHECK_TITLES[r['check']]} found{scope}."
    by = "vendor" if r["check"] == "non_active_vendor" else "cost centre"
    lines = [f"{r['count']} {_CHECK_TITLES[r['check']]}{scope}, totalling {eur(r['total_eur'])}.", f"By {by}:"]
    lines += [f"- {g['label']}: {_n(g['invoice_count'], 'invoice')}, {eur(g['total_eur'])}" for g in r["groups"]]
    if r.get("also_missing_po"):
        lines.append(f"{len(r['also_missing_po'])} of these also have no PO number: {', '.join(r['also_missing_po'])}.")
    lines.append(f"Required action: {r['action']}")
    return "\n".join(lines)


def render_approval_tier(r: dict[str, Any]) -> str:
    amount = f"{r['currency']} {r['amount']:,.2f}"
    if r["currency"] == "EUR":
        basis = amount
    elif r["amount_eur"] is not None:
        basis = f"{amount} ({eur(r['amount_eur'])})"
    else:
        lo, hi = r["amount_eur_range"]
        basis = f"{amount} ({eur(lo)}–{eur(hi)} depending on the rate date)"
    return (
        f"An invoice of {basis} requires approval by: {r['required_role']} "
        f"(policy §3, tier \"{r['tier_label']}\")."
    )


_RESULT_WORD = {"ok": "NO ISSUE FOUND", "flag": "FLAG", "cannot_determine": "CANNOT DETERMINE", "not_applicable": "N/A"}


def render_invoice_check(r: dict[str, Any]) -> str:
    amount = f"{r['currency']} {r['amount']:,.2f}"
    if r["currency"] != "EUR":
        amount += f" ({eur(r['amount_eur'])})"
    head = f"{r['invoice_number']}, {amount}."
    if r["required_role"]:
        head += f" Required approver under §3: {r['required_role']}."
    lines = [head]
    lines += [f"- [{_RESULT_WORD[c['result']]}] §{c['clause']} {c['rule']}: {c['detail']}" for c in r["checks"]]
    if any(c["result"] == "cannot_determine" for c in r["checks"]):
        lines.append("Items marked CANNOT DETERMINE need information that is not in the data.")
    return "\n".join(lines)


def render_policy_clauses(r: dict[str, Any], policy: Policy) -> str:
    lines = [f"From {policy.version_label}:"]
    for cid in r["clause_ids"]:
        lines.append(f"§{cid} ({policy.clauses[cid].section}): {policy.clauses[cid].text}")
    return "\n".join(lines)


DECLINE_TEXT = {
    "outside_data_period": "I can't answer this: the question is about a period the invoice data does not cover.",
    "data_not_available": "I can't answer this from the available data.",
    "unsupported_question": "I can't answer this with the analyses this assistant supports.",
    "not_finance_related": "This assistant only answers questions about the 2025 invoice data and the procurement policy.",
}


def render(tool: str, result: dict[str, Any], policy: Policy) -> str:
    if tool == "spend":
        return render_spend(result)
    if tool == "exceptions":
        return render_exceptions(result)
    if tool == "approval_tier":
        return render_approval_tier(result)
    if tool == "invoice_check":
        return render_invoice_check(result)
    if tool == "policy_clauses":
        return render_policy_clauses(result, policy)
    raise KeyError(tool)
