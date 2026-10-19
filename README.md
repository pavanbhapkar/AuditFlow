# AuditFlow: Agentic Expense Compliance Auditor

CA-3 Agentic AI Proof of Concept, Symbiosis Institute of Technology, Pune.
Author: Pavan Bhapkar (PRN 24070126137, AIML 24-28 B3)

## Problem
Auditors check expense claims manually against policy, usually on a small sample, so duplicates,
over-limit spending, split billing and policy breaches slip through. AuditFlow uses a team of
agents to audit every claim, verify each flag, and produce a risk-ranked report.

## Agents
| Agent | Role |
|---|---|
| Planner | Reads the audit request, picks the checks and the claims in scope |
| Policy Agent | Retrieves the matching rules from `policy.json` |
| Executor | Runs the six pandas rule checks and raises flags |
| Critic | Re-verifies each flag on the raw claim, dismisses false positives |
| Decision Agent | Scores risk, assigns ESCALATE / FLAG / CLEARED, writes the report |

Flow: request -> Planner -> Policy Agent -> Executor -> Critic -> Decision Agent -> report

## Run it
```
python -m venv venv
venv\Scripts\activate          # Windows (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
python data_generator.py       # creates expense_claims.csv and ground_truth.csv
python -m streamlit run app.py
```
Optional: `python agents.py` runs the pipeline in the terminal and prints precision/recall.

## Files
- `app.py`: Streamlit UI (agent trace, results, critic review, validation, report download)
- `agents.py`: the five agents and the orchestrator
- `checks.py`: the six rule checks (the Executor's tools)
- `data_generator.py`: synthetic claims with planted violations (seeded, reproducible)
- `policy.json`: audit rules and limits
- `docs/`: presentation and project report

## Results (synthetic data)
330 claims, 55 planted violations. Executor alone: 70 flags, precision 0.79, recall 1.00.
With the Critic: 55 flags, precision 1.00, recall 1.00. Scores are optimistic because the data
is synthetic with known patterns.

## Limitations and future scope
Rule-based agent logic (no LLM), small JSON policy, synthetic data. Future: local LLM via
Ollama, RAG over the real policy document, receipt OCR, ML anomaly detection, human-in-the-loop approvals.
