# AI usage log

Brief record of how AI was used: important prompts (summarised), my decisions, and issues found and fixed.
No secrets and no raw chat history.

**Tool:** Claude Code (cloud session), working on branch `claude/dazzling-tesla-orll02`.
**Way of working:** design first, reviewed step by step. I made the decisions; the AI challenged the design, verified facts against the data, and implemented after approval.

## 1. Design review (docs/design.md)

| Step | My prompt (summary) | My decisions |
|---|---|---|
| 1. Problem | "Review my problem understanding. Tell me what is correct, missing, needs improvement. Don't modify files yet." | One persona (finance analyst); the 3 example questions plus one data-and-policy question; net invoiced EUR; clarify only material ambiguity; stay within 3–4 h |
| 2. Data and policy | "Review against the original files, identify gaps or mistakes, verify important findings." | Classify each rule as checkable, partially checkable, or cannot be determined; document FX and threshold assumptions; note real-world issues (dirty data, policy changes) without building them |
| 3. Evaluation | "Review my evaluation approach, challenge what I missed, suggest test cases and metrics." | Structured interpretation in `/ask` so the LLM can be evaluated separately; cases T1–T6, T8, T11, T16; rewordings for selected cases; critical errors first; report actual results, not just accuracy |
| 4. Architecture | "Review critically; suggest a minimal plan; what is over-complicated or missing given the time limit?" | Fixed tools with validated parameters (no generated SQL); LLM interprets only; fixed answer templates (no second LLM call); `/summary` without LLM; simple UI with no conversation memory; never return incomplete results |
| 5. Build | "Backend first with tests; OpenRouter with configurable model; .env support; no keys in git; keep this log; scan for credentials before pushing." | Real ECB rates only, never assumed; be explicit about which providers were actually tested |

## 2. Corrections the review produced

- **Rule 1 in my policy list was wrong.** EUR 1,000+ requires a PO (§2.1), not approval. Approval applies at every amount (§3).
- **Rule 2 mixed two rules:** approval tiers (§3, checkable) and segregation of duties (§7, not checkable: no requester field).
- **Vendor status** is an undated snapshot, not "current" status. Wording corrected.
- **The data makes "consulting" genuinely ambiguous:** vendor category (14 invoices) and description (13 invoices) do not overlap.
- **FX matters for approval tiers.** With real ECB rates, 4 invoices fall in a different tier once converted (e.g. INV-20065, GBP 47,368.81 → EUR 54,185.32 → Finance Director).

## 3. Implementation issues and fixes

| Issue | Fix |
|---|---|
| ECB host blocked by the session's network policy | I allowed it in the environment settings; rates downloaded and committed with their source recorded (`data/fx/SOURCE.md`). Rates were never generated |
| OpenRouter key is supplied by a network secret (the proxy adds it) | The client sends `Authorization` only when `LLM_API_KEY` is set; keys come only from env/.env |
| The scorer found an EUR figure in the T6 answer (the PO threshold) that was not in the structured result | Added `po_threshold_eur` to the `invoice_check` result, so every figure in the text comes from the result |
| "[OK]" in invoice checks could read as a compliance verdict | Renamed to "NO ISSUE FOUND"; authority and segregation of duties always show "CANNOT DETERMINE" |
| First eval run: 4 "major" consistency findings | Investigated: only LLM-written wording differed (clarification text, decline explanation) or full-year dates equivalent to no dates. Check changed to compare meaning; the original strict report is kept in `eval/results/` |
| How to know clarification options are usable | Added a follow-through check: each option is re-asked and must be answered with the oracle's figures |
| Pydantic `anyOf`/`title` in tool schemas may be rejected by some providers | Schemas simplified (nullable `anyOf` removed, catalog enums added); tested on two OpenRouter models |

## 4. Prompts in the product

- `backend/app/prompts/interpreter_system.md`: the only LLM prompt. It contains routing rules, the clarify-or-decline policy, the data catalog and the full policy text, and treats the user's question as data.
- No LLM writes answer text: answers come from `backend/app/templates.py`.

## 5. Credential hygiene

- `.env` is git-ignored; `.env.example` has an empty key.
- Before each push, the working tree and full git history are scanned for key patterns (OpenRouter/OpenAI `sk-…`, `Bearer …` tokens, non-empty `API_KEY=`).
