# Manual review

Tested by the author through the UI with `anthropic/claude-haiku-5.5` (via OpenRouter), 2026-11-05.

1. What is our total spend in 2025? — Pass
2. What are our top 5 vendors by total spend in 2025? — Pass after fix
3. What is our total spend with vendors based in Spain? — Pass
4. Who is allowed to approve an invoice of EUR 60,000? — Pass
5. Which invoices of EUR 1,000 or more have no PO, and who should they be flagged to? — Pass
6. Can you be specific to each of these invoices and who should they be flagged to? — Fail originally, not retested
7. List the invoices with no PO at or above EUR 1,000 (missing_po exceptions) and the cost centre owner each should be flagged to. — Pass
8. List the invoices from vendors that are ON_HOLD or INACTIVE (non_active_vendor exceptions) and where each should be escalated. — Pass
9. What is our consulting spend? — Pass
10. What is our total net spend with vendors in the Consulting vendor category across the whole 2025 data period? — Pass
11. Was INV-20008 approved by someone with sufficient authority? — Pass
12. What was our total spend in 2024? — Pass
13. Are all invoices fully compliant with the procurement policy? — Pass
14. Was INV-99999 properly approved? — Pass
15. Who signs off on a 60k invoice? — Pass
16. Who is the Finance Director? — Pass
17. Can you infer? — Pass
18. Can you infer who is the finance director? — Pass
19. How about just the invoices from CC-FIN-100? (after missing PO question) — Pass after fix, 10 invoices, EUR 383,083.04

## Issues found

Both issues were missed by the automated evaluation, fixed, and retested, and both now have regression tests. The reports from before each fix are kept in [`results/`](results/README.md).

- **Top 5 vendor notes used the wrong scope (question 2).** The figures were correct, but the notes (non-active invoices, credit notes, converted invoices) described all 140 invoices instead of the 85 invoices of the 5 vendors shown. Regression tests: the unit test `test_top_n_notes_describe_the_groups_shown` and a `notes_vs_evidence` evaluation check. Failed before the fix in run 4; clean after it in runs 5 and 8.
- **Follow-up questions lost context (question 19).** Each question was interpreted on its own, so the follow-up returned all CC-FIN-100 spend (30 invoices, EUR 1,665,933.65) instead of the 10 missing-PO invoices. Fix: the previous answered question and its interpretation are sent as context. Regression tests: evaluation cases T17 (the follow-up), T18 (a new question must not inherit filters) and T19 (a follow-up without context must be clarified), plus API tests. Failed before the fix in run 6; first fix attempt in run 7; clean in run 8.
