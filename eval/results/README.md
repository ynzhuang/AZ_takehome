# Evaluation reports

All runs are kept, including the run where the checker itself was wrong. Each `.md` file is a readable report; each `.json` file has every run's interpretation, answer and findings. The run 1 JSON was overwritten before reports were kept per run; only its Markdown report survives.

| Run | Files | What changed before this run | Result |
|---|---|---|---|
| 1 | `2026-11-05_run1_haiku-5-5_strict-consistency-check.md` | First run with the real LLM | 0 critical; 4 cases flagged "major" for consistency. **This was a checker issue:** the check compared LLM-written wording (clarification question, decline explanation) and treated explicit full-year dates as different from no dates. Figures, evidence and statuses were identical across runs. |
| 2 | `2026-11-05_run2_haiku-5-5.*`, `2026-11-05_run2_gpt-6-luna.*` | Consistency check now compares meaning (status, tool, filters actually applied); added a clarification follow-through check (each option is re-asked and must be answered correctly) | Both models: 0 critical, 11/11 cases with no findings |
| 3 | `2026-11-05_run3_haiku-5-5.*`, `2026-11-05_run3_gpt-6-luna.*` | After the frontend: result labels changed to "No issue detected in checked fields" and similar; token usage and cost recorded | Both models: 0 critical, 11/11 cases with no findings; usage and cost included |

Before run 1, a dry run with the keyword stub (not kept) produced critical findings for two reworded questions, and found one genuine defect: the T6 answer text contained a figure (the PO threshold) that was not in the structured result. That defect was fixed. The dry run showed the scorer does detect failures.

New runs write to `<UTC timestamp>_<model>.md/.json` by default, so earlier reports are never overwritten.
