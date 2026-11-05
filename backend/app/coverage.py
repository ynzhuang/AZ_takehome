"""Deterministic check that no recognisable condition in the question is silently dropped.

An interpretation can be valid (every parameter exists in the data) and still leave out part of the question:
"spend with BioReagent Direct" read as total spend, or "invoices over EUR 50,000 without a PO" read as every
missing-PO invoice. That is a confident answer to a broader question. Before a data tool runs, this module finds
conditions it can recognise from the data catalog and checks that the parameters express each one:

- the tool has a filter for the condition, but it was not applied  -> clarification (the analyst can rephrase);
- the tool cannot apply the condition at all (amount or count thresholds, approvers, specific invoice numbers,
  a country or currency filter on an exception list) or the year is outside the data -> decline.

Recognised: vendor names and IDs, countries, cost centres, non-EUR currencies, approvers, invoice numbers,
amount/count thresholds and years. Anything else (e.g. "late invoices") still depends on the interpreter's prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .tools import Context, eur

DATA_TOOLS = {"spend", "exceptions"}  # tools whose answer is a set of invoices, so a dropped filter widens it


@dataclass(frozen=True)
class Missed:
    text: str  # as found in the question
    reason: str  # shown to the analyst
    expressible: bool  # the chosen tool has a filter for it (rephrasing can help)
    out_of_period: bool = False


_WORD_OPS = (r"at or above|at least|more than|greater than|in excess of|exceeding|over|above|"
             r"fewer than|less than|at most|up to|under|below")
_AFTER_OPS = r"or more|or above|or higher|and above|and over|or less|or below|or lower|and below|plus"
_AMOUNT = r"(?P<ccy>eur|usd|gbp|€|\$|£)?\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<mult>k|m)?(?![\w])\s?(?P<ccy2>eur|usd|gbp)?"
_THRESHOLDS = [
    re.compile(rf"(?<![\w])(?P<op>{_WORD_OPS})\s+{_AMOUNT}"),
    re.compile(rf"(?P<op>>=|<=|>|<)\s*{_AMOUNT}"),
    re.compile(rf"{_AMOUNT}\s*(?P<op>{_AFTER_OPS})(?![\w])"),
]
_SYMBOL = {"€": "EUR", "$": "USD", "£": "GBP"}
_GROUP_FOR = {"vendor_ids": "vendor", "countries": "country", "cost_centers": "cost_center", "currencies": "currency"}


def _mentioned(text: str, alias: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", text) is not None


def _threshold_misses(q: str, tool: str, params: Any, ctx: Context) -> list[Missed]:
    misses, seen = [], set()
    for pattern in _THRESHOLDS:
        for m in pattern.finditer(q):
            if any(a <= m.start() < b for a, b in seen):
                continue
            seen.add((m.start(), m.end()))
            raw, op = m.group("num"), m.group("op")
            ccy = m.group("ccy") or m.group("ccy2")
            ccy = _SYMBOL.get(ccy, ccy.upper()) if ccy else None
            value = float(raw.replace(",", "")) * {"k": 1e3, "m": 1e6}.get(m.group("mult") or "", 1)
            if ccy is None and "," not in raw and not m.group("mult") and 1900 <= value <= 2100 and value.is_integer():
                continue  # a year ("over 2025"), handled by the year check
            upwards = op not in {"fewer than", "less than", "at most", "up to", "under", "below",
                                 "or less", "or below", "or lower", "and below", "<=", "<"}
            # The PO rule's own threshold is what check="missing_po" applies; the answer states it as
            # "EUR 1,000.00 or above (§2.1)", so either "over" or "or more" wording is covered.
            if (tool == "exceptions" and getattr(params, "check", None) == "missing_po" and upwards
                    and ccy in (None, "EUR") and value == ctx.policy.po_threshold_eur):
                continue
            text = m.group(0).strip()
            misses.append(Missed(text, f"'{text}': amount or count thresholds can't be applied (only the PO rule's "
                                       f"{eur(ctx.policy.po_threshold_eur)} threshold is supported)", False))
    return misses


def missed_conditions(question: str, tool: str, params: Any, ctx: Context) -> list[Missed]:
    """Conditions recognised in the question that the parameters do not express. Empty list = covered."""
    if tool not in DATA_TOOLS:
        return []
    q = question.lower()
    catalog = ctx.data.catalog()
    misses: list[Missed] = []

    def need(found: str, param: str, value: str, label: str) -> None:
        # A full breakdown by the same dimension (no top-N cut-off) shows this value's own figure: nothing dropped.
        if getattr(params, "group_by", None) == _GROUP_FOR.get(param) and not getattr(params, "top_n", None):
            return
        if not hasattr(params, param):
            misses.append(Missed(found, f"'{found}': this list can't be filtered by {label}", False))
        elif value not in (getattr(params, param) or []):
            misses.append(Missed(found, f"'{found}' is in the question but no {label} filter for it was applied", True))

    for vendor_id, name in zip(ctx.data.vendors["vendor_id"], ctx.data.vendors["vendor_name"]):
        aliases = {vendor_id.lower(), name.lower(), name.split()[0].lower()}
        found = next((a for a in sorted(aliases, key=len, reverse=True) if len(a) >= 5 and _mentioned(q, a)), None)
        if found:
            need(f"{name} ({vendor_id})", "vendor_ids", vendor_id, "vendor")
    for country in catalog["countries"]:
        if _mentioned(q, country.lower()):
            need(country, "countries", country, "country")
    for cc in sorted(set(re.findall(r"(?<![\w-])cc-[a-z]+-\d+(?![\w-])", q))):
        if cc.upper() in catalog["cost_centers"]:
            need(cc.upper(), "cost_centers", cc.upper(), "cost centre")
        else:
            misses.append(Missed(cc.upper(), f"'{cc.upper()}' is not a cost centre in the data", False))
    for ccy in (c for c in catalog["currencies"] if c != "EUR"):
        for m in re.finditer(rf"(?<![\w-]){ccy.lower()}(?![\w-])", q):
            if q[:m.start()].rstrip()[-1:].isdigit() or q[m.end():].lstrip()[:1].isdigit():
                continue  # part of an amount ("USD 55,000"), not a currency filter
            need(ccy, "currencies", ccy, "currency")
            break
    approvers = sorted(set(ctx.data.invoices["approved_by"].dropna()))
    for approver in approvers:
        if re.search(rf"(?<![\w.]){re.escape(approver.lower())}(?![\w.])", q):
            misses.append(Missed(approver, f"'{approver}': invoices can't be filtered by approver", False))
    for inv in sorted(set(re.findall(r"(?<![\w-])inv-\d+(?![\w-])", q))):
        misses.append(Missed(inv.upper(), f"'{inv.upper()}': a list can't be limited to specific invoices "
                                          "(ask about one invoice at a time)", False))
    misses += _threshold_misses(q, tool, params, ctx)
    data_year = ctx.data.period_start.year
    for year in sorted(set(re.findall(r"(?<![\w-])((?:19|20)\d{2})(?!\d)", q))):
        if int(year) != data_year:
            misses.append(Missed(year, f"'{year}': the data covers {data_year} only", False, out_of_period=True))
    return misses
