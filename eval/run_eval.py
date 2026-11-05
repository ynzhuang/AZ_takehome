"""Evaluation runner (design §3).

Levels:
  1. tools  - deterministic tools called directly with canonical parameters, compared with the oracle (no LLM)
  2. llm    - the structured interpretation returned by /ask, compared with the expected interpretation
  3. e2e    - full /ask responses: status, figures, evidence rows, citations, limitations, forbidden claims
  + T16     - failure injection (timeout, malformed tool call) with mocked HTTP; /summary must still work

Each case runs N times (default 3); the worst run counts. Results are written to eval/results/.

Usage (from the repository root, with the backend venv):
  backend/.venv/bin/python eval/run_eval.py            # uses LLM settings from .env / environment
  backend/.venv/bin/python eval/run_eval.py --runs 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval"))

from fastapi.testclient import TestClient  # noqa: E402

import oracle  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.llm import OpenAICompatibleInterpreter, build_tools  # noqa: E402
from app.main import build_service, create_app  # noqa: E402
from app.schemas import TOOL_PARAMS  # noqa: E402
from app.tools import TOOLS  # noqa: E402

CRITICAL, MAJOR = "critical", "major"
SEVERITY_RANK = {None: 0, "minor": 1, MAJOR: 2, CRITICAL: 3}

GLOBAL_FORBIDDEN = [
    r"\b(is|are|was|were) (fully )?compliant\b", r"\bproperly approved\b", r"(?<!not )\bconfirmed violations?\b",
    r"\bviolat(ed|es)\b", r"\bapproval was (sufficient|valid|adequate)\b", r"\bhad (sufficient )?authority\b",
]

# --------------------------------------------------------------------------- cases

CASES = [
    {"id": "T1", "question": "What are our top 5 vendors by total spend in 2025?", "status": "answered",
     "tool": "spend", "params": {"group_by": "vendor", "top_n": 5}, "rows": "T1"},
    {"id": "T1-v", "variant_of": "T1", "question": "Which 5 suppliers did we spend the most with last year?",
     "status": "answered", "tool": "spend", "params": {"group_by": "vendor", "top_n": 5}, "rows": "T1"},
    {"id": "T2", "question": "What is our total spend with vendors based in Spain?", "status": "answered",
     "tool": "spend", "params": {"countries": ["Spain"]}, "rows": "T2", "required_limitations": [r"INACTIVE"]},
    {"id": "T3", "question": "Who is allowed to approve an invoice of EUR 60,000?", "status": "answered",
     "tool": "approval_tier", "params": {"amount": 60000}, "required_limitations": [r"no individual can be named"],
     "forbid_approver_names": True},
    {"id": "T3-v", "variant_of": "T3", "question": "Who signs off on a 60k invoice?", "status": "answered",
     "tool": "approval_tier", "params": {"amount": 60000}, "required_limitations": [r"no individual can be named"],
     "required_assumptions_if_no_currency": [r"EUR assumed"], "forbid_approver_names": True},
    {"id": "T4", "question": "Which invoices of EUR 1,000 or more have no PO, and who should they be flagged to?",
     "status": "answered", "tool": "exceptions", "params": {"check": "missing_po"}, "rows": "T4",
     "required_clauses": ["2.1", "2.2"],
     "required_limitations": [r"potential exceptions, not confirmed violations", r"blanket", r"[Cc]ost-centre owners are not in the data"]},
    {"id": "T5", "question": "What is our consulting spend?", "status": "clarification_needed", "tool": "clarify"},
    {"id": "T5-v", "variant_of": "T5", "question": "How much went on consulting?", "status": "clarification_needed",
     "tool": "clarify"},
    {"id": "T6", "question": "Was INV-20008 approved by someone with sufficient authority?", "status": "answered",
     "tool": "invoice_check", "params": {"invoice_number": "INV-20008"}, "rows": "T6",
     "required_limitations": [r"authority cannot be verified"]},
    {"id": "T8", "question": "What was our total spend in 2024?", "status": "declined", "tool": "decline",
     "alt_tools": ["spend"]},  # a spend call with 2024 dates is declined by the period guard
    {"id": "T11", "question": "Which invoices are missing an approver?", "status": "answered", "tool": "exceptions",
     "params": {"check": "missing_approver"}, "rows": "T11", "required_clauses": ["3.1"]},
    # Follow-up regression (found in manual testing): the second question depends on the first.
    # Correct: the missing-PO list for CC-FIN-100 (10 invoices), or a clarification question.
    # Answering anything else (e.g. all CC-FIN-100 spend) is critical: a confident answer to a different question.
    {"id": "T17", "turns": ["Which invoices of EUR 1,000 or more have no PO?"],
     "question": "How about just the invoices from CC-FIN-100?", "status": "answered",
     "alt_status": ["clarification_needed"], "tool": "exceptions",
     "params": {"check": "missing_po", "cost_centers": ["CC-FIN-100"]}, "rows": "T17", "required_clauses": ["2.1", "2.2"]},
]

# --------------------------------------------------------------------------- checks


def _numbers(obj) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numbers(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in _numbers(v)]
    return []


def check_e2e(case: dict, r: dict, exp: dict) -> list[dict]:
    """Return a list of findings: {check, severity, layer, detail}. Empty list = pass."""
    f: list[dict] = []

    def fail(check, severity, layer, detail):
        f.append({"check": check, "severity": severity, "layer": layer, "detail": detail})

    interp = r["interpretation"]
    tool, params = interp.get("tool"), interp.get("params") or {}
    want = case["status"]

    # Acceptable alternative outcome (e.g. a follow-up that may be clarified instead of answered)
    if r["status"] != want and r["status"] in case.get("alt_status", []):
        if r["result"] or r["evidence"]["row_count"]:
            fail("no_partial_answer", CRITICAL, "system", "non-answer carries a result or evidence")
        return f

    # Status / behaviour
    if r["status"] != want:
        if want in ("clarification_needed", "declined") and r["status"] == "answered":
            fail("status", CRITICAL, "interpretation", f"answered instead of {want}")
        elif want == "answered" and r["status"] == "clarification_needed":
            fail("status", MAJOR, "interpretation", "unnecessary clarification")
        elif r["status"] == "error":
            fail("status", MAJOR, "interpretation" if r["error"]["code"] == "invalid_params" else "llm",
                 f"error {r['error']['code']}: {r['error']['message']}")
        else:
            fail("status", CRITICAL, "interpretation", f"status {r['status']} (expected {want})")

    # LLM interpretation
    if tool != case["tool"] and tool not in case.get("alt_tools", []):
        fail("interpretation.tool", MAJOR, "interpretation", f"tool {tool} (expected {case['tool']})")
    for k, v in case.get("params", {}).items():
        if params.get(k) != v:
            fail("interpretation.params", MAJOR, "interpretation", f"{k}={params.get(k)!r} (expected {v!r})")
    if case["tool"] == "spend" and tool == "spend":
        extra = {k: v for k, v in params.items() if k not in case.get("params", {}) and k not in ("date_from", "date_to", "group_by")}
        if extra:
            fail("interpretation.params", MAJOR, "interpretation", f"unexpected filters {extra}")
        for d in ("date_from", "date_to"):
            if d in params and params[d] not in ("2025-01-01", "2025-12-31"):
                fail("interpretation.params", MAJOR, "interpretation", f"{d}={params[d]} narrows the period")
    if case["tool"] == "approval_tier" and tool == "approval_tier" and params.get("currency") not in (None, "EUR"):
        fail("interpretation.params", MAJOR, "interpretation", f"currency={params.get('currency')}")

    if r["status"] != "answered":
        if want == "clarification_needed" and r["status"] == want and len((r.get("clarification") or {}).get("options", [])) < 2:
            fail("clarification.options", MAJOR, "interpretation", "fewer than 2 rewritten options")
        if r["status"] != "answered" and (r.get("result") or r["evidence"]["row_count"]):
            fail("no_partial_answer", CRITICAL, "system", "non-answer carries a result or evidence")
        return f

    res, ev = r["result"] or {}, r["evidence"]
    rows = {row["invoice_number"] for row in ev["rows"]}

    # Figures and evidence (critical)
    key = case.get("rows")
    if key:
        if rows != exp[key]["rows"]:
            fail("evidence.rows", CRITICAL, "system", f"missing {sorted(exp[key]['rows'] - rows)[:5]}, extra {sorted(rows - exp[key]['rows'])[:5]}")
    if key == "T1":
        got = [(g["key"], g["total_eur"]) for g in res.get("groups", [])]
        if [k for k, _ in got] != [k for k, _ in exp["T1"]["top5"]] or any(abs(a[1] - b[1]) > 0.01 for a, b in zip(got, exp["T1"]["top5"])):
            fail("figures", CRITICAL, "system", f"top5 {got} != {exp['T1']['top5']}")
        if abs(res.get("total_eur", 0) - exp["T1"]["total_eur"]) > 0.01:
            fail("figures", CRITICAL, "system", f"total {res.get('total_eur')} != {exp['T1']['total_eur']}")
    if key in ("T2", "T4", "T11", "T17") and abs(res.get("total_eur", 0) - exp[key]["total_eur"]) > 0.01:
        fail("figures", CRITICAL, "system", f"total {res.get('total_eur')} != {exp[key]['total_eur']}")
    if key == "T11" and res.get("also_missing_po") != exp["T11"]["also_missing_po"]:
        fail("figures", CRITICAL, "system", f"also_missing_po {res.get('also_missing_po')}")
    if case["id"].startswith("T3") and res.get("required_role") != exp["T3"]["role"]:
        fail("figures", CRITICAL, "system", f"role {res.get('required_role')} != {exp['T3']['role']}")
    if key == "T6":
        if abs(res.get("amount_eur", 0) - exp["T6"]["amount_eur"]) > 0.01 or res.get("required_role") != exp["T6"]["role"]:
            fail("figures", CRITICAL, "system", f"{res.get('amount_eur')} / {res.get('required_role')}")
        auth = [c for c in res.get("checks", []) if c["clause"] == "3"]
        if not auth or auth[0]["result"] != "cannot_determine":
            fail("unsupported_conclusion", CRITICAL, "system", f"authority check: {auth}")

    # Citations: every clause and row exists; row EUR amounts match the oracle
    bad_clauses = [c["id"] for c in ev["clauses"] if c["id"] not in exp["_clause_ids"]]
    if bad_clauses:
        fail("citations.clauses", CRITICAL, "system", f"unknown clauses {bad_clauses}")
    for row in ev["rows"]:
        e = exp["_eur_by_invoice"].get(row["invoice_number"])
        if e is None or row["amount_eur"] is None or abs(e - row["amount_eur"]) > 0.01:
            fail("citations.rows", CRITICAL, "system", f"{row['invoice_number']} EUR {row['amount_eur']} vs oracle {e}")
            break
    missing_cl = [c for c in case.get("required_clauses", []) if c not in {x["id"] for x in ev["clauses"]}]
    if missing_cl:
        fail("citations.required", CRITICAL, "system", f"missing clauses {missing_cl}")

    # Answer text figures must all come from the structured result
    allowed = _numbers(res)
    for m in re.findall(r"EUR (-?[\d,]+\.\d\d)", r["answer"]):
        v = float(m.replace(",", ""))
        if not any(abs(v - a) <= 0.01 for a in allowed):
            fail("answer_consistency", CRITICAL, "display", f"EUR {m} in answer not in result")

    # Notes must describe the evidence rows (added after manual testing found top-5 notes
    # computed over the whole dataset). Counts and EUR totals stated in assumptions/limitations
    # are compared with the rows actually cited.
    if tool == "spend":
        f += [dict(x, severity=CRITICAL, layer="system") for x in notes_vs_evidence(r)]

    # Required limitations / assumptions
    lim = " ".join(r["limitations"])
    for pat in case.get("required_limitations", []):
        if not re.search(pat, lim):
            sev = CRITICAL if case["id"] in ("T4", "T6") else MAJOR  # missing caveat = unsupported conclusion there
            fail("limitations", sev, "system", f"missing limitation /{pat}/")
    if case.get("required_assumptions_if_no_currency") and params.get("currency") is None:
        for pat in case["required_assumptions_if_no_currency"]:
            if not re.search(pat, " ".join(r["assumptions"])):
                fail("assumptions", MAJOR, "system", f"missing assumption /{pat}/")

    # Forbidden claims (answer text)
    for pat in GLOBAL_FORBIDDEN:
        if re.search(pat, r["answer"], flags=re.I):
            fail("forbidden_claim", CRITICAL, "display", f"/{pat}/ in answer")
    if case.get("forbid_approver_names"):
        named = [a for a in exp["_approvers"] if a in r["answer"]]
        if named:
            fail("forbidden_claim", CRITICAL, "display", f"names approver(s) {named}")
    return f


def _eur(text: str) -> float:
    return float(text.replace(",", ""))


def notes_vs_evidence(r: dict) -> list[dict]:
    """Disclosures in assumptions/limitations must match the cited rows (not a wider set)."""
    rows = r["evidence"]["rows"]
    notes = " ".join(r["assumptions"] + r["limitations"])
    out = []

    def bad(detail):
        out.append({"check": "notes_vs_evidence", "detail": detail})

    non_active = [x for x in rows if x["vendor_status"] != "ACTIVE"]
    m = re.search(r"Includes (\d+) invoice\(s\) \(EUR (-?[\d,]+\.\d\d)\) from vendors that are not ACTIVE", notes)
    if m and (int(m[1]) != len(non_active) or abs(_eur(m[2]) - sum(x["amount_eur"] for x in non_active)) > 0.01):
        bad(f"non-ACTIVE note says {m[1]} invoices / EUR {m[2]}; evidence has {len(non_active)}")
    if non_active and not m:
        bad(f"{len(non_active)} non-ACTIVE invoices in evidence but no disclosure")
    credits = [x for x in rows if x["amount"] < 0]
    m = re.search(r"Includes (\d+) credit note\(s\) totalling EUR (-?[\d,]+\.\d\d)", notes)
    if m and (int(m[1]) != len(credits) or abs(_eur(m[2]) - sum(x["amount_eur"] for x in credits)) > 0.01):
        bad(f"credit-note note says {m[1]} / EUR {m[2]}; evidence has {len(credits)}")
    if credits and not m:
        bad(f"{len(credits)} credit notes in evidence but no disclosure")
    converted = [x for x in rows if x["currency"] != "EUR"]
    m = re.search(r"(\d+) non-EUR invoice\(s\) converted", notes)
    if m and int(m[1]) != len(converted):
        bad(f"FX note says {m[1]} converted invoices; evidence has {len(converted)}")
    return out


FREE_TEXT_PARAMS = {"question", "options", "explanation"}  # LLM wording; compared by meaning, not text


def semantic_params(params: dict | None) -> dict:
    """Params that change the result. Full-period dates are equivalent to no dates."""
    p = {k: v for k, v in (params or {}).items() if k not in FREE_TEXT_PARAMS}
    if p.get("date_from") == "2025-01-01":
        p.pop("date_from")
    if p.get("date_to") == "2025-12-31":
        p.pop("date_to")
    return p


def clarification_follow_through(c, run: dict, exp: dict) -> list[dict]:
    """Stateless clarification: each option is re-asked as-is and must be answered correctly."""
    findings = []
    options = (run.get("params") or {}).get("options", [])
    matched = set()
    for opt in options:
        body = c.post("/ask", json={"question": opt}).json()
        p = body["interpretation"].get("params") or {}
        key = "T5_category" if p.get("categories") == ["Consulting"] else "T5_description" if p.get("descriptions") else None
        if body["status"] != "answered" or body["interpretation"].get("tool") != "spend" or key is None:
            findings.append({"check": "clarification.follow_through", "severity": MAJOR, "layer": "interpretation",
                             "detail": f"option {opt!r} -> {body['status']}/{body['interpretation'].get('tool')} {p}"})
            continue
        matched.add(key)
        rows = {r["invoice_number"] for r in body["evidence"]["rows"]}
        if abs(body["result"]["total_eur"] - exp[key]["total_eur"]) > 0.01 or rows != exp[key]["rows"]:
            findings.append({"check": "clarification.follow_through", "severity": CRITICAL, "layer": "system",
                             "detail": f"option {opt!r}: {body['result']['total_eur']} vs oracle {exp[key]['total_eur']}"})
    if matched != {"T5_category", "T5_description"}:
        findings.append({"check": "clarification.follow_through", "severity": MAJOR, "layer": "interpretation",
                         "detail": f"options did not cover both readings (covered: {sorted(matched)})"})
    return findings


def tools_level(ctx, exp) -> list[dict]:
    """Level 1: canonical parameters straight into the tools (no LLM)."""
    from app.service import AssistantService  # noqa: E402
    svc = AssistantService(ctx, None)  # type: ignore[arg-type]
    out = []
    for case in CASES:
        if case.get("variant_of") or case["status"] != "answered":
            continue
        params = TOOL_PARAMS[case["tool"]].model_validate(case["params"])
        o = TOOLS[case["tool"]](ctx, params)
        from app import templates
        resp = {
            "status": "answered", "answer": templates.render(case["tool"], o.result, ctx.policy), "result": o.result,
            "interpretation": {"tool": case["tool"], "params": case["params"]},
            "evidence": svc._evidence(o).model_dump(mode="json"), "assumptions": o.assumptions, "limitations": o.limitations,
        }
        findings = [x for x in check_e2e(case, resp, exp) if x["layer"] != "interpretation"]
        out.append({"id": case["id"], "findings": findings})
    return out


def failure_injection(settings) -> list[dict]:
    """T16: LLM timeout and malformed tool call -> explicit error, no partial answer; /summary still 200."""
    from app.config import Settings  # noqa: E402
    llm_settings = Settings(**{**settings.__dict__, "llm_provider": "openai_compatible", "llm_model": settings.llm_model or "test"})
    scenarios = {
        "timeout": lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("injected")),
        "malformed_tool_call": lambda req: httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "spend", "arguments": "{not json"}}]}}]}),
        "invalid_parameters": lambda req: httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "spend", "arguments": '{"countries": ["Atlantis"]}'}}]}}]}),
    }
    results = []
    data = build_service(Settings(**{**settings.__dict__, "llm_provider": "stub"})).ctx.data
    for name, handler in scenarios.items():
        interp = OpenAICompatibleInterpreter(llm_settings, "system", build_tools(data), transport=httpx.MockTransport(handler))
        with TestClient(create_app(llm_settings, interp)) as c:
            a = c.post("/ask", json={"question": "What is our total spend with vendors based in Spain?"})
            s = c.get("/summary")
        body = a.json()
        findings = []
        if body["status"] != "error" or a.status_code < 500:
            findings.append({"check": "status", "severity": CRITICAL, "layer": "system", "detail": f"{a.status_code} {body['status']}"})
        if body.get("result") or body["evidence"]["row_count"]:
            findings.append({"check": "no_partial_answer", "severity": CRITICAL, "layer": "system", "detail": "partial answer returned"})
        if s.status_code != 200:
            findings.append({"check": "summary_available", "severity": MAJOR, "layer": "system", "detail": f"/summary {s.status_code}"})
        results.append({"id": f"T16 {name}", "http": a.status_code, "status": body["status"],
                        "error": (body.get("error") or {}).get("code"), "summary_http": s.status_code, "findings": findings})
    return results


# --------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=None, help="output file stem (default: eval/results/<UTC timestamp>_<model>)")
    args = ap.parse_args()

    settings = load_settings()
    exp = oracle.expected()
    label = settings.llm_label
    slug = re.sub(r"[^a-z0-9]+", "-", (settings.llm_model if settings.llm_provider != "stub" else "stub").lower()).strip("-")
    started = datetime.now(timezone.utc)
    # Never overwrite earlier reports: default names are timestamped.
    stem = Path(args.out) if args.out else ROOT / "eval" / "results" / f"{started:%Y-%m-%dT%H%M}Z_{slug}"

    service_ctx = build_service(settings.__class__(**{**settings.__dict__, "llm_provider": "stub"})).ctx
    tool_results = tools_level(service_ctx, exp)

    e2e = []
    with TestClient(create_app(settings)) as c:
        for case in CASES:
            runs = []
            for i in range(args.runs):
                # Earlier turns are sent exactly as the frontend sends them (one question per request).
                for earlier in case.get("turns", []):
                    c.post("/ask", json={"question": earlier})
                t = time.monotonic()
                resp = c.post("/ask", json={"question": case["question"]})
                body = resp.json()
                findings = check_e2e(case, body, exp)
                runs.append({"run": i + 1, "http": resp.status_code, "status": body["status"],
                             "tool": body["interpretation"].get("tool"), "params": body["interpretation"].get("params"),
                             "answer": body["answer"], "latency_ms": int((time.monotonic() - t) * 1000),
                             "usage": body["interpretation"].get("usage"),
                             "assumptions": body["assumptions"], "limitations": body["limitations"],
                             "findings": findings})
            # consistency across runs: same meaning (status, tool, applied params), same templated answer
            sig = {json.dumps([r["status"], r["tool"], semantic_params(r["params"])], sort_keys=True) for r in runs}
            answers = {r["answer"] for r in runs if r["status"] == "answered"}
            consistency = [] if len(sig) == 1 and len(answers) <= 1 else [
                {"check": "consistency", "severity": MAJOR, "layer": "interpretation",
                 "detail": f"{len(sig)} distinct interpretations / {len(answers)} distinct answers across runs"}]
            if case["id"] == "T5":
                consistency += clarification_follow_through(c, runs[0], exp)
            worst = max((x["severity"] for r in runs for x in r["findings"] + consistency), key=SEVERITY_RANK.get, default=None)
            e2e.append({"case": case, "runs": runs, "consistency": consistency, "worst": worst})

    # variants: same interpretation as the original
    by_id = {x["case"]["id"]: x for x in e2e}
    for x in e2e:
        orig = x["case"].get("variant_of")
        if orig:
            a = (x["runs"][0]["tool"], x["runs"][0]["status"])
            b = (by_id[orig]["runs"][0]["tool"], by_id[orig]["runs"][0]["status"])
            if a != b:
                x["consistency"].append({"check": "variant", "severity": MAJOR, "layer": "interpretation",
                                         "detail": f"variant {a} != original {b}"})
                x["worst"] = max([x["worst"], MAJOR], key=SEVERITY_RANK.get)

    t16 = failure_injection(settings)
    finished = datetime.now(timezone.utc)
    report = render_report(label, settings, args.runs, started, finished, tool_results, e2e, t16)
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".md").write_text(report)
    stem.with_suffix(".json").write_text(json.dumps({
        "interpreter": label, "runs": args.runs, "started": started.isoformat(), "finished": finished.isoformat(),
        "tools_level": tool_results, "e2e": [{**x, "case": x["case"]} for x in e2e], "failure_injection": t16,
    }, indent=2, default=str))
    print(report)
    print(f"\nWritten: {stem.with_suffix('.md')} and .json")
    criticals = sum(1 for x in e2e if x["worst"] == CRITICAL) + sum(1 for t in tool_results for f in t["findings"] if f["severity"] == CRITICAL) \
        + sum(1 for t in t16 for f in t["findings"] if f["severity"] == CRITICAL)
    return 1 if criticals else 0


def render_report(label, settings, runs, started, finished, tool_results, e2e, t16) -> str:
    L = [f"# Evaluation results: {label}", "",
         f"- Run: {started:%Y-%m-%d %H:%M} to {finished:%H:%M} UTC; {runs} run(s) per case; worst run counts.",
         "- Expected answers: `eval/oracle.py` (independent pandas implementation; shares only the data files).",
         ""]
    crit = [(x["case"]["id"], r["run"], f) for x in e2e for r in x["runs"] for f in r["findings"] if f["severity"] == CRITICAL]
    crit += [(t["id"], "-", f) for t in tool_results + t16 for f in t["findings"] if f["severity"] == CRITICAL]
    L += ["## Critical errors", ""]
    L += [f"- **{cid}** run {run}: `{f['check']}` ({f['layer']}): {f['detail']}" for cid, run, f in crit] or ["None."]
    n_cases = len(e2e)
    passed = sum(1 for x in e2e if x["worst"] is None)
    majors = sum(1 for x in e2e if x["worst"] == MAJOR)
    L += ["", "## Summary", "",
          "| Level | Result |", "|---|---|",
          f"| Tools (no LLM) | {sum(1 for t in tool_results if not t['findings'])}/{len(tool_results)} cases with no findings |",
          f"| End-to-end + interpretation | {passed}/{n_cases} cases with no findings in any run; {majors} with major findings; {n_cases - passed - majors} with critical |",
          f"| Failure injection (T16) | {sum(1 for t in t16 if not t['findings'])}/{len(t16)} scenarios pass |",
          ""]
    L += ["## End-to-end cases (actual results)", "",
          "| Case | Question | Expected | Actual status / tool (per run) | Params (run 1) | Worst | Findings |", "|---|---|---|---|---|---|---|"]
    for x in e2e:
        c = x["case"]
        acts = "; ".join(f"{r['status']}/{r['tool']}" for r in x["runs"])
        fs = [f"{f['check']}: {f['detail']}" for r in x["runs"] for f in r["findings"]] + [f"{f['check']}: {f['detail']}" for f in x["consistency"]]
        fs = list(dict.fromkeys(fs))
        q = " → ".join(c.get("turns", []) + [c["question"]])
        L.append(f"| {c['id']} | {q} | {c['status']} / {c['tool']} | {acts} | `{json.dumps(x['runs'][0]['params'])}` | "
                 f"{x['worst'] or 'pass'} | {'<br>'.join(fs) or '-'} |")
    L += ["", "## Tools level (canonical parameters, no LLM)", "", "| Case | Findings |", "|---|---|"]
    L += [f"| {t['id']} | {'<br>'.join(f['check'] + ': ' + f['detail'] for f in t['findings']) or 'pass'} |" for t in tool_results]
    L += ["", "## Failure injection (T16)", "", "| Scenario | /ask HTTP | status | error code | /summary HTTP | Result |", "|---|---|---|---|---|---|"]
    L += [f"| {t['id']} | {t['http']} | {t['status']} | {t['error']} | {t['summary_http']} | {'pass' if not t['findings'] else '; '.join(f['detail'] for f in t['findings'])} |" for t in t16]
    lat = [r["latency_ms"] for x in e2e for r in x["runs"]]
    if lat:
        lat.sort()
        L += ["", f"Latency per /ask (incl. LLM): median {lat[len(lat) // 2]} ms, max {lat[-1]} ms over {len(lat)} calls."]
    usages = [r["usage"] for x in e2e for r in x["runs"] if r.get("usage")]
    if usages:
        med = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
        pt = [u.get("prompt_tokens", 0) for u in usages]
        ct = [u.get("completion_tokens", 0) for u in usages]
        cost = [u["cost"] for u in usages if "cost" in u]
        line = f"LLM usage per question: median {med(pt)} prompt + {med(ct)} completion tokens"
        if cost:
            line += f"; median cost USD {med(cost):.5f}, total USD {sum(cost):.4f} over {len(cost)} calls (as reported by the provider)"
        L += ["", line + ". Clarification follow-through calls are not included."]
    L += ["", "## Answers (run 1)", ""]
    for x in e2e:
        L += [f"**{x['case']['id']}**: {' → '.join(x['case'].get('turns', []) + [x['case']['question']])}", "", "```",
              x["runs"][0]["answer"], "```", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
