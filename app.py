"""AuditFlow - Streamlit UI.  Run with:  streamlit run app.py"""
import os
import time

import pandas as pd
import streamlit as st

from agents import evaluate, run_audit
from checks import load_claims, load_policy

st.set_page_config(page_title="AuditFlow", page_icon="🧾", layout="wide")

REQUIRED_COLS = ["claim_id", "employee_id", "employee_name", "department", "category", "vendor",
                 "amount", "expense_date", "submitted_date", "receipt_attached", "invoice_no",
                 "approval_note", "description"]
ICONS = {"User": "🙋", "Planner": "🧭", "Policy Agent": "📚", "Executor": "⚙️",
         "Critic": "🕵️", "Decision Agent": "⚖️"}
DECISION_LABEL = {"ESCALATE": "🔴 ESCALATE", "FLAG": "🟠 FLAG", "CLEARED": "🟢 CLEARED"}
EXAMPLES = [
    "Audit all Q3 expense claims for all policy violations",
    "Check duplicate and split billing claims",
    "Check weekend and late submission claims for Sales department",
    "Audit Travel claims for limit violations and missing receipts",
]


@st.cache_data
def get_policy():
    return load_policy()


def get_truth():
    return pd.read_csv("ground_truth.csv") if os.path.exists("ground_truth.csv") else None


# ------------------------------------------------------------------ Sidebar
st.sidebar.title("🧾 AuditFlow")
st.sidebar.caption("Multi-agent expense compliance auditing (PoC, synthetic data)")
source = st.sidebar.radio("Data source", ["Generated sample (expense_claims.csv)", "Upload CSV"])

claims, using_sample = None, False
if source.startswith("Generated"):
    if os.path.exists("expense_claims.csv"):
        claims, using_sample = load_claims("expense_claims.csv"), True
    else:
        st.sidebar.error("expense_claims.csv not found. Run `python data_generator.py` first.")
else:
    up = st.sidebar.file_uploader("Claims CSV", type="csv")
    if up is not None:
        try:
            df_up = load_claims(up)
        except Exception:
            df_up = None
        if df_up is None or any(c not in df_up.columns for c in REQUIRED_COLS):
            st.sidebar.error("Incorrect dataset")
        else:
            claims = df_up

policy = get_policy()
with st.sidebar.expander("Policy rules in force"):
    for r in policy["rules"]:
        st.markdown(f"**{r['id']} {r['name']}**  \n{r['text']}")
    st.json(policy["params"]["category_limits"])

# ------------------------------------------------------------------ Main
st.title("AuditFlow: Agentic Expense Compliance Auditor")
st.write("Describe the audit you want. A team of agents plans it, checks the policy, runs the checks, "
         "verifies every flag, and produces a risk-ranked report.")

if claims is None:
    st.info("Load a dataset from the sidebar to begin.")
    st.stop()

st.session_state.setdefault("req", EXAMPLES[0])


def _use_example():
    st.session_state["req"] = st.session_state["example"]


st.selectbox("Quick examples", EXAMPLES, key="example", on_change=_use_example)
request = st.text_area("Audit request", key="req", height=80)

with st.expander(f"Preview claims ({len(claims)} rows)"):
    st.dataframe(claims, width="stretch", height=250)

if st.button("▶ Run audit", type="primary"):
    result = run_audit(request, claims, policy)
    st.session_state["result"] = result
    st.session_state["result_is_sample"] = using_sample
    with st.status("Agents at work...", expanded=True) as status:
        for e in result["trace"].events:
            st.write(f"{ICONS.get(e['agent'], '•')} **{e['agent']}**: {e['action']}  \n`{e['detail']}`")
            time.sleep(0.35)
        status.update(label="Audit complete", state="complete", expanded=False)

result = st.session_state.get("result")
if result is None:
    st.stop()

dec, reviewed = result["decisions"], result["reviewed"]
n_esc = int((dec["decision"] == "ESCALATE").sum()) if not dec.empty else 0
n_flag = int((dec["decision"] == "FLAG").sum()) if not dec.empty else 0
n_clear = int((dec["decision"] == "CLEARED").sum()) if not dec.empty else 0
at_risk = float(dec.loc[dec["decision"] != "CLEARED", "amount"].sum()) if not dec.empty else 0.0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Claims audited", len(result["scope"]))
c2.metric("🔴 Escalated", n_esc)
c3.metric("🟠 Flagged", n_flag)
c4.metric("🟢 Cleared by Critic", n_clear)
c5.metric("Amount at risk", f"Rs {at_risk:,.0f}")

tab_trace, tab_res, tab_critic, tab_val, tab_rep = st.tabs(
    ["🧭 Plan & agent trace", "📋 Results", "🕵️ Critic review", "✅ Validation", "📄 Report"])

with tab_trace:
    st.subheader("Plan created by the Planner")
    st.json(result["plan"])
    st.subheader("Policy rules retrieved")
    for r in result["rules"]:
        st.markdown(f"- **{r['id']} {r['name']}**: {r['text']}")
    st.subheader("Agent trace")
    t = result["trace"].df()
    t.insert(1, "", t["agent"].map(ICONS))
    st.dataframe(t, width="stretch", hide_index=True)

with tab_res:
    if dec.empty:
        st.success("No violations found for this request.")
    else:
        pick = st.multiselect("Show decisions", list(DECISION_LABEL), default=["ESCALATE", "FLAG"])
        view = dec[dec["decision"].isin(pick)].copy()
        view["decision"] = view["decision"].map(DECISION_LABEL)
        st.dataframe(view, width="stretch", hide_index=True)
        confirmed = reviewed[reviewed["verdict"] == "CONFIRMED"]
        if not confirmed.empty:
            st.subheader("Confirmed violations by type")
            st.bar_chart(confirmed["violation_type"].value_counts())

with tab_critic:
    st.write("The Critic re-checks every Executor flag against the raw claim. "
             "Dismissed flags are false positives (e.g. a different invoice number, or a valid approval reference).")
    if reviewed.empty:
        st.info("Nothing to review.")
    else:
        mode = st.radio("Show", ["All", "Dismissed only", "Confirmed only"], horizontal=True)
        v = reviewed
        if mode == "Dismissed only":
            v = v[v["verdict"] == "DISMISSED"]
        elif mode == "Confirmed only":
            v = v[v["verdict"] == "CONFIRMED"]
        st.dataframe(v[["claim_id", "violation_type", "verdict", "confidence", "critic_note", "reason"]],
                     width="stretch", hide_index=True)

with tab_val:
    truth = get_truth() if st.session_state.get("result_is_sample") else None
    if truth is None:
        st.info("Validation needs the generated sample data (ground_truth.csv holds the planted violations).")
    else:
        m = evaluate(result["raw_flags"], reviewed, truth, result["scope"])
        st.write(f"Planted violations in scope: **{m['planted']}**")
        table = pd.DataFrame({
            "Setup": ["Executor only (rules)", "Executor + Critic"],
            "Flags": [m["executor_only"]["flags"], m["with_critic"]["flags"]],
            "Correct": [m["executor_only"]["correct"], m["with_critic"]["correct"]],
            "Precision": [round(m["executor_only"]["precision"], 2), round(m["with_critic"]["precision"], 2)],
            "Recall": [round(m["executor_only"]["recall"], 2), round(m["with_critic"]["recall"], 2)],
        })
        st.dataframe(table, width="stretch", hide_index=True)
        st.bar_chart(table.set_index("Setup")[["Precision", "Recall"]])

    with st.expander("Limitations and future scope"):
        st.markdown("""
**Limitations**
- Synthetic data with known planted patterns, so scores are optimistic; real claims are messier.
- Rule-based "brain" (no LLM), so the Planner only understands a fixed set of keywords.
- Policy is a small JSON file, not a full policy document with retrieval.
- Critic relies on invoice numbers and approval references being present and well-formed.

**Future scope**
- Local LLM (Ollama) for the Planner and for explaining flags in natural language.
- Retrieval (RAG) over the real policy PDF.
- OCR of receipts, ML anomaly detection for unknown fraud patterns.
- Human-in-the-loop approvals and integration with ERP/finance systems.
""")

with tab_rep:
    st.write(result["summary"])
    lines = ["AUDITFLOW REPORT", "=" * 40, result["summary"], "",
             f"Request: {request}", f"Checks run: {', '.join(result['plan']['checks'])}", "",
             "Top risks:"]
    for _, r in dec[dec["decision"] != "CLEARED"].head(15).iterrows():
        lines.append(f"- {r['claim_id']} | {r['employee']} | Rs {r['amount']:,.2f} | {r['violations']} "
                     f"| risk {r['risk_score']} | {r['decision']}")
    report_txt = "\n".join(lines)
    st.text(report_txt)
    d1, d2 = st.columns(2)
    d1.download_button("⬇ Decisions (CSV)", dec.to_csv(index=False), "audit_decisions.csv", "text/csv")
    d2.download_button("⬇ Report (TXT)", report_txt, "audit_report.txt", "text/plain")