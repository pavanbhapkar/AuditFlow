"""AuditFlow - synthetic expense claim generator.

Creates:
  expense_claims.csv  -> what the agents will audit
  ground_truth.csv    -> which claims have planted violations (used only for validation)
"""
import json
import random
from datetime import date, timedelta

import pandas as pd

random.seed(42)

with open("policy.json") as f:
    P = json.load(f)["params"]
LIMITS = P["category_limits"]
WEEKEND_OK = P["weekend_allowed_categories"]

NAMES = ["Aarav Mehta", "Diya Nair", "Rohan Joshi", "Sneha Kulkarni", "Vikram Rao",
         "Ishita Shah", "Kabir Singh", "Meera Iyer", "Arjun Patil", "Tanvi Desai",
         "Nikhil Verma", "Priya Menon", "Siddharth Jain", "Anjali Gupta", "Rahul Deshmukh",
         "Neha Kapoor", "Aditya Pillai", "Kavya Reddy", "Yash Malhotra", "Riya Sharma"]
DEPTS = ["Sales", "Engineering", "Finance", "HR", "Operations"]
EMPLOYEES = [(f"E{i:03d}", n, DEPTS[i % len(DEPTS)]) for i, n in enumerate(NAMES, 1)]

VENDORS = {
    "Travel": ["IndiGo", "Ola Corporate", "IRCTC", "Uber Business"],
    "Meals": ["Cafe Coffee Day", "Barbeque Nation", "Zomato Business", "Social"],
    "Accommodation": ["Taj Hotels", "OYO Business", "Lemon Tree", "Ibis"],
    "Office Supplies": ["Amazon Business", "Staples", "Reliance Digital"],
    "Client Entertainment": ["The Westin", "Hard Rock Cafe", "Marriott Events"],
    "Software": ["Atlassian", "Adobe", "Microsoft 365", "JetBrains"],
}

Q_START, Q_END = date(2026, 7, 1), date(2026, 9, 30)

_inv = [100000]


def new_invoice():
    _inv[0] += random.randint(1, 9)
    return f"INV{_inv[0]}"


def rand_date(max_dow=4):
    """Random date in Q3. max_dow=4 -> weekdays only, max_dow=3 -> Mon-Thu (so +1 day is still a weekday)."""
    while True:
        d = Q_START + timedelta(days=random.randint(0, (Q_END - Q_START).days))
        if d.weekday() <= max_dow:
            return d


def rand_weekend():
    while True:
        d = Q_START + timedelta(days=random.randint(0, (Q_END - Q_START).days))
        if d.weekday() >= 5:
            return d


def make_claim(category=None, amount=None, d=None, emp=None, vendor=None,
               submitted=None, receipt="Yes", viol=None, approval=""):
    category = category or random.choice(list(LIMITS))
    emp = emp or random.choice(EMPLOYEES)
    vendor = vendor or random.choice(VENDORS[category])
    amount = amount if amount is not None else round(LIMITS[category] * random.uniform(0.15, 0.45), 2)
    d = d or rand_date()
    submitted = submitted or d + timedelta(days=random.randint(1, 20))
    return {
        "employee_id": emp[0], "employee_name": emp[1], "department": emp[2],
        "category": category, "vendor": vendor, "amount": round(amount, 2),
        "expense_date": d, "submitted_date": submitted, "receipt_attached": receipt, "invoice_no": new_invoice(), "approval_note": approval,
        "description": f"{category} expense at {vendor}", "_viol": viol,
    }


rows = []

# 1) Normal, policy-compliant claims
normal = [make_claim() for _ in range(260)]
rows += normal

# 2) Planted violations
picked = random.sample(normal, 14)
for orig in picked[:8]:  # DUPLICATE: true copy (same invoice number)
    dup = dict(orig)
    dup["submitted_date"] = orig["submitted_date"] + timedelta(days=random.randint(1, 5))
    dup["_viol"] = "DUPLICATE"
    rows.append(dup)

for orig in picked[8:]:  # LOOK-ALIKE: same vendor/amount/date but a different invoice -> legitimate
    twin = dict(orig)
    twin["invoice_no"] = new_invoice()
    twin["submitted_date"] = orig["submitted_date"] + timedelta(days=random.randint(0, 3))
    twin["_viol"] = None
    rows.append(twin)

for _ in range(8):  # OVER_LIMIT
    cat = random.choice(list(LIMITS))
    rows.append(make_claim(cat, amount=LIMITS[cat] * random.uniform(1.1, 1.8), viol="OVER_LIMIT"))

for _ in range(6):  # SPLIT_BILLING: two claims, each 60% of limit, total 120%
    cat = random.choice(list(LIMITS))
    emp, vendor, d = random.choice(EMPLOYEES), random.choice(VENDORS[cat]), rand_date(max_dow=3)
    for day_offset in (0, 1):
        rows.append(make_claim(cat, amount=LIMITS[cat] * 0.6, d=d + timedelta(days=day_offset),
                               emp=emp, vendor=vendor, viol="SPLIT_BILLING"))

for _ in range(8):  # WEEKEND (category not allowed on weekends)
    cat = random.choice([c for c in LIMITS if c not in WEEKEND_OK])
    rows.append(make_claim(cat, d=rand_weekend(), viol="WEEKEND"))

for _ in range(8):  # NO_RECEIPT (amount above threshold)
    cat = random.choice(list(LIMITS))
    amt = max(LIMITS[cat] * random.uniform(0.2, 0.45), 600)
    rows.append(make_claim(cat, amount=amt, receipt="No", viol="NO_RECEIPT"))

for _ in range(8):  # LATE_SUBMISSION
    d = rand_date()
    rows.append(make_claim(d=d, submitted=d + timedelta(days=random.randint(35, 70)), viol="LATE_SUBMISSION"))

# Legitimate exceptions (valid approval reference) - NOT violations, but naive rules will flag them
def approved_note():
    return f"Pre-approved exception APR-{random.randint(1000, 9999)}"


for _ in range(3):
    cat = random.choice(list(LIMITS))
    rows.append(make_claim(cat, amount=LIMITS[cat] * random.uniform(1.1, 1.5), approval=approved_note()))
for _ in range(3):
    cat = random.choice([c for c in LIMITS if c not in WEEKEND_OK])
    rows.append(make_claim(cat, d=rand_weekend(), approval=approved_note()))
for _ in range(3):
    d = rand_date()
    rows.append(make_claim(d=d, submitted=d + timedelta(days=random.randint(35, 70)), approval=approved_note()))

# Fake approvals (no valid APR reference) - STILL violations
FAKE = "Verbally approved by manager"
cat = random.choice(list(LIMITS))
rows.append(make_claim(cat, amount=LIMITS[cat] * 1.4, approval=FAKE, viol="OVER_LIMIT"))
cat = random.choice([c for c in LIMITS if c not in WEEKEND_OK])
rows.append(make_claim(cat, d=rand_weekend(), approval=FAKE, viol="WEEKEND"))
d = rand_date()
rows.append(make_claim(d=d, submitted=d + timedelta(days=50), approval=FAKE, viol="LATE_SUBMISSION"))

# 3) Shuffle, assign IDs, split off ground truth
random.shuffle(rows)
for i, r in enumerate(rows, 1):
    r["claim_id"] = f"CLM{i:04d}"

df = pd.DataFrame(rows)
truth = df[df["_viol"].notna()][["claim_id", "_viol"]].rename(columns={"_viol": "violation_type"})
df = df.drop(columns=["_viol"])
df = df[["claim_id", "employee_id", "employee_name", "department", "category", "vendor",
         "amount", "expense_date", "submitted_date", "receipt_attached", "invoice_no",
         "approval_note", "description"]]

df.to_csv("expense_claims.csv", index=False)
truth.to_csv("ground_truth.csv", index=False)

print(f"Claims generated: {len(df)}")
print(f"Planted violations: {len(truth)}")
print(truth["violation_type"].value_counts().to_string())
