"""AuditFlow - rule checks (the Executor agent's tools).

Each check takes the claims DataFrame + policy params and returns a list of flags.
A flag = {claim_id, rule_id, violation_type, reason}
"""
import json

import pandas as pd


def load_policy(path="policy.json"):
    with open(path) as f:
        return json.load(f)


def load_claims(path="expense_claims.csv"):
    df = pd.read_csv(path, parse_dates=["expense_date", "submitted_date"])
    df["approval_note"] = df["approval_note"].fillna("")
    return df


def _flag(row, rule_id, vtype, reason, related=None):
    return {"claim_id": row["claim_id"], "rule_id": rule_id,
            "violation_type": vtype, "reason": reason, "related_claim": related}


def check_over_limit(df, p):
    flags = []
    for _, r in df.iterrows():
        limit = p["category_limits"][r["category"]]
        if r["amount"] > limit:
            flags.append(_flag(r, "R1", "OVER_LIMIT",
                               f"Amount {r['amount']:.2f} exceeds {r['category']} limit of {limit}"))
    return flags


def check_duplicates(df, p):
    flags = []
    first_seen = {}
    for _, r in df.sort_values("submitted_date").iterrows():
        key = (r["employee_id"], r["vendor"], r["amount"], r["expense_date"])
        if key in first_seen:
            orig = first_seen[key]
            flags.append(_flag(r, "R2", "DUPLICATE",
                               f"Same employee, vendor, amount and date as {orig}", related=orig))
        else:
            first_seen[key] = r["claim_id"]
    return flags


def check_split_billing(df, p):
    flags, seen = [], set()
    window = p["split_window_days"]
    for (_, _, cat), g in df.groupby(["employee_id", "vendor", "category"]):
        limit = p["category_limits"][cat]
        g = g.sort_values("expense_date").reset_index(drop=True)
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = g.loc[i], g.loc[j]
                gap = (b["expense_date"] - a["expense_date"]).days
                if gap > window:
                    break
                if a["amount"] <= limit and b["amount"] <= limit and a["amount"] + b["amount"] > limit:
                    for r, other in ((a, b), (b, a)):
                        if r["claim_id"] not in seen:
                            seen.add(r["claim_id"])
                            flags.append(_flag(r, "R3", "SPLIT_BILLING",
                                               f"Combined with {other['claim_id']} within {window} days = "
                                               f"{a['amount'] + b['amount']:.2f}, above limit {limit}",
                                               related=other["claim_id"]))
    return flags


def check_weekend(df, p):
    flags = []
    for _, r in df.iterrows():
        if r["expense_date"].dayofweek >= 5 and r["category"] not in p["weekend_allowed_categories"]:
            flags.append(_flag(r, "R4", "WEEKEND",
                               f"{r['category']} expense on a weekend ({r['expense_date'].date()})"))
    return flags


def check_no_receipt(df, p):
    flags = []
    for _, r in df.iterrows():
        if r["amount"] > p["receipt_required_above"] and r["receipt_attached"] == "No":
            flags.append(_flag(r, "R5", "NO_RECEIPT",
                               f"No receipt for claim of {r['amount']:.2f} (limit {p['receipt_required_above']})"))
    return flags


def check_late_submission(df, p):
    flags = []
    for _, r in df.iterrows():
        delay = (r["submitted_date"] - r["expense_date"]).days
        if delay > p["max_submission_delay_days"]:
            flags.append(_flag(r, "R6", "LATE_SUBMISSION",
                               f"Submitted {delay} days after expense (max {p['max_submission_delay_days']})"))
    return flags


# Registry: rule type -> function. The Planner will pick from these.
CHECKS = {
    "OVER_LIMIT": check_over_limit,
    "DUPLICATE": check_duplicates,
    "SPLIT_BILLING": check_split_billing,
    "WEEKEND": check_weekend,
    "NO_RECEIPT": check_no_receipt,
    "LATE_SUBMISSION": check_late_submission,
}


def run_checks(df, p, selected=None):
    """Run the selected checks (default: all) and return flags as a DataFrame."""
    flags = []
    for name in (selected or CHECKS):
        flags += CHECKS[name](df, p)
    return pd.DataFrame(flags, columns=["claim_id", "rule_id", "violation_type", "reason", "related_claim"])


if __name__ == "__main__":
    policy = load_policy()
    claims = load_claims()
    flags = run_checks(claims, policy["params"])
    truth = pd.read_csv("ground_truth.csv")

    found = set(zip(flags["claim_id"], flags["violation_type"]))
    actual = set(zip(truth["claim_id"], truth["violation_type"]))
    tp = len(found & actual)
    print(f"Flags raised: {len(found)} | Planted: {len(actual)} | Correct: {tp}")
    print(f"Precision: {tp / max(len(found), 1):.2f} | Recall: {tp / max(len(actual), 1):.2f}")
    print("Missed:", sorted(actual - found))
    print("False positives:", sorted(found - actual))
