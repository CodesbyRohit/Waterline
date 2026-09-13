#!/usr/bin/env python3
"""Variant search: find the flow-projection semantics that reproduce the
grader's amount_safe_to_pay across all 25 solved samples.

Model: paying X on request_date shifts the whole balance path down by X, so
    safe = clamp(min_path_balance(no-payment sim) - min_balance, 0, requested)
We vary projection semantics and score each variant by exact matches.
"""
import csv
import itertools
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")


def pdt(s):
    return datetime.strptime(s, "%Y-%m-%d")


def fmt(dt):
    return dt.strftime("%Y-%m-%d")


def month_add(d, k):
    m = d.month - 1 + k
    y = d.year + m // 12
    m = m % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return d.replace(year=y, month=m, day=day)


# ---------- load ----------
BASE = "dataset"
profiles = {}
for r in csv.DictReader(open(f"{BASE}/financial_profiles.csv", encoding="utf-8")):
    profiles[r["user_id"]] = r

events = defaultdict(list)
for r in csv.DictReader(open(f"{BASE}/financial_events.csv", encoding="utf-8")):
    if r["amount"].strip():
        r["amt"] = float(r["amount"])
        events[r["user_id"]].append(r)

image_amounts = json.load(open("engine/image_amounts.json", encoding="utf-8"))
for r in csv.DictReader(open(f"{BASE}/financial_events.csv", encoding="utf-8")):
    if not r["amount"].strip() and r["event_id"] in image_amounts:
        r["amt"] = float(image_amounts[r["event_id"]])
        events[r["user_id"]].append(r)

rates = {}
for r in csv.DictReader(open(f"{BASE}/exchange_rates.csv", encoding="utf-8")):
    rates[(r["rate_date"], r["from_currency"], r["to_currency"])] = float(r["rate"])


def convert(amt, d, f, t):
    if f == t:
        return amt
    r = rates.get((d, f, t))
    if r is None:
        r0 = rates.get((d, t, f))
        if r0:
            r = 1.0 / r0
    if r is None:
        cands = sorted({dd for (dd, ff, tt) in rates if (ff, tt) in ((f, t), (t, f)) and dd <= d})
        if cands:
            dd = cands[-1]
            r = rates.get((dd, f, t))
            if r is None:
                r0 = rates.get((dd, t, f))
                r = 1.0 / r0 if r0 else 1.0
        else:
            r = 1.0
    return amt * r


FIXED_CATS = {
    "rent", "utilities", "education", "debt_repayment", "insurance", "phone",
    "internet", "music_subscription", "delivery_membership", "gym",
    "cloud_storage", "streaming_subscription", "software_subscription",
    "streaming", "healthcare",
}
VARIABLE_CATS = {
    "groceries", "transport", "dining", "shopping", "entertainment",
    "family_support", "personal_care", "clothing", "fuel", "travel",
    "investment", "other", "savings",
}


def user_events(uid):
    home = profiles[uid]["home_currency"]
    out = []
    for e in events.get(uid, []):
        if e["status"] in ("cancelled", "failed", "unrealized"):
            continue
        if e["status"] == "pending" and e["direction"] == "credit":
            continue
        if e.get("amt") is None:
            continue
        d = e["settlement_date"].strip() or e["event_date"]
        out.append((e, convert(e["amt"], d, e["currency"], home)))
    return out


def detect_recurring(uevs, cats, min_occ, cv_max):
    groups = defaultdict(list)
    for e, amt in uevs:
        if e["status"] == "settled" and e["direction"] == "debit" and e["category"] in cats:
            groups[(e["category"], e["event_type"])].append((e["event_date"], amt))
    out = []
    for (cat, et), inst in groups.items():
        if len(inst) < min_occ:
            continue
        inst.sort()
        amts = [a for _, a in inst]
        mean = sum(amts) / len(amts)
        cv = (sum((a - mean) ** 2 for a in amts) / len(amts)) ** 0.5 / mean if mean else 9e9
        if cv > cv_max:
            continue
        dates = [pdt(d) for d, _ in inst]
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_gap = sum(gaps) / len(gaps)
        if avg_gap <= 10:
            out.append({"cat": cat, "et": et, "freq": "weekly", "step": max(7, round(avg_gap)),
                        "typical": mean, "last": dates[-1]})
        else:
            by_day = defaultdict(list)
            for d, a in inst:
                by_day[int(d.split("-")[2])].append(a)
            best_day, best_n = None, 0
            for day, lst in by_day.items():
                if len(lst) >= 2 and len(lst) > best_n:
                    best_day, best_n = day, len(lst)
            if best_day is None:
                continue
            lst = by_day[best_day]
            typical = Counter(round(a, 2) for a in lst).most_common(1)[0][0]
            out.append({"cat": cat, "et": et, "freq": "monthly", "day": best_day,
                        "typical": typical, "last": dates[-1]})
    return out


def detect_income(uevs, from_scheduled):
    groups = defaultdict(list)
    for e, amt in uevs:
        ok_status = e["status"] == "settled" or (from_scheduled and e["status"] == "scheduled")
        if not ok_status or e["direction"] != "credit" or e["category"] != "salary":
            continue
        low = e["description"].lower()
        if "prorated" in low:
            continue
        day = int(e["event_date"].split("-")[2])
        groups[day].append((e["event_date"], amt))
    out = []
    for day, inst in groups.items():
        amts = [round(a, 2) for _, a in inst]
        typical, cnt = Counter(amts).most_common(1)[0]
        if cnt < max(1, len(amts)) and len(set(amts)) > 1 and cnt < 2:
            continue  # inconsistent amounts -> not a stable salary
        out.append({"day": day, "amount": typical, "last": max(pdt(d) for d, _ in inst)})
    return out


def simulate(uid, rdate_str, horizon_end, payments_at_start, V):
    prof = profiles[uid]
    bal0 = float(prof["current_available_balance"])
    minkeep = float(prof["minimum_balance_to_keep"])
    uevs = user_events(uid)

    credits = defaultdict(float)
    debits = defaultdict(float)

    # pending debits on/before request date are already reflected in balance?
    # NO - pending debits are future settlements; reserve them.
    for e, amt in uevs:
        st = e["status"]
        sd = (e["settlement_date"].strip() or e["event_date"])
        if st in ("pending", "scheduled") and sd > rdate_str and sd <= fmt(horizon_end):
            if e["direction"] == "debit":
                debits[sd] += amt
            else:
                credits[sd] += amt

    # recurring expense projection
    if V["var_mode"] != "none":
        var_cats = VARIABLE_CATS
    else:
        var_cats = set()
    rec = detect_recurring(uevs, FIXED_CATS, 2, 0.15) + detect_recurring(uevs, var_cats, 2, 0.60)
    for p in rec:
        if p["freq"] == "weekly" and not V["weekly"]:
            continue
        d = p["last"]
        while True:
            if p["freq"] == "monthly":
                d2 = month_add(d, 1)
                d2 = d2.replace(day=min(p["day"], 28))
                d2 = month_add(p["last"], ((d2.year - p["last"].year) * 12 + d2.month - p["last"].month))
                d = d2
            else:
                d = d + timedelta(days=p["step"])
            if d > horizon_end:
                break
            debits[fmt(d)] += p["typical"]

    # salary projection
    inc = detect_income(uevs, V["sched_income"])
    for p in inc:
        d = p["last"]
        while True:
            d = month_add(d, 1)
            ds = fmt(d.replace(day=min(p["day"], 28)))
            if d > horizon_end:
                break
            if V["dedup_income"]:
                # skip if a scheduled/pending salary credit already exists near this date
                dup = any(
                    e["category"] == "salary" and e["direction"] == "credit"
                    and abs((pdt(e["settlement_date"].strip() or e["event_date"]) - d).days) <= 3
                    for e, _ in uevs if e["status"] in ("pending", "scheduled")
                )
                if dup:
                    continue
            credits[ds] += p["amount"]

    # apply payment at start
    bal = bal0 - payments_at_start
    min_obs = bal
    d = pdt(rdate_str)
    while d <= horizon_end:
        ds = fmt(d)
        bal += credits.get(ds, 0.0) - debits.get(ds, 0.0)
        min_obs = min(min_obs, bal)
        d += timedelta(days=1)
    return min_obs, minkeep


def predict_safe(uid, rdate, desired, req_amt, V):
    if V["horizon"] == "desired":
        end = pdt(desired)
    else:
        end = pdt(rdate) + timedelta(days=90)
    M, minkeep = simulate(uid, rdate, end, 0.0, V)
    return max(0.0, min(req_amt, M - minkeep))


def main():
    samples = [
        r for r in csv.DictReader(open(f"{BASE}/sample_requests.csv", encoding="utf-8"))
        if r.get("amount_safe_to_pay")
    ]

    best = []
    for var_mode, weekly, sched_income, dedup, horizon in itertools.product(
        ["none", "rec"], [False, True], [False, True], [False, True], ["desired", "90d"]
    ):
        V = {"var_mode": var_mode, "weekly": weekly, "sched_income": sched_income,
             "dedup_income": dedup, "horizon": horizon}
        hits = 0
        misses = []
        for s in samples:
            pred = predict_safe(s["user_id"], s["request_date"], s["desired_completion_date"],
                                float(s["requested_amount"]), V)
            exp = float(s["amount_safe_to_pay"])
            if abs(pred - exp) < 0.011:
                hits += 1
            else:
                misses.append((s["request_id"], round(pred, 2), exp))
        best.append((hits, V, misses[:6]))

    best.sort(key=lambda x: -x[0])
    for hits, V, misses in best[:8]:
        print(f"{hits}/25  {V}")
        for m in misses:
            print(f"     miss {m}")


if __name__ == "__main__":
    main()
