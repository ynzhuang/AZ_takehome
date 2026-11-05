# Finance Spend Intelligence Assistant

A prototype assistant that answers finance analysts' questions about 2025 invoices and the procurement policy (FIN-POL-014 v3.2), showing the evidence behind every answer.

**Status:** the backend (FastAPI), its tests and the evaluation are done. The React frontend is next.

Design notes: [`docs/design.md`](docs/design.md). How AI was used: [`ai/LOG.md`](ai/LOG.md).

## How it works

```
question ──► LLM interpreter ──► exactly one tool call ──► Pydantic + catalog validation
                (or stub)                                         │
                                                                  ▼
answer  ◄── fixed template ◄── deterministic tool (pandas, ECB FX, policy rules)
  + interpretation, evidence rows, policy clauses, FX rates, assumptions, limitations, versions
```

- **The LLM only interprets.** It chooses one of 7 tools and its parameters:

  | Tool | Used for |
  |---|---|
  | `spend` | Spend totals and breakdowns |
  | `exceptions` | Invoices missing a PO, missing an approver, or from a non-active vendor |
  | `approval_tier` | Who must approve an amount |
  | `invoice_check` | Checks on a single invoice |
  | `policy_clauses` | Quoting the policy |
  | `clarify` | Asking a clarification question |
  | `decline` | Saying the question can't be answered |

  The LLM never calculates figures, selects invoice rows, or writes answer text.
- **Parameters are validated** against JSON schemas and against the values that exist in the data (countries, categories, vendor IDs, clause IDs). An invented value returns an error and is never guessed. Invoice data never reaches the LLM, so text inside invoices (for example a description) can't inject instructions.
- **Calculations and policy rules are deterministic:**
  - Spend is the net invoiced amount in EUR, with credit notes included.
  - Non-EUR amounts are converted at the ECB reference rate for the invoice date, or the most recent earlier rate when none was published that day.
  - Approval tiers and the PO threshold are read from the policy document itself. If the document can't be parsed, the app does not start.
- **Answers come from fixed templates** filled from the structured result. The evaluation checks that every EUR figure in the answer text matches it.
- **The system fails closed.** An LLM timeout, a malformed tool call, invalid parameters, or a missing FX rate all return `status: "error"` with no partial result.

## Setup

Requirements: Python 3.11+ (tested on 3.13).

```bash
python3 -m venv backend/.venv
```

```bash
backend/.venv/bin/pip install -r backend/requirements.txt
```

```bash
cp .env.example .env
```

Then edit `.env` and set `LLM_API_KEY`, or set `LLM_PROVIDER=stub` to run without an LLM. Never commit `.env`; it is git-ignored.

Run the API on http://localhost:8000 (interactive docs at `/docs`):

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
```

### LLM configuration

The interpreter calls any **OpenAI-compatible `/chat/completions` endpoint that supports tool calling with `tool_choice: "required"`**. You switch provider by changing three values in `.env`:

| Setting | Example |
|---|---|
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` |
| `LLM_API_KEY` | your key (sent as `Authorization: Bearer …`; left out when empty) |
| `LLM_MODEL` | `anthropic/claude-haiku-5.5` |

**What has actually been tested:**

| Provider | Model | Status |
|---|---|---|
| OpenRouter | `anthropic/claude-haiku-5.5` (default) | Tested: full evaluation, 3 runs |
| OpenRouter | `openai/gpt-6-luna` | Tested: full evaluation, 3 runs |
| Stub (`LLM_PROVIDER=stub`) | keyword rules | Runs offline. Not an LLM, and it fails some reworded questions (see below) |
| OpenAI, Ollama, other OpenAI-compatible endpoints | — | **Untested.** They should work if they support tools and `tool_choice: "required"`, but they have not been run |

Every response reports which interpreter produced it, in `interpretation.interpreter` and in `GET /health`.

## API

**`GET /summary`** works without the LLM. It returns:
- net EUR total, broken down by currency and by vendor status;
- counts of invoices that need follow-up (no PO, no approver, non-active vendor);
- FX information and data/policy/FX versions.

**`POST /ask`** takes `{"question": "..."}` and returns:

```json
{
  "trace_id": "…", "status": "answered | clarification_needed | declined | error",
  "question": "…", "answer": "text from a fixed template",
  "result": { "…structured figures…" },
  "interpretation": { "tool": "spend", "route": "data", "params": { "countries": ["Spain"] },
                      "interpreter": "anthropic/claude-haiku-5.5 via https://openrouter.ai/api/v1", "latency_ms": 900 },
  "evidence": { "rows": [ "…invoice rows with EUR amount and FX rate used…" ], "row_count": 49,
                "clauses": [ { "id": "5.1", "section": "…", "text": "…" } ], "fx_rates": [ "…" ] },
  "assumptions": [ "…" ], "limitations": [ "…" ],
  "clarification": { "question": "…", "options": [ "complete rewritten questions" ] },
  "error": { "code": "llm_timeout", "message": "…" },
  "versions": { "data": "…", "policy": "FIN-POL-014 v3.2", "fx": "…" }
}
```

HTTP status codes:
- **200:** answered, clarification needed, or declined.
- **502:** LLM failure or invalid parameters.
- **503:** missing FX rate.
- **422:** invalid request.

When the status is `error`, the body is still the same JSON shape, with no result and no evidence.

Clarification works without conversation memory: each option is a complete question that the UI sends back as a new `/ask`.

## Tests and evaluation

Unit and API tests make no network calls:

```bash
cd backend && .venv/bin/python -m pytest -q
```

The evaluation calls the LLM configured in `.env`:

```bash
backend/.venv/bin/python eval/run_eval.py --runs 3
```

The tests cover:
- FX lookup on weekends and TARGET holidays, and refusing to guess a missing rate;
- policy parsing, inclusive tier boundaries, and a policy change flowing through;
- data validation blocking startup on bad data;
- tool invariants, for example that spend split by country adds up to the total;
- how each outcome maps to a status;
- the LLM client failing closed on timeouts, HTTP errors, malformed or multiple tool calls, and unknown tools.

The evaluation (`eval/run_eval.py`, design §3):
- compares answers with **independently computed expected answers** (`eval/oracle.py`, plain pandas, no app code);
- runs at three levels: tools alone, the LLM's interpretation alone, and end to end;
- runs each case 3 times and counts the worst run;
- re-asks each clarification option to check it gets answered correctly;
- injects failures (T16): timeout, malformed tool call, invalid parameters.

### Actual results

Run on 2026-11-05 against OpenRouter, 3 runs per case. Full reports: [`eval/results/`](eval/results/).

| Interpreter | Critical errors | E2E cases with no findings (11) | Tools (no LLM) | Failure injection |
|---|---|---|---|---|
| `anthropic/claude-haiku-5.5` | 0 | 11/11 | 6/6 | 3/3 |
| `openai/gpt-6-luna` | 0 | 11/11 | 6/6 | 3/3 |

The 11 end-to-end cases are T1–T6, T8 and T11, plus rewordings of T1, T3 and T5.

**Backend tests:** 64 passed (`pytest`, 2026-11-05).

**Caveats:**
- Eleven questions over 140 invoices is a smoke test and a regression guard. It does not prove accuracy.
- The first Haiku run flagged 4 "major" consistency findings. All four were differences in LLM-written wording, such as how a clarification question was phrased, or explicit full-year dates that mean the same as no dates. The check now compares meaning rather than wording. The original report is kept in [`eval/results/anthropic-claude-haiku-5-5.initial-strict-consistency.md`](eval/results/anthropic-claude-haiku-5-5.initial-strict-consistency.md).
- A dry run of the evaluation with the stub produced critical findings (missed rewordings). That shows the scorer catches failures. It also caught one genuine issue: the T6 answer showed the PO threshold, which was not in the structured result. That is now fixed.

## Data

`data/` holds the provided pack (invoices, vendor master, policy) and `data/fx/ecb_exr_2025.csv`. The rate file contains the official ECB EUR/USD and EUR/GBP reference rates for 2025, unmodified. Where they came from is recorded in [`data/fx/SOURCE.md`](data/fx/SOURCE.md).

## Write-up

_To be completed after the frontend: what was built and left out, how to evaluate it, what production would need, time spent, and next steps._
