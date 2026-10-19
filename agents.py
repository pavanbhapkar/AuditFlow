"""AuditFlow - agents + orchestrator.

Flow: Planner -> Policy agent -> Executor -> Critic -> Decision/Report agent
Each agent is a plain function that logs what it did to a shared Trace (shown in the UI).
The "brain" is rule-based (mock LLM), so the demo is deterministic and needs no API.
"""
import re

import pandas as pd

from checks import CHECKS, run_checks

# Planner: keywords in the audit request -> which checks to run
KEYWORDS = {
    "duplicate": "DUPLICATE", "limit": "OVER_LIMIT", "overspend": "OVER_LIMIT",
    "split": "SPLIT_BILLING", "weekend": "WEEKEND", "receipt": "NO_RECEIPT",
    "late": "LATE_SUBMISSION", "deadline": "LATE_SUBMISSION",
}
APPROVAL_RE = re.compile(r"APR-\d{4}")  # a valid exception approval reference
RISK_BASE = {"DUPLICATE": 75, "SPLIT_BILLING": 80, "OVER_LIMIT": 55,
             "NO_RECEIPT": 50, "WEEKEND": 40, "LATE_SUBMISSION": 30}


class Trace:
    """Shared log of what each agent did (drives the live agent trace in the UI)."""

    def __init__(self):
        self.events = []

    def log(self, agent, action, detail=""):
        self.events.append({"step": len(self.events) + 1, "agent": agent,
                            "action": action, "detail": detail})

    def df(self):
        return pd.DataFrame(self.events)


# ---------------------------------------------------------------- Planner
def planner_agent(request, claims, trace):
    text = request.lower()
    checks = list(dict.fromkeys(name for kw, name in KEYWORDS.items() if kw in text)) or list(CHECKS)
    dept = next((d for d in claims["department"].unique() if d.lower() in text), None)
    cat = next((c for c in claims["category"].unique() if c.lower() in text), None)
    plan = {"checks": checks, "department": dept, "category": cat}
    trace.log("Planner", "Created audit plan",
              f"Checks: {', '.join(checks)} | Department: {dept or 'All'} | Category: {cat or 'All'}")
    return plan


# ---------------------------------------------------------------- Policy (research) agent
def policy_agent(plan, policy, trace):
    rules = [r for r in policy["rules"] if r["type"] in plan["checks"]]
    trace.log("Policy Agent", "Retrieved policy rules",
              f"{len(rules)} rules: " + ", ".join(f"{r['id']} ({r['name']})" for r in rules))
    return rules


# ---------------------------------------------------------------- Executor
def executor_agent(plan, claims, policy, trace):
    scope = claims
    if plan["department"]:
        scope = scope[scope["department"] == plan["department"]]
    if plan["category"]:
        scope = scope[scope["category"] == plan["category"]]
    trace.log("Executor", "Selected claims in scope", f"{len(scope)} of {len(claims)} claims")

    frames = []
    for name in plan["checks"]:
        f = run_checks(scope, policy["params"], selected=[name])
        trace.log("Executor", f"Ran check {name}", f"{len(f)} flags raised")
        frames.append(f)
    flags = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return scope, flags


# ---------------------------------------------------------------- Critic
def critic_agent(flags, claims, trace):
    """Re-verify every flag against the raw claim row. Dismiss false positives."""
    by_id = claims.set_index("claim_id")
    verdicts, confs, notes = [], [], []
    for _, f in flags.iterrows():
        row = by_id.loc[f["claim_id"]]
        note = str(row["approval_note"]).strip()
        vtype = f["violation_type"]
        verdict, conf, why = "CONFIRMED", 0.95, "Rule breach verified on raw claim data"

        if vtype == "DUPLICATE":
            other = by_id.loc[f["related_claim"]]
            if row["invoice_no"] != other["invoice_no"]:
                verdict, conf = "DISMISSED", 0.90
                why = f"Different invoice numbers ({row['invoice_no']} vs {other['invoice_no']}): separate purchase"
            else:
                conf, why = 0.98, f"Identical invoice number {row['invoice_no']} as {f['related_claim']}"
        elif vtype in ("OVER_LIMIT", "WEEKEND", "LATE_SUBMISSION"):
            if APPROVAL_RE.search(note):
                verdict, conf = "DISMISSED", 0.92
                why = f"Valid exception approval on file: {APPROVAL_RE.search(note).group()}"
            elif note:
                conf, why = 0.90, f"Approval note '{note}' has no valid APR reference"
        elif vtype == "SPLIT_BILLING":
            conf, why = 0.88, f"Pattern with {f['related_claim']} confirmed"

        verdicts.append(verdict); confs.append(conf); notes.append(why)

    reviewed = flags.copy()
    reviewed["verdict"], reviewed["confidence"], reviewed["critic_note"] = verdicts, confs, notes
    dismissed = (reviewed["verdict"] == "DISMISSED").sum()
    trace.log("Critic", "Reviewed all flags",
              f"{len(reviewed)} reviewed: {len(reviewed) - dismissed} confirmed, {dismissed} dismissed as false positives")
    return reviewed


# ---------------------------------------------------------------- Decision / Report agent
def decision_agent(reviewed, claims, policy, trace):
    by_id = claims.set_index("claim_id")
    limits = policy["params"]["category_limits"]
    rows = []
    for cid, g in reviewed.groupby("claim_id"):
        c = by_id.loc[cid]
        conf = g[g["verdict"] == "CONFIRMED"]
        if conf.empty:
            score, decision = 0, "CLEARED"
            vtypes, reasons = "-", " | ".join(g["critic_note"])
        else:
            score = max(RISK_BASE[v] for v in conf["violation_type"]) + 10 * (len(conf) - 1)
            if "OVER_LIMIT" in set(conf["violation_type"]):
                score += min(20, max(0, c["amount"] / limits[c["category"]] - 1) * 30)
            score = int(min(100, round(score)))
            decision = "ESCALATE" if score >= 70 else "FLAG"
            vtypes = ", ".join(conf["violation_type"])
            reasons = " | ".join(conf["reason"])
        rows.append({"claim_id": cid, "employee": c["employee_name"], "department": c["department"],
                     "category": c["category"], "amount": c["amount"], "violations": vtypes,
                     "risk_score": score, "decision": decision, "explanation": reasons})
    cols = ["claim_id", "employee", "department", "category", "amount",
            "violations", "risk_score", "decision", "explanation"]
    out = pd.DataFrame(rows, columns=cols)
    if not out.empty:
        out = out.sort_values("risk_score", ascending=False).reset_index(drop=True)

    n_esc = (out["decision"] == "ESCALATE").sum() if not out.empty else 0
    n_flag = (out["decision"] == "FLAG").sum() if not out.empty else 0
    n_clear = (out["decision"] == "CLEARED").sum() if not out.empty else 0
    at_risk = out.loc[out["decision"] != "CLEARED", "amount"].sum() if not out.empty else 0
    summary = (f"Audit complete: {n_esc} claims escalated, {n_flag} flagged for review, "
               f"{n_clear} cleared by the Critic. Total amount at risk: Rs {at_risk:,.2f}.")
    trace.log("Decision Agent", "Scored claims and generated report", summary)
    return out, summary


# ---------------------------------------------------------------- Validation
def evaluate(raw_flags, reviewed, truth, scope):
    """Precision/recall of Executor alone vs Executor + Critic, against the planted ground truth."""
    actual = set(zip(truth["claim_id"], truth["violation_type"]))
    actual = {a for a in actual if a[0] in set(scope["claim_id"])}

    def pr(flag_df):
        found = set(zip(flag_df["claim_id"], flag_df["violation_type"]))
        tp = len(found & actual)
        return {"flags": len(found), "correct": tp,
                "precision": tp / max(len(found), 1), "recall": tp / max(len(actual), 1)}

    return {"planted": len(actual), "executor_only": pr(raw_flags),
            "with_critic": pr(reviewed[reviewed["verdict"] == "CONFIRMED"])}


# ---------------------------------------------------------------- Orchestrator
def run_audit(request, claims, policy):
    trace = Trace()
    trace.log("User", "Audit request", request)
    plan = planner_agent(request, claims, trace)
    rules = policy_agent(plan, policy, trace)
    scope, raw_flags = executor_agent(plan, claims, policy, trace)
    reviewed = critic_agent(raw_flags, claims, trace)
    decisions, summary = decision_agent(reviewed, claims, policy, trace)
    return {"plan": plan, "rules": rules, "scope": scope, "raw_flags": raw_flags,
            "reviewed": reviewed, "decisions": decisions, "summary": summary, "trace": trace}


if __name__ == "__main__":
    from checks import load_claims, load_policy

    claims, policy = load_claims(), load_policy()
    truth = pd.read_csv("ground_truth.csv")
    result = run_audit("Audit all Q3 expense claims for all policy violations", claims, policy)

    print(result["trace"].df()[["step", "agent", "action", "detail"]].to_string(index=False))
    print()
    print(result["decisions"].head(8)[["claim_id", "violations", "risk_score", "decision"]].to_string(index=False))
    print()
    m = evaluate(result["raw_flags"], result["reviewed"], truth, result["scope"])
    for k in ("executor_only", "with_critic"):
        v = m[k]
        print(f"{k:14s} flags={v['flags']:3d} correct={v['correct']:3d} "
              f"precision={v['precision']:.2f} recall={v['recall']:.2f}")
