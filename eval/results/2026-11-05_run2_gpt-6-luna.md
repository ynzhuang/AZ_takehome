# Evaluation results: openai/gpt-6-luna via https://openrouter.ai/api/v1

- Run: 2026-11-05 03:15 to 03:16 UTC; 3 run(s) per case; worst run counts.
- Expected answers: `eval/oracle.py` (independent pandas implementation; shares only the data files).

## Critical errors

None.

## Summary

| Level | Result |
|---|---|
| Tools (no LLM) | 6/6 cases with no findings |
| End-to-end + interpretation | 11/11 cases with no findings in any run; 0 with major findings; 0 with critical |
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
| T5 | What is our consulting spend? | clarification_needed / clarify | clarification_needed/clarify; clarification_needed/clarify; clarification_needed/clarify | `{"question": "Do you mean spend with vendors in the Consulting category, or spend on invoices described as consulting services?", "options": ["What is our spend with vendors in the Consulting category?", "What is our spend on invoices with Q1\u2013Q4 consulting services descriptions?"]}` | pass | - |
| T5-v | How much went on consulting? | clarification_needed / clarify | clarification_needed/clarify; clarification_needed/clarify; clarification_needed/clarify | `{"question": "Do you mean spend with vendors in the Consulting category, or invoices with consulting services descriptions?", "options": ["How much was spent in 2025 with vendors in the Consulting category?", "How much was spent in 2025 on invoices described as Q1, Q2, Q3, or Q4 consulting services?"]}` | pass | - |
| T6 | Was INV-20008 approved by someone with sufficient authority? | answered / invoice_check | answered/invoice_check; answered/invoice_check; answered/invoice_check | `{"invoice_number": "INV-20008"}` | pass | - |
| T8 | What was our total spend in 2024? | declined / decline | declined/decline; declined/decline; declined/decline | `{"reason": "outside_data_period", "explanation": "The available invoice data covers 2025 only, so total spend for 2024 cannot be determined."}` | pass | - |
| T11 | Which invoices are missing an approver? | answered / exceptions | answered/exceptions; answered/exceptions; answered/exceptions | `{"check": "missing_approver"}` | pass | - |

## Tools level (canonical parameters, no LLM)

| Case | Findings |
|---|---|
| T1 | pass |
| T2 | pass |
| T3 | pass |
| T4 | pass |
| T6 | pass |
| T11 | pass |

## Failure injection (T16)

| Scenario | /ask HTTP | status | error code | /summary HTTP | Result |
|---|---|---|---|---|---|
| T16 timeout | 502 | error | llm_timeout | 200 | pass |
| T16 malformed_tool_call | 502 | error | llm_invalid_arguments | 200 | pass |
| T16 invalid_parameters | 502 | error | invalid_params | 200 | pass |

Latency per /ask (incl. LLM): median 1128 ms, max 4612 ms over 33 calls.

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
Do you mean spend with vendors in the Consulting category, or spend on invoices described as consulting services?
```

**T5-v**: How much went on consulting?

```
Do you mean spend with vendors in the Consulting category, or invoices with consulting services descriptions?
```

**T6**: Was INV-20008 approved by someone with sufficient authority?

```
INV-20008, GBP 50,350.62 (EUR 60,435.49). Required approver under §3: Finance Director.
- [NO ISSUE FOUND] §2.1 PO required at EUR 1,000.00 or above: PO number recorded: PO-78797 (validity and issue date cannot be checked).
- [NO ISSUE FOUND] §3.1 Approver recorded: Recorded approver: r.okafor.
- [CANNOT DETERMINE] §3 Approver has authority (required role: Finance Director): The data does not map approvers to roles, so the approver's authority cannot be verified.
- [NO ISSUE FOUND] §4.1 Vendor is ACTIVE: Vendor status ACTIVE in the vendor master (undated snapshot).
- [CANNOT DETERMINE] §7.2 No self-approval / segregation of duties: The data has no requester, so segregation of duties cannot be checked.
Items marked CANNOT DETERMINE need information that is not in the data.
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
