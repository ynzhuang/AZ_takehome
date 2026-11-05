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
| 5. Backend | "Backend first with tests; OpenRouter with configurable model; .env support; no keys in git; keep this log; scan for credentials before pushing." | Real ECB rates only, never assumed; be explicit about which providers were actually tested; review the backend before the frontend |
| 6. Frontend | "Vite + React + TypeScript, keep it simple. Key findings and limitations first, evidence in collapsible tables. Handle all response states. Change 'NO ISSUE FOUND' wording. Then run the full app and evaluation again, keep the original reports including the checker issue." | Architecture stays in the README (no design §4); check results worded as "No issue detected in checked fields" |
| 7. README | "Move the write-up after the introduction, use my wording and only fix grammar, remove the old write-up, keep the technical sections unchanged, replace the time estimate with a placeholder, check links and numbers, don't change app code." Then: "Add back a short cost estimate for 200 users, clear that it's only model API cost based on assumptions." | The write-up is in my own words; time spent is left for me to fill in; the cost estimate covers LLM API calls only, with its assumptions stated |
| 8. Manual testing | "For top 5 vendors, the answer says 29 invoices from non-active vendors and includes 2 credit notes. Those seem to be from the full dataset, not the top 5 vendors. Please check." | Confirm, fix with a regression test, and make the evaluation able to catch this kind of error |
| 9. Manual testing | "I asked which invoices of EUR 1,000 or more have no PO (21, correct), then 'How about just the invoices from CC-FIN-100?'. Expected 10 missing-PO invoices, EUR 383,083.04; got spend(), 30 invoices, EUR 1,665,933.65. Check whether the follow-up changes are running, whether context is sent, and why spend was selected. Add a regression test. Don't change code until the cause is identified." | Option B: send the previous answered turn (question + interpretation) as optional context; no chat history |
| 10. Follow-up fix | "Go with option B, then rerun tests and evaluation." | One previous turn as validated context; "Follow-up to" shown only when the LLM built on it; referring questions without context are clarified |
| 11. Finalize | "I finished manual testing. Add eval/manual_review.md with my questions and pass/fail results, briefly document the two issues, link it in the README, update design.md and this log if needed, keep my README wording, verify tests, links and secrets, leave time spent for me, then commit, push and prepare a PR to main without overwriting changes. Don't merge yet." | Manual review recorded as I tested it (question 6 failed originally and was not retested); PR prepared, not merged |

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
| "NO ISSUE FOUND" still sounded like a verdict (my review) | Labels come from the backend (`RESULT_LABELS`): "No issue detected in checked fields", "Flag for review", "Cannot determine from available data". The UI uses the same labels, so the two cannot drift |
| UI screenshots: invoice checks repeated as text lines and in the table | For invoice checks the card shows the headline line and the table only |
| Dev proxy returns an empty HTTP 500 when the backend is down; the UI said "unexpected response" | Message now says no valid response was received and asks whether the backend is running |
| UI smoke test: an ambiguous selector matched both the parameter chip and the answer text | Fixed the test (selector scoped to the chip); not an app bug |
| Re-running the evaluation would overwrite earlier reports | Reports renamed by run (`eval/results/README.md`); new runs are timestamped by default. Run 1's JSON had already been overwritten by run 2; its Markdown report is kept |
| Production cost was an estimate | Token usage and provider-reported cost are now captured per question and summarised in each evaluation report |
| The AI-drafted README write-up was not in my words, and its time estimate came from git history, which misses my planning and review time | Replaced with my own write-up (grammar fixes only); time spent is a placeholder for me to fill in |
| **Found by my manual testing, missed by the evaluation:** for "top 5 vendors", the notes said 29 non-active invoices, 2 credit notes and 51 converted invoices. These described all 140 invoices, not the 85 invoices of the 5 vendors shown (correct: 17, 0, 32; both credit notes belong to Helix, which is not in the top 5). Figures and evidence rows were correct | Root cause: `spend()` computed notes over all filtered invoices instead of the rows shown. Fixed: notes describe the rows shown; a scope line says the ranking and total cover all invoices; credit notes outside the shown vendors are named separately. Added a failing-first unit test and a `notes_vs_evidence` evaluation check (critical). Run 4 (unfixed code) kept to show the check catches it; run 5 after the fix. Lesson: the evaluation checked figures and evidence but not whether the explanatory notes matched the evidence |
| **Found by my manual testing, missed by the evaluation:** a follow-up question ("How about just the invoices from CC-FIN-100?") after the missing-PO question returned all CC-FIN-100 spend (30 invoices, EUR 1,665,933.65) instead of the 10 missing-PO invoices (EUR 383,083.04) | Cause (verified, no code changed yet): no follow-up support was ever built (my earlier "no conversation memory" decision). The frontend and `/ask` send only the current question; a capture of the LLM input confirmed it received only the follow-up text. Read alone, the literal reading is spend filtered by cost centre, and the prompt has no rule for context-dependent questions, so the model guesses. Regression test added: two-turn evaluation case T17 and a unit test for the expected deterministic answer. Run 6 (unfixed): T17 critical for both models (Haiku `spend` 2/3, one lucky guess; gpt-6-luna clarified 2/3, `spend` 1/3). I chose option B (one previous turn as context). Fixed: optional validated `context` on `/ask`, a `follow_up` flag turned into `interpretation.follow_up_to`, prompt rules, frontend sends the last answered turn and shows "Follow-up to". Guard cases T18 (no filter leakage) and T19 (no context means clarify) added |
| Prompt template bug introduced by the follow-up change: literal JSON braces in a `str.format` template stopped the app starting with the real LLM; unit tests did not build the real prompt | Braces escaped; a unit test now renders the real system prompt |
| First option-B evaluation (run 7): Haiku made two tool calls for the two-part T4 question (2/3 runs) and invented tool names for T19 (3/3). Both failed closed (errors, no wrong answers), and gpt-6-luna was unaffected | Exactly one tool call enforced in the API request (`parallel_tool_calls: false`), instead of relying on prompt wording; `follow_up` kept only on answer tools (also cut about 300 prompt tokens); plainer no-context rule. Checked 5/5 with Haiku, then run 8: clean for both models |
| The cost estimate was removed together with the old write-up | Re-added as a short, assumption-based estimate of model API cost only (about USD 50/month with Haiku, USD 4/month with gpt-6-luna, for 200 users × 20 questions per working day), explicitly not total production cost |

## 4. Verification actually run (2026-11-05)

- Backend: 65 pytest tests passed (no network).
- Evaluation, run 3 (after the frontend), 3 runs per case: 0 critical errors and 11/11 cases with no findings for both `anthropic/claude-haiku-5.5` and `openai/gpt-6-luna`; failure injection 3/3.
- Full app: two backends (one with an unreachable LLM endpoint) plus the Vite dev server, driven by `frontend/e2e/ui_smoke.mjs`: 24/24 UI checks passed. Screenshots in `docs/screenshots/` were reviewed by eye, which is how the duplicated invoice-check text and the overlapping composer were found.
- Notes-scope fix: unit test failed before the fix and passes after (66 tests passed). Evaluation run 4 (unfixed code, new check): critical errors in T1 and T1-v in every run and at the tools level. Run 5 (fixed), both models: 0 critical, 11/11 cases with no findings, failure injection 3/3. UI smoke test re-run: 24/24 passed; top-5 screenshot regenerated and checked by eye.
- Follow-up fix (option B): 78 backend tests passed. Evaluation run 8, 14 cases × 3 runs: 0 critical, 14/14 clean, tools 8/8, failure injection 3/3, for both models. UI smoke test 28/28, including the follow-up (10 invoices, EUR 383,083.04, "Follow-up to" label); screenshot reviewed by eye. Cost per question rose slightly (Haiku USD 0.00059 → 0.00064) because the prompt is longer.
- Final verification before the PR (no code changes since run 8): 78 backend tests passed; evaluation run 9: Haiku 14/14 with 0 critical; gpt-6-luna 13/14 with 0 critical and one major finding (T5 clarification option led to a second clarification; passed in run 8); UI smoke test 28/28; links and secret scan checked.
- Manual review by me with Haiku 5.5: 19 questions, 18 pass (2 of them after fixes), 1 failed originally and not retested (question 6). See `eval/manual_review.md`.
- README restructure: technical sections confirmed byte-for-byte unchanged; all relative links resolve; figures checked against the run 2 and run 3 reports; backend tests re-run (65 passed). No app code changed.

## 5. Prompts in the product

- `backend/app/prompts/interpreter_system.md`: the only LLM prompt. It contains routing rules, the clarify-or-decline policy, the data catalog and the full policy text, and treats the user's question as data.
- No LLM writes answer text: answers come from `backend/app/templates.py`.

## 6. Credential hygiene

- `.env` is git-ignored; `.env.example` has an empty key.
- Before each push, the working tree and full git history are scanned for key patterns (OpenRouter/OpenAI `sk-…`, `Bearer …` tokens, non-empty `API_KEY=`).
