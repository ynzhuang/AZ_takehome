# Finance Spend Intelligence Assistant

A prototype that answers finance analysts' questions about 2025 invoices and the procurement policy (FIN-POL-014 v3.2). Every answer shows exactly what it is based on: the invoice rows, the policy clauses, the exchange rates, the assumptions it made and what it cannot tell you.

**Status:** prototype complete (backend, frontend, tests and evaluation).

**Stack:** FastAPI backend, React + TypeScript (Vite) frontend. The LLM runs through OpenRouter; the model is configurable.

**More detail:**
- [`docs/design.md`](docs/design.md): problem, data and policy analysis, evaluation design
- [`ai/LOG.md`](ai/LOG.md): how AI was used, my decisions, issues and fixes
- [`eval/results/`](eval/results/): every evaluation report
- [`eval/manual_review.md`](eval/manual_review.md): my manual test questions and results

## Write-up

### 1. What I built and what I left out

I focused on the Finance problem rather than building a general chatbot. The LLM interprets questions and selects tools, while calculations, conversion and policy checks are deterministic. An answer can sound correct but still be wrong if the data or evidence is incorrect. I prioritized core functions, evidence and evaluation. I left out authentication, databases, agent workflows and deployment due to the time limit.

### 2. How I evaluated it

For evaluation, I focused on three things: whether the LLM understands the question, whether the results and evidence are correct, and whether Finance users can understand the answers and limitations. I used independently calculated expected answers and tested ambiguity, missing information and failures. I ran 26 cases, 3 runs each, with 2 models, and no critical errors were reported. But this is still a small test set and doesn't prove production readiness. Some evaluation questions also appear as examples in the system prompt, so those cases are not fully independent; the unscripted cold-review questions are.

### 3. What production would need

For production with 200 Finance users, I would consider role-based access control (since different users may have different permissions), real data integration, and audit trails under policy §8.2. I would also collect human-labeled examples to evaluate and improve performance. If policy documents become large and complex, I would consider RAG with a vector database. Production would also need policy versioning, data validation, CI/CD, and monitoring of latency, errors and costs. Before deployment, I would run a pilot with Finance users to check accuracy, missed exceptions and usability.

**Model API cost estimate (LLM calls only, not total production cost):** the measured median cost was USD 0.00069 per question with `anthropic/claude-haiku-5.5` and USD 0.00007 with `openai/gpt-6-luna` (run 16, as reported by OpenRouter). *Assuming* 200 users each ask 20 questions per working day over 21 working days a month, that is about 84,000 questions a month: roughly **USD 58/month with Haiku** or **USD 6/month with gpt-6-luna**. This excludes hosting, data integration, monitoring, support, and any increase in prompt size if the policy grows.

### 4. Optional enhancement: evaluation set and scoring script

For the optional enhancement, I chose the evaluation set and scoring script because I think measuring accuracy is more important than adding features at this stage. I wanted to check the full process, including tool selection, parameters, calculations and evidence, not just the final answer. I also tested ambiguity and failure cases to see if the assistant asks for clarification or stops instead of guessing. I used independently calculated answers rather than another LLM as a judge. The results are in [`eval/results/`](eval/results/). I didn't choose guardrails or agentic routing as the optional item, because the design already limits the LLM to predefined tools and deterministic calculations, and I wanted to evaluate this approach first. Some guardrails are still part of the core design: the assistant asks for clarification or declines instead of guessing, and after cold reviews found answers that silently dropped part of a question, a code check now refuses such answers. Follow-up questions use only the previous answered question as context; no chat history is stored.

## Time spent and next steps

- **Time spent:** approximately 6.5 hours total: 4 hours for initial development and 2.5 hours for additional evaluation, debugging, and revisions.
- **Next steps:**
  1. Policy-change and dirty-data cases in the evaluation (T14, T15, defined in design §3).
  2. A persistent audit log.
  3. Routing for questions that need several tools, shown explicitly to the user.
  4. Ingestion quarantine for bad data.
  5. More evaluation questions, collected from real analyst questions.

## Screenshots

| Start-up summary (no LLM) | Invoice check: evidence and limitations | Follow-up with one-turn context | LLM unavailable: fails closed |
|---|---|---|---|
| ![startup](docs/screenshots/01-startup.png) | ![invoice check](docs/screenshots/04-invoice-check.png) | ![follow-up](docs/screenshots/07-follow-up.png) | ![error](docs/screenshots/05-llm-error.png) |

## How it works

```
question ──► LLM interpreter ──► exactly one tool call ──► Pydantic + data-catalog validation
                (or stub)                                         │
                                                                  ▼
answer  ◄── fixed template ◄── deterministic tool (pandas, ECB FX, policy rules)
  + interpretation, evidence rows, policy clauses, FX rates, assumptions, limitations, versions
```

- **The LLM only interprets the question.** It picks one of 7 tools and fills in its parameters:

  | Tool | Used for |
  |---|---|
  | `spend` | Spend totals and breakdowns |
  | `exceptions` | Invoices missing a PO, missing an approver, or from a non-active vendor |
  | `approval_tier` | Who must approve an amount |
  | `invoice_check` | Checks on a single invoice |
  | `policy_clauses` | Quoting the policy |
  | `clarify` | Asking a clarification question |
  | `decline` | Saying the question can't be answered |

  It never calculates figures, picks invoice rows, or writes answer text. Invoice rows and amounts are never sent to the LLM. It does receive the values it may filter by: vendor IDs and names, countries, categories, cost centres, currencies, and the 13 distinct invoice descriptions. In production, descriptions are supplier-written text, so they would have to be treated as untrusted input (and there would be far too many to list).
- **Every parameter is validated** against a JSON schema and against the values that actually exist in the data (countries, categories, vendor IDs, policy clause IDs). An invented value becomes an error, never a guess.
- **Dropped conditions are refused in code, not just by the prompt.**
  - Before a spend or exception answer is returned, the backend looks for conditions it can recognise in the question: vendor names or IDs, vendor statuses (INACTIVE, ON_HOLD, not active), the missing-PO and missing-approver checks, countries, cost centres, non-EUR currencies, approvers, invoice numbers, amount or count thresholds, years (including "this year"), credit notes, payments, and rankings such as "which vendor".
  - It checks that the parameters express each one. If the tool has a filter for it but it wasn't applied, the answer is a clarification. If the condition can't be applied at all (e.g. "over EUR 50,000", a credit-note total, a payment question, a vendor ranking of an exception list, or a year outside the data), the answer is a decline. A broader or unrelated answer is never returned.
  - A vendor status must be applied with the existing filters: `vendor_statuses` for spend, or the vendor IDs with that status for an exception list. Lowercase "inactive" on its own is ambiguous (status INACTIVE, or every vendor that is not ACTIVE), so the assistant asks which is meant and offers both as complete questions.
  - Conditions it can't recognise (e.g. "late invoices") still depend on the prompt rules.
- **All calculations are deterministic code:**
  - Spend is the net invoiced amount in EUR, with credit notes and all statuses included.
  - Non-EUR amounts are converted at the official ECB rate for the invoice date. If none was published that day, the most recent earlier rate is used.
  - Approval tiers and the PO threshold are read from the policy document. If the document can't be parsed, the app won't start.
- **Answer text comes from fixed templates**, filled from the structured result. No second LLM call is made.
- **Failures fail closed.** An LLM timeout, a malformed tool call, invalid parameters or a missing FX rate all return `status: "error"`, with no result and no evidence. The UI says "no answer produced". Exactly one tool call per question is enforced by the API request (`parallel_tool_calls: false`).
- **Follow-ups use one previous turn, not chat history.**
  - The frontend sends the last *answered* question and its interpretation (tool and parameters) as optional `context`. The backend validates it with the same schemas.
  - The LLM sees it as a marked data block. If the new question builds on it ("How about just CC-FIN-100?"), the LLM reuses those settings, applies only the change, and sets a `follow_up` flag. The answer card then shows "Follow-up to: …", and the parameter chips show the full settings used.
  - A question that only makes sense as a follow-up but arrives without context is clarified, not guessed.

## Setup and run

Requirements: Python 3.11+ (tested on 3.13) and Node 20+ (tested on 22).

**Backend.** Create the virtual environment:

```bash
python3 -m venv backend/.venv
```

Install the dependencies:

```bash
backend/.venv/bin/pip install -r backend/requirements.txt
```

Create your local settings file:

```bash
cp .env.example .env
```

Edit `.env`: set `LLM_API_KEY`, or set `LLM_PROVIDER=stub` to run without an LLM. `.env` is git-ignored; never commit it. Then start the API:

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000
```

**Frontend.** Install the dependencies:

```bash
cd frontend && npm install
```

Start the dev server:

```bash
cd frontend && npm run dev
```

Open http://localhost:5173. The dev server forwards `/api/*` to `http://localhost:8000`; set `VITE_BACKEND_URL` to point it elsewhere.

### LLM provider

The interpreter works with any **OpenAI-compatible `/chat/completions` endpoint that supports tool calling with `tool_choice: "required"`**. To switch provider, change these values in `.env`:

| Setting | Example |
|---|---|
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` |
| `LLM_API_KEY` | your key (sent as `Authorization: Bearer …`; left out when empty) |
| `LLM_MODEL` | `anthropic/claude-haiku-5.5` |

| Provider | Model | Status |
|---|---|---|
| OpenRouter | `anthropic/claude-haiku-5.5` (default) | **Tested:** full evaluation, 3 runs per case, 16 separate evaluation runs |
| OpenRouter | `openai/gpt-6-luna` | **Tested:** full evaluation, 3 runs per case, 14 separate evaluation runs |
| Stub (`LLM_PROVIDER=stub`) | keyword rules | Runs offline. Not an LLM; it fails some reworded questions. The UI header labels it "(not an LLM)" |
| OpenAI direct, Ollama, other OpenAI-compatible APIs | — | **Untested.** They should work if they support tools and `tool_choice: "required"` |

## API

- **`GET /health`:** which interpreter is in use, plus the data, policy and FX versions.
- **`GET /summary`:** works without the LLM. Returns net EUR spend, totals by currency and by vendor status, counts of invoices needing follow-up (no PO at or above the threshold, no approver, non-active vendor), and FX source information.
- **`POST /ask`** with `{"question": "...", "context": {"question": "...", "tool": "exceptions", "params": {"check": "missing_po"}}}`. `context` is optional: the previous answered turn, used for follow-ups. It always returns this shape:

```json
{
  "trace_id": "…", "status": "answered | clarification_needed | declined | error",
  "question": "…", "answer": "text from a fixed template", "result": { "…structured figures…" },
  "interpretation": { "tool": "spend", "route": "data", "params": { "countries": ["Spain"] },
                      "interpreter": "anthropic/claude-haiku-5.5 via https://openrouter.ai/api/v1",
                      "latency_ms": 900, "usage": { "prompt_tokens": 6456, "completion_tokens": 45, "cost": 0.0007 },
                      "follow_up_to": null },
  "evidence": { "rows": ["…invoice rows with EUR amount and FX rate used…"], "row_count": 49,
                "clauses": [{ "id": "5.1", "section": "…", "text": "…" }], "fx_rates": ["…"] },
  "assumptions": ["…"], "limitations": ["…"],
  "clarification": { "question": "…", "options": ["complete rewritten questions"] },
  "error": { "code": "llm_timeout", "message": "…" },
  "versions": { "data": "…", "policy": "FIN-POL-014 v3.2", "fx": "…" }
}
```

HTTP status codes:
- **200:** answered, clarification needed, or declined.
- **502:** the LLM failed or returned invalid parameters.
- **503:** an FX rate is missing.
- **422:** the request itself is invalid, including an invalid `context`.

## Frontend

- **Summary panel:** loads `/summary` on start-up, so figures are on screen before any question. It handles loading and error states and has a Retry button. Each "needs follow-up" count has a **Show** link that asks the matching question.
- **Answer card order:**
  1. The status badge and "Interpreted as: <tool> using <data/policy> + parameters".
  2. The answer: the key findings, plus a table of checks for invoice checks.
  3. **Limitations**, highlighted.
  4. Assumptions and definitions.
  5. Collapsible tables: invoice rows used, policy clauses cited (quoted word for word), exchange rates used.
  6. Technical details: model, latency, tokens, cost, the raw tool call, versions, trace ID.
- **Response states:**

  | State | What the UI shows |
  |---|---|
  | Loading | Spinner; the Ask button is disabled |
  | Answered | The full answer card above |
  | Clarification needed | Option buttons; each is a complete question sent as a new `/ask`. If there are no options (e.g. a missing date), an "Edit my question" button |
  | Declined | The reason |
  | Error | Error code, "nothing above is a partial result", Retry |
  | Network failure or timeout | Error message and Retry |

- **Follow-ups:** each question sends the last answered turn as `context`; no chat history is kept or sent. Clarification still works without it, as described above. State is plain `useState`.

## Tests and evaluation

| What | Command | Result (2026-11-05) |
|---|---|---|
| Backend unit and API tests (no network) | `cd backend && .venv/bin/python -m pytest -q` | **146 passed** |
| Evaluation (uses the LLM in `.env`) | `backend/.venv/bin/python eval/run_eval.py --runs 3` | See below |
| UI smoke test against the running app (optional; needs Playwright) | `cd frontend && node e2e/ui_smoke.mjs` | **28/28 checks passed**, including a follow-up (real backend; LLM-down case used a backend pointed at an unreachable LLM endpoint) |
| Frontend type check and build | `cd frontend && npm run build` | Passes |

**How the evaluation works** (design §3):
- Expected answers are computed independently in `eval/oracle.py`, with plain pandas and no app code.
- It tests three levels: the tools alone (no LLM), the LLM's interpretation, and the full answer end to end.
- Each case runs 3 times and the worst run counts.
- Clarification options are re-asked to check they get answered correctly.
- Failures are injected: timeout, malformed tool call, invalid parameters.
- Follow-up cases send the earlier question first and pass the last answered turn as context, as the frontend does. T18 checks that a self-contained question does not inherit filters; T19 checks that a follow-up sent without context is clarified.
- Dropped-condition cases: T20 checks that a vendor named by name is applied as a filter; T21 checks that a condition no tool can apply ("over EUR 50,000") is clarified or declined. Answering T21 counts as critical.
- Unanswerable-question cases: T22 (credit-note total), T23 ("paid this year"), T24 (vendor ranking of missing approvers) and T26 ("spend this year") must be declined or clarified; answering counts as critical. T25 checks that a supported cost-centre ranking is still answered; declining a supported question counts as major (over-refusal).
- Third-cold-review cases: T27 and T28 check single-invoice results for an invoice missing an approver (INV-20005) and one missing a PO (INV-20077) against the raw data; "nan" in any answer is critical. T29 checks that "inactive vendors" is clarified and that both offered options are answered exactly. T30 and T31 check that a vendor status combined with a check is applied. Answering without the status counts as critical.

**Results, last run after a code change (run 16):** 26 cases (T1–T6, T8, T11, rewordings of T1, T3 and T5, follow-ups T17–T19, dropped conditions T20–T21, unanswerable questions T22–T26, and the third cold review's cases T27–T31) and 3 injected failures.

| Model (OpenRouter) | Critical errors | Cases with no findings | Tools level | Failure injection | Median latency | Median tokens per question | Median cost per question |
|---|---|---|---|---|---|---|---|
| `anthropic/claude-haiku-5.5` | 0 | 26/26 | 14/14 | 3/3 | 906 ms | 6,630 + 45 | USD 0.00069 |
| `openai/gpt-6-luna` | 0 | 24/26 (2 major: T5 and T25, below) | 14/14 | 3/3 | 1,529 ms | 3,616 + 34 | USD 0.00007 |

**History, kept in full in [`eval/results/README.md`](eval/results/README.md):**
- **Run 1** flagged 4 "major" consistency findings. These were a **checker issue**: the check compared LLM-written wording rather than meaning. After the fix, run 2 and run 3 are clean.
- **Manual testing found a bug the evaluation had missed:** for "top 5 vendors", the notes (non-active invoices, credit notes, converted invoices) described all 140 invoices instead of the 85 invoices of the vendors shown. The figures were correct. A new check that notes must match the evidence rows reported critical errors in T1 and T1-v on the unfixed code (run 4). After the fix, run 5 is clean. Runs 1–3 did not include this check.
- **Manual testing found a second bug the evaluation had missed:** a follow-up ("How about just the invoices from CC-FIN-100?") was interpreted without the previous question and returned all CC-FIN-100 spend instead of the 10 missing-PO invoices. On the unfixed code, the new two-turn case T17 failed for both models (run 6). After adding one-turn context, the first attempt (run 7) fixed T17 but caused Haiku to make two tool calls (T4) and invent tool names (T19). Enforcing a single tool call and simplifying the prompt fixed both. Run 8 is clean.
- **Verification re-run (run 9, no code changes):** Haiku was again 14/14 with 0 critical. gpt-6-luna had 0 critical but one major finding (13/14): in T5, one rewritten clarification option, asked again, led to a second clarification instead of an answer. It passed in run 8 but has recurred in every gpt-6-luna run since (runs 10–12); Haiku is unaffected. Not fixed.
- [Manual review](eval/manual_review.md) with Haiku: 19 questions; both bugs above were found this way.
- **A cold review found two more silent failures** ([questions and results](eval/results/2026-11-05_cold-review-probes.md)). "How much did we spend with BioReagent Direct?" returned total spend for all vendors, and "Show me invoices over EUR 50,000 without a PO" returned all 21 missing-PO invoices. The vendor name and the amount condition were dropped, because the LLM had no vendor names and nothing refused an answer that left out part of the question.
  - On the unfixed code, the new cases T20 and T21 were critical for both models (run 10).
  - The fix (vendor names, plus the code check described in "How it works") at first turned an invented-value error into a clarification (run 11, failure injection). Running the check after the tool's own validation fixed that.
  - Run 12 is clean apart from gpt-6-luna's T5 loop. "Which vendor has the most invoices without an approver?" is still answered by cost centre: a missing grouping, not a dropped filter, so it's a known limitation.
- **A second cold review** ([questions and results](eval/results/2026-11-05_cold-review-probes.md)) passed **17 of 19** unscripted questions. "What is the total value of our credit notes?" and "How much have we paid our vendors this year?" were answered with total spend. A separate check, not one of the 19, found "What is our total spend this year?" answered with 2025 figures, although "this year" is 2026. The vendor-ranking question above was also still answered by cost centre.
  - The new cases T22–T24 and T26 were critical for Haiku on the unfixed code (run 13).
  - The fix extends the same code check: credit notes, payment terms, relative years and rankings by a dimension the tool doesn't group by are now declined. No new analytics were added. After it, the second cold review passes 19 of 19, the separate "this year" check is declined, and "last year" and "in 2025" are still answered.
  - Run 14: Haiku 0 critical, 21/21. gpt-6-luna 0 critical, with two major findings: its T5 loop, and declining the supported cost-centre ranking (T25), which it also did before the fix. The scorer now counts an unnecessary decline as major (over-refusal), like an unnecessary clarification.
- **A third cold review** ([questions and results](eval/results/2026-11-05_cold-review-probes.md)) passed **19 of 23** new unscripted questions. Two failures were critical:
  - The single-invoice check reported "No issue detected" and printed "nan" for every invoice missing a PO (21) or an approver (25), because missing values load as NaN, which Python treats as true. A test now checks all 140 invoices against the raw data.
  - Questions combining a vendor status with a check ("inactive vendors … missing a PO", "ON_HOLD vendors … no approver") were answered for all vendors. The code check now requires the status to be applied with the existing filters, and lowercase "inactive" alone is clarified with both readings.
  - On the unfixed code, the new cases T27–T31 were critical: Haiku 5 cases, gpt-6-luna 2 (it declined the status questions instead) (run 15). After the fix (run 16): Haiku 0 critical, 26/26; gpt-6-luna 0 critical, with major findings for its T5 clarification loop and declining T25, both seen in earlier runs.
  - **Documented, not fixed:** "What is our spend with Crown Travel Partners, and is that vendor allowed to be paid?" is declined because of the word "paid", although §4.1 answers the policy part. "What is the total spend in 2025 in USD?" is answered as spend on USD-billed invoices (EUR 1,015,796.16) instead of asking whether a conversion to USD was meant; the interpretation shown makes this visible.
  - "How much did we spend with inactive vendors?" (answered in the second cold review) is now a clarification between the two readings, by design.
- A stub dry run showed the scorer does catch failures. It also found a real defect: a figure appeared in the answer text but not in the result. That defect was fixed.
- **Caveat:** 26 cases over 140 invoices is a smoke test and a regression guard. It does not prove accuracy. Some cases use the same wording as examples in the system prompt (T5 "consulting", T17 "How about just CC-FIN-100?", T20 "BioReagent", T21 "over EUR 50,000"), so they are not held out; the cold-review questions are.

## Data

`data/` holds the provided pack. `data/fx/ecb_exr_2025.csv` contains the official ECB EUR/USD and EUR/GBP reference rates for 2025, unmodified; [`data/fx/SOURCE.md`](data/fx/SOURCE.md) records the request URL and download time. Rates were downloaded, never generated.
