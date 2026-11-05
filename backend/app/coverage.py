"""Deterministic check that no recognisable condition in the question is silently dropped.

An interpretation can be valid (every parameter exists in the data) and still leave out part of the question:
"spend with BioReagent Direct" read as total spend, or "invoices over EUR 50,000 without a PO" read as every
missing-PO invoice. That is a confident answer to a broader question. Before a data tool runs, this module finds
conditions it can recognise from the data catalog and checks that the parameters express each one:

- the tool has a filter for the condition, but it was not applied  -> clarification (the analyst can rephrase);
- the tool cannot apply the condition at all (amount or count thresholds, approvers, specific invoice numbers,
  a country or currency filter on an exception list) or the year is outside the data -> decline.

Recognised: vendor names and IDs, vendor statuses, the missing-PO and missing-approver checks, countries, cost
centres, non-EUR currencies, approvers, invoice numbers, amount/count thresholds, years (including "this year"), credit
notes, payments, and rankings by a dimension the tool does not group by. Anything else (e.g. "late invoices") still depends on the interpreter's prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .tools import Context, eur

DATA_TOOLS = {"spend", "exceptions"}  # tools whose answer is a set of invoices, so a dropped filter widens it


@dataclass(frozen=True)
class Missed:
    text: str  # as found in the question
    reason: str  # shown to the analyst
    expressible: bool  # the chosen tool has a filter for it (rephrasing can help)
    out_of_period: bool = False
    options: tuple[str, ...] = ()  # complete rewritten questions, when the condition itself is ambiguous


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

# No tool totals or lists credit notes on their own; spend is net of them, so "including credit notes" is covered.
_CREDIT = re.compile(r"(?<![\w-])credit[- ]?(?:notes?|memos?)(?![\w-])")
_CREDIT_INCLUDED = re.compile(r"(?:including|incl\.?|net of|after)\s+(?:the\s+|our\s+|all\s+|any\s+)?credit[- ]?(?:notes?|memos?)")
# There is no payment data. Policy wording such as "may not be paid" (§3.1) describes a rule, not a payment status.
_PAYMENT = re.compile(r"(?<![\w-])(?:paid|unpaid|pay|pays|paying|payments?|payables?|settled|outstanding|overdue)(?![\w-])")
_POLICY_PAY = re.compile(r"(?:must|may|can|cannot|can't|should|could)\s+(?:not\s+)?be\s+paid")
# Rankings or breakdowns by a dimension: must match how the chosen tool groups its result.
_DIMENSIONS = {"vendor": r"vendors?|suppliers?", "country": r"countr(?:y|ies)", "category": r"categor(?:y|ies)",
               "currency": r"currenc(?:y|ies)", "cost_center": r"cost[- ]cent(?:re|er)s?"}
_RANKING_FORMS = [r"(?:which|what)\s+(?:top\s+)?(?:\d+\s+)?(?:DIM)", r"(?:by|per|each|every)\s+(?:DIM)",
                  r"top\s+(?:\d+\s+)?(?:DIM)", r"(?:DIM)\s+(?:with|has|have|had)\s+the\s+(?:most|highest|largest|biggest|fewest|lowest)",
                  r"rank(?:ing|ed|s)?\s+(?:of\s+)?(?:the\s+|our\s+)?(?:DIM)"]
_RANKINGS = {dim: [re.compile(r"(?<![\w-])" + form.replace("DIM", words) + r"(?![\w-])") for form in _RANKING_FORMS]
             for dim, words in _DIMENSIONS.items()}
# Vendor status (third cold review: "inactive vendors ... missing a PO" was answered for all vendors). Only status
# words that describe vendors count: "on hold" alone can mean an invoice held under §3.1. Lowercase "inactive" alone
# is ambiguous (status INACTIVE, or every vendor that is not ACTIVE); the status code "INACTIVE" or "inactive status"
# is not.
_STATUS_WORD = r"(?:inactive|on[ _-]hold|non[ -]?active|not active)"
_STATUS_LIST = rf"{_STATUS_WORD}(?:\s*(?:,|/|\bor\b|\band\b)\s*{_STATUS_WORD})*"
_VENDOR_WORD = r"(?:vendors?|suppliers?)"
_STATUS_PHRASES = [
    re.compile(rf"(?<![\w-]){_STATUS_LIST}\)?(?:[- ]status)?\s+{_VENDOR_WORD}(?![\w-])"),
    re.compile(rf"(?<![\w-]){_VENDOR_WORD}\s+(?:(?:that|which|who)\s+)?(?:are|is|were|was)\s+(?:currently\s+)?"
               rf"{_STATUS_LIST}(?![\w-])"),
    re.compile(rf"(?<![\w-])(?:status(?:es)?\s+(?:of\s+)?{_STATUS_LIST}|{_STATUS_LIST}\s+status)(?![\w-])"),
]
# Checks the question names: "no PO" / "without an approver" must be the exception list's check.
_CHECK_PHRASES = {
    "missing_po": (re.compile(r"(?<![\w-])(?:no|without|missing|lacking)\s+(?:an?\s+|the\s+|any\s+)?(?:po|purchase order)s?"
                              r"(?![\w-])|(?<![\w-])(?:missing|no)[- _]po(?![\w-])"), "invoices without a PO"),
    "missing_approver": (re.compile(r"(?<![\w-])(?:no|without|missing|lacking)\s+(?:an?\s+|the\s+|any\s+)?"
                                    r"(?:approver|approval)s?(?![\w-])|(?<![\w-])missing[- _]approver(?![\w-])"),
                         "invoices without an approver"),
}
_RELATIVE_YEARS = {"this year": 0, "current year": 0, "year to date": 0, "ytd": 0, "next year": 1,
                   "last year": -1, "previous year": -1}


def _today() -> date:
    return date.today()


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


def _first_outside(pattern: re.Pattern, q: str, allowed: re.Pattern) -> str | None:
    spans = [m.span() for m in allowed.finditer(q)]
    return next((m.group(0) for m in pattern.finditer(q) if not any(a <= m.start() < b for a, b in spans)), None)


def _status_misses(question: str, tool: str, params: Any, ctx: Context) -> list[Missed]:
    """Vendor-status conditions must be expressed with the existing filters: vendor_statuses for spend, the vendor IDs
    with that status for exception lists. Ambiguous "inactive" is clarified with both readings as options."""
    q = question.lower()
    spans = [m.span() for p in _STATUS_PHRASES for m in p.finditer(q)]
    if not spans:
        return []
    texts = [question[a:b] for a, b in spans]
    joined = " ".join(t.lower() for t in texts)
    inactive, on_hold = "inactive" in joined, re.search(r"on[ _-]hold", joined) is not None
    not_active = re.search(r"non[ -]?active|not active", joined) is not None
    vendors = ctx.data.vendors
    names = lambda statuses: ", ".join(vendors.loc[vendors["status"].isin(statuses), "vendor_name"])  # noqa: E731
    if not_active or (inactive and on_hold):
        wanted = {"INACTIVE", "ON_HOLD"}
    elif on_hold:
        wanted = {"ON_HOLD"}
    elif any("INACTIVE" in t or "status" in t.lower() for t in texts):
        wanted = {"INACTIVE"}
    else:  # lowercase "inactive" alone: ask which reading is meant
        # Every "inactive" (any case, e.g. "Inactive vendors…") is rewritten, so no option is ambiguous again;
        # the status code "INACTIVE" is kept as it is.
        rewrite = lambda words: re.sub(r"(?<![\w-])inactive(?![\w-])",  # noqa: E731
                                       lambda m: m.group(0) if m.group(0) == "INACTIVE" else words, question, flags=re.I)
        return [Missed("inactive", f"'inactive' can mean vendors with status INACTIVE ({names({'INACTIVE'})}), or "
                                   f"every vendor that is not ACTIVE (INACTIVE or ON_HOLD: "
                                   f"{names({'INACTIVE', 'ON_HOLD'})}). Which do you mean?", True,
                       options=(rewrite("INACTIVE"), rewrite("non-active (INACTIVE or ON_HOLD)")))]
    ids = set(vendors.loc[vendors["status"].isin(wanted), "vendor_id"])
    label = " or ".join(sorted(wanted))
    if tool == "spend":
        covered = (set(params.vendor_statuses) == wanted or set(params.vendor_ids) == ids
                   or (params.group_by == "vendor_status" and not params.top_n))
    else:
        covered = set(params.vendor_ids) == ids or (params.check == "non_active_vendor" and wanted == {"INACTIVE", "ON_HOLD"})
    if covered:
        return []
    return [Missed(texts[0], f"'{texts[0]}' is in the question but no filter for vendor status {label} was applied", True)]


def _check_misses(question: str, tool: str, params: Any) -> list[Missed]:
    """A check named in the question ("no PO", "without an approver") must be the exception list's check."""
    q = question.lower()
    named = [(check, m.group(0), label) for check, (rx, label) in _CHECK_PHRASES.items() if (m := rx.search(q))]
    misses = []
    for check, text, label in named:
        if tool == "exceptions" and getattr(params, "check", None) == check:
            continue
        if tool == "exceptions" and len(named) == 1:
            misses.append(Missed(text, f"'{text}' is in the question but this list does not apply it", True))
        else:
            misses.append(Missed(text, f"'{text}': this answer can't be limited to {label}", False))
    return misses


def missed_conditions(question: str, tool: str, params: Any, ctx: Context, today: date | None = None) -> list[Missed]:
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

    vendor_named = False
    for vendor_id, name in zip(ctx.data.vendors["vendor_id"], ctx.data.vendors["vendor_name"]):
        aliases = {vendor_id.lower(), name.lower(), name.split()[0].lower()}
        found = next((a for a in sorted(aliases, key=len, reverse=True) if len(a) >= 5 and _mentioned(q, a)), None)
        if found:
            vendor_named = True
            need(f"{name} ({vendor_id})", "vendor_ids", vendor_id, "vendor")
    if not vendor_named:
        misses += _status_misses(question, tool, params, ctx)
    misses += _check_misses(question, tool, params)
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
    current = (today or _today()).year
    for phrase, offset in _RELATIVE_YEARS.items():
        if _mentioned(q, phrase) and current + offset != data_year:
            misses.append(Missed(phrase, f"'{phrase}' means {current + offset}; the data covers {data_year} only",
                                 False, out_of_period=True))
    if found := _first_outside(_CREDIT, q, _CREDIT_INCLUDED):
        misses.append(Missed(found, f"'{found}': credit notes can't be totalled or listed on their own "
                                    "(spend always includes them, net)", False))
    if found := _first_outside(_PAYMENT, q, _POLICY_PAY):
        misses.append(Missed(found, f"'{found}': payment status and paid amounts are not in the data "
                                    "(spend is invoiced, not paid)", False))
    grouped = (getattr(params, "group_by", None) if tool == "spend"
               else "vendor" if getattr(params, "check", None) == "non_active_vendor" else "cost_center")
    for dim, patterns in _RANKINGS.items():
        match = next((m for p in patterns for m in [p.search(q)] if m), None)
        if match and grouped != dim:
            asked, label = match.group(0), dim.replace("_", " ").replace("center", "centre")
            if tool == "spend":
                misses.append(Missed(asked, f"'{asked}' asks for a breakdown by {label}, but none was applied", True))
            else:
                misses.append(Missed(asked, f"'{asked}': this list is grouped by {grouped.replace('_', ' ').replace('center', 'centre')} "
                                            f"and can't be ranked by {label}", False))
    return misses
