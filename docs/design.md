## 1 Problem and objective
### 1. What is the business problem?

The Finance team needs to review invoice data against procedure policy, including approval, purchase, vendor, and etc.  The goal is to build a chatbot for analyst to use to check across multiple sources and determine which invoice need further action. 

Users could have different access and questions based on their role. For analyst, questions may include calculations. for approvers, questions may include what invoice need to approve. for auditor, questions may ask invoice need further validation.

The chatbot is a decision-support tool and should not automatically make decisions over human.

### 2. How to evaluate the correctness of the answer?

a. Numerical correctness: Currency conversion has to be correct using defined conversion method. Results should be traceable to the underlying invoice records and conversion method.

b. Interpretation correctness: The system must answer the question the analyst intended. For example, "consulting spend" could refer to vendors classified as consulting companies or invoices describing consulting services. If unsure about user's intent, then need to prompt user to answer follow up question to clarify

c. Evidence correctness: the system need to distinguish what data shows and what policy requires. For example, an invoice with approver does not mean the approver has sufficient authority. When the data is insufficient to support conclusion, the assistant should explain to the user instead of assume an answer.

Each response, the assistant need to use deterministic functions, records, policy clauses to draw the conclusion and let the user know. 

### 3. What are the most consequential failure modes?

a. incorrect financial calculations, for example, aggregating different currencies without conversion, selecting the wrong invoices, or mishandling approval thresholds.

b. incorrect interpretation. For example, the assistant uses the definition or filters but got a correct result. This still considered failure.

c. an unsupported compliance conclusion. For example, the assistant might claim that an invoice was properly approved even though the dataset does not contain the approver's organizational role.

### 4. Evaluation metrics?

a. LLM: I would test whether the model correctly interprets different questions, identifies the appropriate data or policy operation, extracts filters, and recognizes ambiguous requests.

b. system evaluation: use a small set of reference questions with independently verified expected results. Including but not limit to numerical accuracy, policy interpretation, evidence attribution, and missing info.

c. product evaluation. A technically correct answer is not necessarily useful to a Finance analyst. I would assess whether the interface clearly presents the answer, supporting records, relevant policy clauses, and limitations.

This is a prototype, so i will use synthetic data to test. for a real deployment, I would extend this evaluation through expert review and a controlled pilot alongside the existing manual workflow. I would measure agreement with analyst decisions, task completion, time saved, false flags, missed exceptions, and critical errors.

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
