## 1. Problem and objective

### 1.1 Business problem

Finance analysts spend significant time cross-referencing vendor invoices against the vendor master and the procurement policy (FIN-POL-014 v3.2): approvals, vendor status, and spend thresholds. The results feed control reporting (e.g. maverick spend is reported quarterly to the Finance Controls Committee, §2.2), so errors have control consequences, not just analytical ones.

**Objective:** a decision-support assistant for **finance analysts**. For each question, the assistant returns exactly one of:
1. an **answer** whose figures are reproducible and whose basis is visible: invoice rows, policy clauses, FX rates, and stated assumptions;
2. a **clarification question**, when the question is materially ambiguous; or
3. an explicit **"cannot be answered from available data"**, with the reason.

The assistant does not approve, reject, or certify compliance. It flags items for human review.

### 1.2 Prototype scope (3–4 hour time box)

| Question type | Example | Sources |
|---|---|---|
| Data aggregation | "What are our top 5 vendors by total spend in 2025?" | invoices |
| Data + vendor join | "What is our total spend with vendors based in Spain?" | invoices, vendor master |
| Policy lookup | "Who is allowed to approve an invoice of EUR 60,000?" | policy |
| Data + policy | "Which invoices of EUR 1,000 or more have no PO, and who should they be flagged to?" (§2.1–2.2) | invoices, vendor master, policy |

Also in scope: `GET /summary`, which is computed deterministically and works without the LLM.

Out of scope (documented, not built): other personas (approver, auditor), authentication and access control, deployment, automated decisions, and policy rules the data cannot support (see Section 2).

### 1.3 Definitions and decision rules

- **Spend** = net invoiced amount: the sum of invoice amounts, including credit notes (negative amounts) and all invoices regardless of PO, approver, or vendor status. Payment data does not exist, so "spend" never means "paid".
- **Currency:** convert to EUR at the ECB reference rate on the invoice date (§5.1). When the ECB publishes no rate for that date (weekends, TARGET holidays), use the most recent prior published rate. Rates are stored in a versioned local table with source and retrieval date, never fetched at question time. Each converted amount is rounded to 2 decimal places, and thresholds are compared against that value.
- **Ambiguity:** ask for clarification when the plausible interpretations select different invoice sets or lead to a different policy conclusion. Example: "consulting spend" may mean the vendor category *Consulting* or descriptions containing "consulting", and these are disjoint sets in this data. Otherwise, answer and state the assumption.

### 1.4 Consequential failure modes (by severity)

**Critical**
1. **Unsupported compliance conclusion.** Example: stating an invoice was "properly approved" when the data has no approver role, requester, or exception register.
2. **Wrong figure.** Causes: mixed currencies summed, wrong rows selected, credit notes mishandled, or the wrong approval tier near a threshold.
3. **Invalid evidence.** A cited invoice or clause does not exist or does not support the claim, or a number in the answer text differs from the computed result.

**High**

4. **Wrong interpretation.** Wrong filter or definition, including a correct number reached for the wrong reason, or a silent assumption on a material ambiguity.
5. **No abstention.** Answering a question outside the data or policy (e.g. 2024 spend, generic approval limits) instead of declining.

**Operational**

6. **LLM or provider failure** (timeout, invalid tool call). The system fails closed with an explicit error and never returns a partial, unsupported answer. `/summary` is unaffected.

### 1.5 Success criteria (prototype)

These are measured on a small labelled evaluation set if time permits: 8–10 questions covering the four question types, plus at least one ambiguous and one unanswerable question. Expected answers are computed independently of the application code, and the scoring script runs each question 3 times.

| Priority | Area | Metric | Target |
|---|---|---|---|
| 1 | LLM interpretation | Correct route (data / policy / both), filters, and the answer-vs-clarify-vs-decline decision | 100% on the 3 brief examples; ≥ 90% overall |
| 2 | Numerical accuracy | Figures match expected values within ± EUR 0.01 | 100% |
| 3 | Evidence validity | Every cited invoice ID and clause exists, and the cited rows equal the rows used in the computation | 100% |
| 3 | Answer consistency | Every figure in the answer text matches the computed result | 100% |
| 4 | Unsupported conclusions | Answers asserting compliance or authority that the data cannot establish | 0 |
| — | Reproducibility | Same figures and evidence across 3 runs | 100% |
| — | Failure handling | With the LLM unavailable, `/ask` returns an explicit error and `/summary` still loads | Pass |

Each answer in the UI shows its evidence rows, policy clauses, FX rates used, and assumptions or limitations.

### 1.6 Production considerations (not built)

- **Pilot:** run alongside the existing manual review and measure agreement with analysts, false flags, missed exceptions, critical errors, and time saved.
- **Audit trail (§8.2):** log the question, interpretation, tool calls, and the data, policy, FX, prompt, and model versions for each answer.
- **Operations:** access control, latency, and cost per question at roughly 200 users.

## 2. data

### 1. Data structure: 
I saw 140 invoices from invoices_2025_clean.csv, some are in EUR, GBP, USD. some has missing PO number or approver. some are negative amounts. 

vendor_master.csv has 10 venders. need to join with invoice table.

what information I missed for data description? if not complete the section.
### 2. Policy:

After review the policy, i need to mapping policy to computable rules to data.

1. EUR 1000 or above needs approval. need conversion for currency
2. Require approval level based on amount/segregation of duties.
3. needs to record approver
4. vendor needs to be active
5. credit note
6. emergency expectations

what information I missed for policy? if not complete the section and list out specific data need for each rule. if the rule cannot be translated, i need explanation.

### 3. Business questions:

These could be problematic questions may need specific definition. For this prototype, i will make the decision but need to work with Finance and procurement in real world.

1.  is the spending invoice amounts? approved amounts? I will pick invoice amounts for this task
2. how the conversion rate be used? i will use the conversion rate on the date of the invoice for this task.
3. how should ambiguous questions be handled? consulting could be vendors or consulting service. I will ask the assistant to give prompts for clarification instead of assumption.
4. what consider a good answer? needs to have deterministic evidence.

what information I missed for questions? if not complete the section and list out specific data need for each rule. if the rule cannot be translated, i need explanation.
### 4. system design principle:

1. all functions need deterministic function and evidence, including not limit to currency conversion, evaluation metrics and policy requriement
2. LLM should interpret users question but cannot imply what users are ambigurous
3. the assistant should always validate the available data and response rather than silently filling missing data.
4. when information is insufficient, system needs to clarify with user.
5. architecture should seperate data access, policy handling, question interpretation, tool/skills calling.

what information I missed for system design? if not complete the section and list out specific data need for each rule. if the rule cannot be translated, i need explanation.
