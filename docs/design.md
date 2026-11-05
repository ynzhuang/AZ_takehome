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

## 2. Data, policy, and business definitions

All figures in this section were verified against the provided files (`invoices_2025_clean.csv`, `vendor_master.csv`, `procurement_policy.md`). Assumptions marked **[A]** are prototype decisions that would need confirmation from Finance and Procurement in the real world.

### 2.1 Data structure

**Invoices** (`invoices_2025_clean.csv`): 140 invoices, one row per invoice.
- Columns: `invoice_number, invoice_date, vendor_id, vendor_name, description, amount, currency, cost_center, po_number, approved_by`.
- Dates run from 2025-01-03 to 2025-12-26.
- Currencies: 89 EUR, 27 GBP, 24 USD. Native net totals are EUR 4,493,726.12, GBP 1,182,251.02 and USD 1,150,445.53. These cannot be added together without conversion.
- There are no duplicate invoice numbers and no duplicate PO numbers (119 invoices have a PO).

**Vendors** (`vendor_master.csv`): 10 vendors with country, category, and status (8 ACTIVE, 1 INACTIVE, 1 ON_HOLD).
- Every invoice `vendor_id` matches a vendor, and `vendor_name` is identical in both files.
- Joins use `vendor_id`, and vendor attributes come from the vendor master.

**Findings that affect the design**

| Finding | Implication |
|---|---|
| 21 invoices have no PO, 25 have no approver, and 3 have neither | Core inputs to the policy checks (§2.1, §3.1) |
| 29 invoices come from non-ACTIVE vendors: PharmaPrint Studios is INACTIVE (17 invoices, Jan–Nov 2025) and Crown Travel Partners is ON_HOLD (12 invoices, Feb–Oct 2025) | Vendor status is a **current snapshot with no effective date**, so we cannot tell whether a vendor was inactive when it invoiced |
| 2 credit notes, both from Helix Consulting AG (V-1005): INV-20029 (EUR −52,749.39, "Cloud hosting") and INV-20096 (USD −29,534.55, "Conference & travel") | There is no reference to the original invoice. INV-20029 has no positive Helix "Cloud hosting" invoice to match against. INV-20096 has one plausible candidate (INV-20131, USD 85,116.01) |
| Invoice descriptions are independent of vendor category. For example, the Travel vendor bills software licences | "Consulting spend" is ambiguous: by vendor category it is 14 invoices, by description 13 invoices, with **no overlap** |
| `approved_by` is a username (5 people, or blank). All 5 approve invoices with a native amount above 50,000 | We cannot verify whether an approver had sufficient authority |
| `cost_center` is a code (5 cost centres) with no owner | For §2.2 we can name the cost centre to flag, but not its owner |
| **No FX rates are provided.** 35 invoices fall on weekends or TARGET holidays (16 of them non-EUR) | A rule for dates with no published rate is needed (see 2.3) |
| 13 non-EUR invoices have a native amount between 0.8× and 1.25× an approval or PO threshold | The approval tier can change after conversion, so tiers must be applied to the EUR amount, never the native amount |
| Quarter labels in descriptions ("Q1 consulting services") match the invoice dates | No conflict to handle |

### 2.2 Policy: mapping rules to the data

My original list was: (1) EUR 1,000+ needs approval, (2) approval level by amount and segregation of duties, (3) record the approver, (4) vendor must be active, (5) credit notes, and (6) emergency exceptions. After checking it against the policy text, two corrections are needed:
- Rule 1 requires a **PO** (§2.1), not approval. Approval applies at every amount (§3).
- Rule 2 combines two separate rules: approval tiers (§3) and segregation of duties (§7).

Rules are classified as **checkable**, **partially checkable**, or **cannot be determined** from the available data.

**Checkable**

| Rule | Check | Data used |
|---|---|---|
| §3 Required approver tier | Map the EUR amount to a tier: ≤ 5,000 Cost Center Owner; 5,000.01–50,000 Department Head; 50,000.01–250,000 Finance Director; > 250,000 CFO + Procurement Excellence | `amount`, `currency`, `invoice_date`, FX table |
| §3.1 Approver must be recorded | A blank `approved_by` means hold, do not pay (25 invoices) | `approved_by` |
| §5.1 Report in EUR | Convert at the ECB rate for the invoice date | `amount`, `currency`, `invoice_date`, FX table |

**Partially checkable**

| Rule | What we can check | What we cannot |
|---|---|---|
| §2.1 PO required at ≥ EUR 1,000 | Whether a PO number is present (21 missing; the smallest is EUR 2,026.78, so all 21 are above the threshold) | Whether the PO is valid, or was issued **before** the work started (no PO master or PO dates) |
| §2.2 Flag maverick spend | The list of invoices without a PO, grouped by cost centre and quarter | The cost-centre owner; whether the invoice was already reported |
| §4.1 ACTIVE vendors only | Invoices from vendors that are **currently** INACTIVE or ON_HOLD (29). The required action is escalation to Procurement Excellence, not rejection | Vendor status at the invoice date; whether the invoice was processed or paid |
| §6.1 Credit notes | Identify negative amounts (2) | Matching to the original invoice (no reference); 90-day ageing (no matching status) |

**Cannot be determined**

| Rule | Why not | Data needed |
|---|---|---|
| §3 Approver had sufficient authority | No mapping from approver to role | Approver role and authority matrix |
| §7.1–7.2 Segregation of duties / no self-approval | No requester field | Requester ID per purchase |
| §2.3 Blanket POs for recurring services | No PO master. 10 of the 21 no-PO invoices are recurring services (6 cloud hosting, 4 facilities) and *may* be covered | PO master with blanket PO coverage |
| §9 Emergency exceptions | No exception register | Exception Register |
| §5.2 Payment terms (60 days from receipt) | No receipt or payment dates | Receipt and payment dates |
| §4.2–4.3 Vendor due diligence and master-data changes | No vendor onboarding or change history | Due diligence records, change log |
| §1 Exclusion of clinical trial site payments | No field identifies them | Spend type flag. **[A]** None of these invoices are site payments |
| §8.1–8.2 Retention and lineage | Not data rules | These are system requirements; see 2.4 |

**Consequence:** because §2.3 and §9 cannot be checked, any no-PO invoice is reported as a **potential exception for review**, never as a confirmed violation.

### 2.3 Business definitions and assumptions

My original questions, with the decisions taken:

1. **What is "spend"?** Net invoiced amount in EUR: the sum of all invoice amounts, including credit notes and invoices of every status (missing PO, missing approver, non-ACTIVE vendor). It is not the approved or paid amount, since that data does not exist. Year = `invoice_date`. Group by `vendor_id`.
2. **Currency conversion**
   - Use the ECB euro reference rate for the invoice date. ECB quotes currency per 1 EUR, so EUR amount = amount ÷ rate.
   - **[A]** On dates with no published rate (weekends, TARGET holidays), use the most recent prior published rate.
   - **[A]** Round each converted amount to 2 decimal places; totals are sums of the rounded amounts.
   - Rates are stored in a versioned local file with its source and retrieval date. They are never fetched at question time.
   - If a rate is missing, that invoice is reported as unconverted and excluded, with the exclusion disclosed. The system never guesses a rate.
3. **Approval thresholds**
   - Tiers are applied to the converted EUR amount, with upper bounds inclusive (EUR 5,000.00 is Cost Center Owner).
   - **[A]** Credit notes get no approval tier or PO check; they are reported under §6.
   - **[A]** If a policy question gives a non-EUR amount with no date, use the latest available rate and disclose it. If the tier would differ anywhere within the range of 2025 rates, ask for the date.
4. **Ambiguous questions:** ask for clarification when plausible interpretations select different invoices or lead to a different policy conclusion. Example: "consulting spend" by vendor category versus by description. Otherwise answer and state the assumption.
5. **Insufficient data:** say what cannot be determined, why (the missing field), and what would be needed. Examples: approver authority, segregation of duties, credit-note matching, years other than 2025.
6. **Answering "who can approve":** return a role from the policy (e.g. EUR 60,000 means Finance Director, §3). Never name a person, because the data has no roles.
7. **What counts as a good answer:** defined in Section 1.5.

### 2.4 System design principles

1. All calculations (currency conversion, aggregation, threshold tiers, policy checks) are done by deterministic functions that return their evidence: rows, clause IDs, FX rates, and assumptions.
2. The LLM interprets the question and selects tools and parameters. It never does arithmetic, never selects rows itself, and never resolves a material ambiguity by guessing.
3. The assistant validates the available data and reports gaps, rather than silently filling missing values.
4. When information is insufficient, the system asks the user to clarify (ambiguous question) or explains what is missing (insufficient data).
5. The architecture keeps data access, policy rules, question interpretation, and tool calling separate.
6. Policy clauses have stable IDs (e.g. §3, §4.1), and every citation is checked against the clause list and the tool output before the answer is returned.
7. The system fails closed: if the LLM or a tool fails, it returns an explicit error, never a partial answer.

### 2.5 Real-world considerations (documented, not built)

| Challenge | Prototype | Production approach |
|---|---|---|
| **Longer policies** | ~50 lines loaded directly into the prompt | Split by clause with stable IDs; retrieve relevant clauses (keyword or vector search) once the policy no longer fits in context; still cite clause IDs |
| **More rules** | A few rules written in code | A rule registry stored as data: clause ID, logic, required fields, whether it can be checked, version. Rules extracted with an LLM are reviewed by people before use, never applied directly |
| **Dirty data** | Clean data; validated on load | A validation layer at ingestion (schema, types, currency codes, dates, duplicates, unknown vendors, sign conventions). Bad rows are quarantined and reported, and data-quality issues appear in the answer |
| **Policy changes** | One policy version (v3.2, effective 2025-01-01) | Version policies by effective date; check each invoice against the policy in force on its date; stamp the policy version on every answer |
| **Vendor status history** | Current snapshot only | A status history with effective dates, to check status at the invoice or payment date |
| **FX** | Static ECB file | A scheduled, versioned rate import with gap and anomaly checks |

