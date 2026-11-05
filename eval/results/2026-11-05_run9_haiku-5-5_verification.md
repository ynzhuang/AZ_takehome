# Evaluation results: anthropic/claude-haiku-5.5 via https://openrouter.ai/api/v1

- Run: 2026-11-05 05:16 to 05:17 UTC; 3 run(s) per case; worst run counts.
- Expected answers: `eval/oracle.py` (independent pandas implementation; shares only the data files).

## Critical errors

None.

## Summary

| Level | Result |
|---|---|
| Tools (no LLM) | 8/8 cases with no findings |
| End-to-end + interpretation | 14/14 cases with no findings in any run; 0 with major findings; 0 with critical |
| Failure injection (T16) | 3/3 scenarios pass |

## End-to-end cases (actual results)

| Case | Question | Expected | Actual status / tool (per run) | Params (run 1) | Worst | Findings |
|---|---|---|---|---|---|---|
| T1 | What are our top 5 vendors by total spend in 2025? | answered / spend | answered/spend; answered/spend; answered/spend | `{"group_by": "vendor", "top_n": 5}` | pass | - |
| T1-v | Which 5 suppliers did we spend the most with last year? | answered / spend | answered/spend; answered/spend; answered/spend | `{"group_by": "vendor", "top_n": 5}` | pass | - |
| T2 | What is our total spend with vendors based in Spain? | answered / spend | answered/spend; answered/spend; answered/spend | `{"countries": ["Spain"]}` | pass | - |
| T3 | Who is allowed to approve an invoice of EUR 60,000? | answered / approval_tier | answered/approval_tier; answered/approval_tier; answered/approval_tier | `{"amount": 60000.0, "currency": "EUR"}` | pass | - |
| T3-v | Who signs off on a 60k invoice? | answered / approval_tier | answered/approval_tier; answered/approval_tier; answered/approval_tier | `{"amount": 60000.0}` | pass | - |
| T4 | Which invoices of EUR 1,000 or more have no PO, and who should they be flagged to? | answered / exceptions | answered/exceptions; answered/exceptions; answered/exceptions | `{"check": "missing_po"}` | pass | - |
| T5 | What is our consulting spend? | clarification_needed / clarify | clarification_needed/clarify; clarification_needed/clarify; clarification_needed/clarify | `{"question": "\"Consulting\" could mean the vendor category or invoices with consulting descriptions. Which do you mean?", "options": ["What is our spend with vendors in the Consulting vendor category in 2025?", "What is our spend on invoices with descriptions Q1, Q2, Q3 or Q4 consulting services in 2025?"]}` | pass | - |
| T5-v | How much went on consulting? | clarification_needed / clarify | clarification_needed/clarify; clarification_needed/clarify; clarification_needed/clarify | `{"question": "\"Consulting\" could mean the vendor category or invoices with consulting descriptions. Which do you mean?", "options": ["What is the total net invoiced spend in EUR for vendors in the Consulting vendor category for 2025?", "What is the total net invoiced spend in EUR for invoices with descriptions Q1, Q2, Q3 or Q4 consulting services in 2025?"]}` | pass | - |
| T6 | Was INV-20008 approved by someone with sufficient authority? | answered / invoice_check | answered/invoice_check; answered/invoice_check; answered/invoice_check | `{"invoice_number": "INV-20008"}` | pass | - |
| T8 | What was our total spend in 2024? | declined / decline | declined/decline; declined/decline; declined/decline | `{"reason": "outside_data_period", "explanation": "The invoice data covers only 2025-01-01 to 2025-12-31, so 2024 spend is not available."}` | pass | - |
| T11 | Which invoices are missing an approver? | answered / exceptions | answered/exceptions; answered/exceptions; answered/exceptions | `{"check": "missing_approver"}` | pass | - |
| T17 | Which invoices of EUR 1,000 or more have no PO? → How about just the invoices from CC-FIN-100? | answered / exceptions | answered/exceptions; answered/exceptions; answered/exceptions | `{"check": "missing_po", "cost_centers": ["CC-FIN-100"]}` | pass | - |
| T18 | Which invoices of EUR 1,000 or more have no PO? → What is our total spend with vendors based in Spain? | answered / spend | answered/spend; answered/spend; answered/spend | `{"countries": ["Spain"]}` | pass | - |
| T19 | How about just the invoices from CC-FIN-100? | clarification_needed / clarify | clarification_needed/clarify; clarification_needed/clarify; clarification_needed/clarify | `{"question": "I don't have the previous turn to build on. Could you state the full question? For example: which check or spend measure should be applied to invoices from CC-FIN-100?", "options": ["List invoices from CC-FIN-100 with no PO at or above EUR 1,000 (missing_po check)", "List invoices from CC-FIN-100 with no approver recorded (missing_approver check)", "List invoices from CC-FIN-100 from non-ACTIVE vendors (non_active_vendor check)", "What is the total net invoiced spend in EUR for cost centre CC-FIN-100?"]}` | pass | - |

## Tools level (canonical parameters, no LLM)

| Case | Findings |
|---|---|
| T1 | pass |
| T2 | pass |
| T3 | pass |
| T4 | pass |
| T6 | pass |
| T11 | pass |
| T17 | pass |
| T18 | pass |

## Failure injection (T16)

| Scenario | /ask HTTP | status | error code | /summary HTTP | Result |
|---|---|---|---|---|---|
| T16 timeout | 502 | error | llm_timeout | 200 | pass |
| T16 malformed_tool_call | 502 | error | llm_invalid_arguments | 200 | pass |
| T16 invalid_parameters | 502 | error | invalid_params | 200 | pass |

Latency per /ask (incl. LLM): median 891 ms, max 1593 ms over 42 calls.

LLM usage per question: median 6124 prompt + 64 completion tokens; median cost USD 0.00064, total USD 0.0276 over 42 calls (as reported by the provider). Clarification follow-through calls are not included.

## Answers (run 1)

**T1**: What are our top 5 vendors by total spend in 2025?

```
Top 5 vendors by net spend:
1. BioReagent Direct (V-1008): EUR 1,131,552.52 (19 invoices)
2. PharmaPrint Studios (V-1006): EUR 1,084,833.66 (17 invoices)
3. ClinSupply Iberia S.L. (V-1002): EUR 977,202.81 (21 invoices)
4. Labtech Solutions GmbH (V-1001): EUR 753,471.70 (15 invoices)
5. Quanta Cloud Services (V-1007): EUR 675,042.79 (13 invoices)
These 5 of 10 vendors account for EUR 4,622,103.48 of EUR 6,886,631.10 total net spend (67.1%).
```

**T1-v**: Which 5 suppliers did we spend the most with last year?

```
Top 5 vendors by net spend:
1. BioReagent Direct (V-1008): EUR 1,131,552.52 (19 invoices)
2. PharmaPrint Studios (V-1006): EUR 1,084,833.66 (17 invoices)
3. ClinSupply Iberia S.L. (V-1002): EUR 977,202.81 (21 invoices)
4. Labtech Solutions GmbH (V-1001): EUR 753,471.70 (15 invoices)
5. Quanta Cloud Services (V-1007): EUR 675,042.79 (13 invoices)
These 5 of 10 vendors account for EUR 4,622,103.48 of EUR 6,886,631.10 total net spend (67.1%).
```

**T2**: What is our total spend with vendors based in Spain?

```
Net spend (countries = Spain): EUR 2,631,938.81 across 49 invoices.
```

**T3**: Who is allowed to approve an invoice of EUR 60,000?

```
An invoice of EUR 60,000.00 requires approval by: Finance Director (policy §3, tier "50,000.01 – 250,000").
```

**T3-v**: Who signs off on a 60k invoice?

```
An invoice of EUR 60,000.00 requires approval by: Finance Director (policy §3, tier "50,000.01 – 250,000").
```

**T4**: Which invoices of EUR 1,000 or more have no PO, and who should they be flagged to?

```
21 invoices at or above the PO threshold with no PO number (potential maverick spend), totalling EUR 1,131,481.11.
By cost centre:
- CC-FIN-100: 10 invoices, EUR 383,083.04
- CC-COM-440: 5 invoices, EUR 277,102.48
- CC-IT-550: 2 invoices, EUR 203,183.42
- CC-OPS-330: 3 invoices, EUR 191,864.23
- CC-RND-210: 1 invoice, EUR 76,247.94
Required action: Flag each invoice to its cost-centre owner and include it in the quarterly maverick-spend report (§2.2).
```

**T5**: What is our consulting spend?

```
"Consulting" could mean the vendor category or invoices with consulting descriptions. Which do you mean?
```

**T5-v**: How much went on consulting?

```
"Consulting" could mean the vendor category or invoices with consulting descriptions. Which do you mean?
```

**T6**: Was INV-20008 approved by someone with sufficient authority?

```
INV-20008, GBP 50,350.62 (EUR 60,435.49). Required approver under §3: Finance Director.
- §2.1 PO required at EUR 1,000.00 or above: No issue detected in checked fields. PO number recorded: PO-78797 (validity and issue date cannot be checked).
- §3.1 Approver recorded: No issue detected in checked fields. Recorded approver: r.okafor.
- §3 Approver has authority (required role: Finance Director): Cannot determine from available data. The data does not map approvers to roles, so the approver's authority cannot be verified.
- §4.1 Vendor is ACTIVE: No issue detected in checked fields. Vendor status ACTIVE in the vendor master (undated snapshot).
- §7.2 No self-approval / segregation of duties: Cannot determine from available data. The data has no requester, so segregation of duties cannot be checked.
Items that cannot be determined need information that is not in the data.
```

**T8**: What was our total spend in 2024?

```
I can't answer this: the question is about a period the invoice data does not cover.
```

**T11**: Which invoices are missing an approver?

```
25 invoices with no approver recorded, totalling EUR 1,057,076.74.
By cost centre:
- CC-FIN-100: 10 invoices, EUR 577,178.35
- CC-OPS-330: 4 invoices, EUR 196,021.31
- CC-COM-440: 5 invoices, EUR 177,549.22
- CC-RND-210: 3 invoices, EUR 61,135.84
- CC-IT-550: 3 invoices, EUR 45,192.02
3 of these also have no PO number: INV-20042, INV-20123, INV-20128.
Required action: Hold these invoices; they may not be paid until an approval is recorded (§3.1).
```

**T17**: Which invoices of EUR 1,000 or more have no PO? → How about just the invoices from CC-FIN-100?

```
10 invoices at or above the PO threshold with no PO number (potential maverick spend) (cost centers = CC-FIN-100), totalling EUR 383,083.04.
By cost centre:
- CC-FIN-100: 10 invoices, EUR 383,083.04
Required action: Flag each invoice to its cost-centre owner and include it in the quarterly maverick-spend report (§2.2).
```

**T18**: Which invoices of EUR 1,000 or more have no PO? → What is our total spend with vendors based in Spain?

```
Net spend (countries = Spain): EUR 2,631,938.81 across 49 invoices.
```

**T19**: How about just the invoices from CC-FIN-100?

```
I don't have the previous turn to build on. Could you state the full question? For example: which check or spend measure should be applied to invoices from CC-FIN-100?
```
