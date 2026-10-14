"""
Explanation of Benefits (EOB) Generator
---------------------------------------
Reads a claim (JSON), applies the member's plan rules, and produces a
printable EOB showing what was billed, what the plan allowed, what the
plan paid, and what the member owes.

Benefit order applied to each service line:
    1. Provider discount  : billed -> allowed amount (contract rate)
    2. Not-covered check  : non-covered services are the member's full cost
    3. Copay              : flat amount (for services that have one)
    4. Deductible         : member pays until the annual deductible is met
    5. Coinsurance        : member pays a % of what remains, plan pays the rest
    6. Out-of-pocket max  : member cost-sharing is capped; plan pays the excess

Python 3.8+, standard library only.

Usage:
    python eob_generator.py sample                  # create sample_claim.json
    python eob_generator.py generate sample_claim.json
"""

import json
import os
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

OUTPUT_DIR = "eobs"


# ---------------------------------------------------------------------------
# Money helpers (Decimal avoids floating-point rounding errors)
# ---------------------------------------------------------------------------
def D(value):
    return Decimal(str(value))


def money(value):
    return D(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt(value):
    return f"${money(value):,.2f}"


# ---------------------------------------------------------------------------
# Benefit calculation
# ---------------------------------------------------------------------------
def calculate_line(line, plan, acc):
    """Calculate one service line. `acc` holds running accumulators and is updated."""
    billed = money(line["billed"])
    allowed = min(billed, money(line["allowed"]))
    result = {
        "service_date": line["service_date"],
        "code": line["code"],
        "description": line["description"],
        "billed": billed,
        "allowed": Decimal("0.00"),
        "provider_discount": Decimal("0.00"),
        "not_covered": Decimal("0.00"),
        "copay": Decimal("0.00"),
        "deductible": Decimal("0.00"),
        "coinsurance": Decimal("0.00"),
        "plan_paid": Decimal("0.00"),
        "member_owes": Decimal("0.00"),
        "remark": "",
    }

    # Non-covered service: member owes the allowed amount, nothing counts toward limits
    if not line.get("covered", True):
        result["provider_discount"] = billed - allowed
        result["not_covered"] = allowed
        result["member_owes"] = allowed
        result["remark"] = line.get("remark", "Service is not covered by your plan.")
        return result

    result["allowed"] = allowed
    result["provider_discount"] = billed - allowed

    # Copay (not subject to deductible)
    copay = min(money(line.get("copay", 0)), allowed)
    remaining = allowed - copay

    # Deductible
    ded_left = money(plan["deductible"]) - acc["deductible_met"]
    deductible = min(remaining, max(ded_left, Decimal("0.00")))
    remaining -= deductible

    # Coinsurance
    coinsurance = money(remaining * D(plan["coinsurance_pct"]) / Decimal(100))

    # Out-of-pocket maximum cap on member cost-sharing
    cost_share = copay + deductible + coinsurance
    oop_left = max(money(plan["oop_max"]) - acc["oop_met"], Decimal("0.00"))
    if cost_share > oop_left:
        excess = cost_share - oop_left
        coinsurance = max(coinsurance - excess, Decimal("0.00"))
        cost_share = copay + deductible + coinsurance
        if cost_share > oop_left:  # excess still left, trim deductible next
            deductible -= cost_share - oop_left
            cost_share = copay + deductible + coinsurance
        result["remark"] = "Out-of-pocket maximum reached."

    # Update accumulators
    acc["deductible_met"] += deductible
    acc["oop_met"] += cost_share

    result.update(
        copay=copay,
        deductible=deductible,
        coinsurance=coinsurance,
        member_owes=cost_share,
        plan_paid=allowed - cost_share,
    )
    return result


def process_claim(data):
    plan = data["plan"]
    acc = {
        "deductible_met": money(plan["deductible_met"]),
        "oop_met": money(plan["oop_met"]),
    }
    start = dict(acc)
    lines = [calculate_line(l, plan, acc) for l in data["lines"]]

    totals = {
        k: sum((l[k] for l in lines), Decimal("0.00"))
        for k in ("billed", "provider_discount", "allowed", "not_covered",
                  "copay", "deductible", "coinsurance", "plan_paid", "member_owes")
    }
    return lines, totals, start, acc


# ---------------------------------------------------------------------------
# EOB formatting
# ---------------------------------------------------------------------------
def build_eob(data, lines, totals, start, end):
    m, p, c, plan = data["member"], data["provider"], data["claim"], data["plan"]
    W = 78
    out = []
    add = out.append

    add("=" * W)
    add(f"{plan['payer_name']:^{W}}")
    add(f"{'EXPLANATION OF BENEFITS':^{W}}")
    add(f"{'This is not a bill.':^{W}}")
    add("=" * W)
    add(f"Date Issued   : {date.today():%Y-%m-%d}")
    add(f"Claim Number  : {c['claim_id']}        Received: {c['received_date']}")
    add("")
    add(f"Member        : {m['name']}   (ID: {m['member_id']})")
    add(f"Plan          : {plan['plan_name']}")
    add(f"Provider      : {p['name']}   (ID: {p['provider_id']}, {p['network']})")
    add("-" * W)

    for i, l in enumerate(lines, 1):
        add(f"Line {i}: {l['service_date']}  {l['code']}  {l['description']}")
        add(f"    Billed {fmt(l['billed']):>10}   Discount {fmt(l['provider_discount']):>10}"
            f"   Allowed {fmt(l['allowed']):>10}")
        add(f"    Copay {fmt(l['copay']):>10}   Deductible {fmt(l['deductible']):>10}"
            f"   Coinsurance {fmt(l['coinsurance']):>10}")
        if l["not_covered"]:
            add(f"    Not covered {fmt(l['not_covered']):>10}")
        add(f"    Plan paid {fmt(l['plan_paid']):>10}   You owe {fmt(l['member_owes']):>10}")
        if l["remark"]:
            add(f"    Note: {l['remark']}")
        add("")

    add("-" * W)
    add("CLAIM SUMMARY")
    rows = [
        ("Total billed by provider", totals["billed"]),
        ("Provider discount (network rate)", totals["provider_discount"]),
        ("Allowed amount", totals["allowed"]),
        ("Amount plan paid", totals["plan_paid"]),
        ("Not covered", totals["not_covered"]),
        ("Copay", totals["copay"]),
        ("Applied to deductible", totals["deductible"]),
        ("Coinsurance", totals["coinsurance"]),
    ]
    for label, val in rows:
        add(f"  {label:<40}{fmt(val):>14}")
    add(f"  {'-' * 54}")
    add(f"  {'YOU OWE THE PROVIDER':<40}{fmt(totals['member_owes']):>14}")
    add("")
    add("YEAR-TO-DATE ACCUMULATORS (after this claim)")
    ded = money(plan["deductible"])
    oop = money(plan["oop_max"])
    add(f"  Deductible : {fmt(end['deductible_met'])} of {fmt(ded)} met"
        f"   (was {fmt(start['deductible_met'])})")
    add(f"  Out-of-pocket max : {fmt(end['oop_met'])} of {fmt(oop)} met"
        f"   (was {fmt(start['oop_met'])})")
    add("=" * W)
    add("Questions? Call Member Services. You may appeal within 180 days.")
    add("=" * W)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Sample data + CLI
# ---------------------------------------------------------------------------
SAMPLE = {
    "plan": {
        "payer_name": "Sunrise Health Plan",
        "plan_name": "Silver PPO 3000",
        "deductible": 1000,
        "deductible_met": 800,
        "coinsurance_pct": 20,
        "oop_max": 5000,
        "oop_met": 1200,
    },
    "member": {"member_id": "M1001", "name": "Anita Rao"},
    "provider": {"provider_id": "P2001", "name": "City Care Clinic", "network": "In-Network"},
    "claim": {"claim_id": "CLM-0001", "received_date": "2026-09-20"},
    "lines": [
        {"service_date": "2026-09-10", "code": "99213", "description": "Office visit",
         "billed": 200, "allowed": 120, "copay": 30},
        {"service_date": "2026-09-10", "code": "85025", "description": "Complete blood count",
         "billed": 90, "allowed": 40},
        {"service_date": "2026-09-10", "code": "71046", "description": "Chest X-ray",
         "billed": 300, "allowed": 180},
        {"service_date": "2026-09-10", "code": "S9999", "description": "Cosmetic consultation",
         "billed": 150, "allowed": 150, "covered": False,
         "remark": "Cosmetic services are excluded from your plan."},
    ],
}


def load_claim(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"Error: file '{path}' not found.")
    except json.JSONDecodeError as e:
        sys.exit(f"Error: invalid JSON in '{path}': {e}")


def generate(path):
    data = load_claim(path)
    try:
        lines, totals, start, end = process_claim(data)
        text = build_eob(data, lines, totals, start, end)
    except KeyError as e:
        sys.exit(f"Error: missing field {e} in claim file.")

    print(text)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"EOB_{data['claim']['claim_id']}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\nSaved to {out_path}")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "sample":
        with open("sample_claim.json", "w", encoding="utf-8") as f:
            json.dump(SAMPLE, f, indent=2)
        print("Created sample_claim.json. Now run: python eob_generator.py generate sample_claim.json")
    elif len(sys.argv) == 3 and sys.argv[1] == "generate":
        generate(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
