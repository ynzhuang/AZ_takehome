# Finance Spend Intelligence Assistant

A prototype that answers finance analysts' questions about 2025 invoices and the procurement policy (FIN-POL-014 v3.2). Every answer shows exactly what it is based on: the invoice rows, the policy clauses, the exchange rates, the assumptions it made and what it cannot tell you.

**Stack:** FastAPI backend, React + TypeScript (Vite) frontend. The LLM runs through OpenRouter; the model is configurable.

**More detail:**
- [`docs/design.md`](docs/design.md): problem, data and policy analysis, evaluation design
- [`ai/LOG.md`](ai/LOG.md): how AI was used, my decisions, issues and fixes
- [`eval/results/`](eval/results/): every evaluation report

| Start-up summary (no LLM) | Invoice check: evidence and limitations | LLM unavailable: fails closed |
|---|---|---|
| ![startup](docs/screenshots/01-startup.png) | ![invoice check](docs/screenshots/04-invoice-check.png) | ![error](docs/screenshots/05-llm-error.png) |

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

  It never calculates figures, picks invoice rows, or writes answer text. Invoice data is never sent to the LLM, so text inside the data (such as invoice descriptions) cannot inject instructions.
- **Every parameter is validated** against a JSON schema and against the values that actually exist in the data (countries, categories, vendor IDs, policy clause IDs). An invented value becomes an error, never a guess.
- **All calculations are deterministic code:**
  - Spend is the net invoiced amount in EUR, with credit notes and all statuses included.
  - Non-EUR amounts are converted at the official ECB rate for the invoice date. If none was published that day, the most recent earlier rate is used.
  - Approval tiers and the PO threshold are read from the policy document. If the document can't be parsed, the app won't start.
- **Answer text comes from fixed templates**, filled from the structured result. No second LLM call is made.
- **Failures fail closed.** An LLM timeout, a malformed tool call, invalid parameters or a missing FX rate all return `status: "error"`, with no result and no evidence. The UI says "no answer produced".

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
| OpenRouter | `anthropic/claude-haiku-5.5` (default) | **Tested:** full evaluation, 3 runs per case, 3 separate evaluation runs |
| OpenRouter | `openai/gpt-6-luna` | **Tested:** full evaluation, 3 runs per case, 2 separate evaluation runs |
| Stub (`LLM_PROVIDER=stub`) | keyword rules | Runs offline. Not an LLM; it fails some reworded questions. The UI header labels it "(not an LLM)" |
| OpenAI direct, Ollama, other OpenAI-compatible APIs | — | **Untested.** They should work if they support tools and `tool_choice: "required"` |

## API

- **`GET /health`:** which interpreter is in use, plus the data, policy and FX versions.
- **`GET /summary`:** works without the LLM. Returns net EUR spend, totals by currency and by vendor status, counts of invoices needing follow-up (no PO at or above the threshold, no approver, non-active vendor), and FX source information.
- **`POST /ask`** with `{"question": "..."}`. It always returns this shape:

```json
{
  "trace_id": "…", "status": "answered | clarification_needed | declined | error",
  "question": "…", "answer": "text from a fixed template", "result": { "…structured figures…" },
  "interpretation": { "tool": "spend", "route": "data", "params": { "countries": ["Spain"] },
                      "interpreter": "anthropic/claude-haiku-5.5 via https://openrouter.ai/api/v1",
                      "latency_ms": 900, "usage": { "prompt_tokens": 5561, "completion_tokens": 65, "cost": 0.0006 } },
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
- **422:** the request itself is invalid.

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

- **No conversation memory:** clarification works without it, as described above. State is plain `useState`.

## Tests and evaluation

| What | Command | Result (2026-11-05) |
|---|---|---|
| Backend unit and API tests (no network) | `cd backend && .venv/bin/python -m pytest -q` | **65 passed** |
| Evaluation (uses the LLM in `.env`) | `backend/.venv/bin/python eval/run_eval.py --runs 3` | See below |
| UI smoke test against the running app (optional; needs Playwright) | `cd frontend && node e2e/ui_smoke.mjs` | **24/24 checks passed** (real backend; LLM-down case used a backend pointed at an unreachable LLM endpoint) |
| Frontend type check and build | `cd frontend && npm run build` | Passes |

**How the evaluation works** (design §3):
- Expected answers are computed independently in `eval/oracle.py`, with plain pandas and no app code.
- It tests three levels: the tools alone (no LLM), the LLM's interpretation, and the full answer end to end.
- Each case runs 3 times and the worst run counts.
- Clarification options are re-asked to check they get answered correctly.
- Failures are injected: timeout, malformed tool call, invalid parameters.

**Results, final run (run 3):** 11 cases (T1–T6, T8, T11, plus rewordings of T1, T3 and T5) and 3 injected failures.

| Model (OpenRouter) | Critical errors | Cases with no findings | Tools level | Failure injection | Median latency | Median tokens per question | Median cost per question |
|---|---|---|---|---|---|---|---|
| `anthropic/claude-haiku-5.5` | 0 | 11/11 | 6/6 | 3/3 | 880 ms | 5,561 + 65 | USD 0.00059 |
| `openai/gpt-6-luna` | 0 | 11/11 | 6/6 | 3/3 | 1,174 ms | 2,888 + 24 | USD 0.00005 |

**History, kept in full in [`eval/results/README.md`](eval/results/README.md):**
- **Run 1** flagged 4 "major" consistency findings. These were a **checker issue**: the check compared LLM-written wording rather than meaning. After the fix, run 2 and run 3 are clean.
- A stub dry run showed the scorer does catch failures. It also found a real defect: a figure appeared in the answer text but not in the result. That defect was fixed.
- **Caveat:** 11 questions over 140 invoices is a smoke test and a regression guard. It does not prove accuracy.

## Write-up

### 1. What I built, what I left out, and why

**Priority:** correct, traceable answers over coverage. In finance controls, a confident wrong answer, or an unsupported "this invoice is compliant", costs more than a question the tool can't answer.

**Built:**
- An LLM that only interprets the question, deterministic tools, and answers from fixed templates.
- A structured interpretation and full evidence in every response.
- Explicit clarify and decline paths.
- A UI that shows the key findings, then the limitations, then the evidence.
- From the brief's optional items, I chose the **evaluation set with a scoring script**. Basic guardrails (clarify or decline) come from the design itself.

**Left out:**
- Conversation memory: clarification options are complete questions instead.
- Questions that need several tools at once (for example, "Spain spend from inactive vendors without a PO"). These are declined.
- Analyses outside the 7 tools.
- Checks the data cannot support: approver authority, segregation of duties, credit-note matching, blanket POs and emergency exceptions. These are reported as **cannot determine**, never as passed.
- Auth, deployment, Docker, a persistent audit log, quarantine for dirty data, and policy retrieval for longer policies.

**Trade-off:** fixed templates make answers rigid, and the tools limit what can be asked. In exchange, any figure in an answer can be traced to code and to data rows.

### 2. How I'd evaluate it

The same approach as now, at larger scale (details in design §3):
1. **Independent expected answers.** Compute them from the raw data without app code, with categorised questions:
   - data, data + join, policy, data + policy;
   - ambiguous, unanswerable, adversarial;
   - reworded versions of each.
2. **Score each layer separately** so failures can be traced: the LLM's interpretation against the expected tool and parameters, the tools against the expected answers, and the full answer end to end.
3. **Catch hallucinations mostly with automatic checks:**
   - figures within EUR 0.01;
   - evidence rows exactly equal the expected rows;
   - every cited invoice and clause exists;
   - every EUR figure in the text appears in the result;
   - required limitations are present;
   - forbidden claims are absent ("compliant", naming an approver as authorised).

   Use an LLM judge only where wording matters, calibrated against analyst labels.
4. **Gate releases:** zero critical errors, worst of N runs, on every change to the prompt, model, policy or data.
5. **Monitor in production:**
   - sampled expert review;
   - analyst feedback and overrides;
   - decline and clarification rates;
   - error rate and latency.

### 3. What production would need

- **Users and access:**
  - SSO and role-based access (analyst, auditor).
  - Row-level access by entity or cost centre.
  - The LLM never sees invoice data, which limits what goes to the provider. The provider contract and data region still need agreeing.
- **Real data:**
  - Read-only views from the ERP or data warehouse instead of CSVs.
  - Validation at ingestion that quarantines bad rows and reports data quality in answers.
  - Data that turns "cannot determine" into actual checks: vendor status history, a delegation-of-authority matrix mapping approvers to roles (§3), requesters (§7), a PO master (§2.1, §2.3) and the Exception Register (§9).
- **Audit trail (§8.2):**
  - For each answer, keep an immutable record of the user, question, interpretation, tool call, data snapshot hash, policy version, FX file version, prompt and model version, and the response. Keep it for 10 years (§8.1).
  - Answers are deterministic once interpreted, so any answer can be reproduced from its record.
- **Policy changes:**
  - Versioned policies with effective dates; check each invoice against the policy in force on its date.
  - A rule registry.
  - Clause retrieval once policies no longer fit in the prompt.
- **Reliability:**
  - Timeouts (already in place), retries with backoff, and a fallback model.
  - Rate limits.
  - Monitoring, and the evaluation as a CI gate.
  - A scheduled ECB rate import with gap checks.
- **Cost:** measured at about USD 0.0006 per question with Haiku, or USD 0.00005 with gpt-6-luna. Assuming 200 users ask 20 questions per working day, that is about 4,000 questions a day: roughly **USD 2.40 a day, or about USD 50 a month, with Haiku**. Hosting, data integration and people will cost far more than the LLM. Caching the fixed system prompt (about 5.5k tokens) would reduce it further.

### Time spent and next steps

- **Time:** the commit history covers about 3¼ hours: design notes from 00:19 UTC, then design review, backend, frontend and evaluation, ending at 03:35 UTC on 2026-11-05. **[Confirm or adjust: time spent before the first commit is not included.]**
- **Next steps:**
  1. Policy-change and dirty-data cases in the evaluation (T14, T15, defined in design §3).
  2. A persistent audit log.
  3. Routing for questions that need several tools, shown explicitly to the user.
  4. Ingestion quarantine for bad data.
  5. More evaluation questions, collected from real analyst questions.

## Data

`data/` holds the provided pack. `data/fx/ecb_exr_2025.csv` contains the official ECB EUR/USD and EUR/GBP reference rates for 2025, unmodified; [`data/fx/SOURCE.md`](data/fx/SOURCE.md) records the request URL and download time. Rates were downloaded, never generated.
