#!/usr/bin/env python3
"""C0: Investigate simulator contradiction - binary search returns value it rejects"""
import sys, csv
from datetime import datetime, timedelta

sys.path.insert(0, '.')
from engine.main import load_all_data, Simulator, process_request, Request, get_effective_events, _parse_date, _fmt_date, calc_amount_safe

dataset_dir = 'dataset'
data = load_all_data(dataset_dir)
sim = Simulator(data)

print("=" * 70)
print("C0: SIMULATOR CONTRADICTION INVESTIGATION")
print("=" * 70)

# Check 1: Does binary search call same simulate() with same horizon?
print("\n1. BINARY SEARCH CALL SITE ANALYSIS")
print("-" * 70)

with open('engine/main.py', 'r') as f:
    code = f.read()
    lines = code.split('\n')

print("\ncalc_amount_safe() function (lines 669-694):")
in_func = False
for i, line in enumerate(lines[668:694], start=669):
    if 'def calc_amount_safe' in line:
        in_func = True
    if in_func:
        print(f"{i:4d}: {line}")
        if line.strip() == '' and i > 675:
            break

print("\nsimulate() call in calc_amount_safe:")
for i, line in enumerate(lines):
    if 'sim.simulate(user, events, [(request_date, mid)]' in line:
        print(f"  Line {i+1}: {line.strip()}")
    if 'def simulate(' in line and 'Simulator' not in line:
        print(f"\n  simulate() signature at line {i+1}:")
        for j in range(i, min(i+15, len(lines))):
            if lines[j].strip():
                print(f"    {j+1}: {lines[j].rstrip()}")

print("\n2. HORIZON WINDOW ANALYSIS")
print("-" * 70)

# Find horizon_days default
print("\nSearching for horizon_days usage:")
for i, line in enumerate(lines):
    if 'horizon_days' in line:
        print(f"  Line {i+1}: {line.strip()}")

print("\nIn simulate() - check the loop range:")
for i, line in enumerate(lines):
    if 'range(horizon_days)' in line or 'range(90)' in line or 'for day in range' in line:
        context_start = max(0, i-2)
        context_end = min(len(lines), i+3)
        for j in range(context_start, context_end):
            marker = ">>>" if j == i else "   "
            print(f"{marker} {j+1:4d}: {lines[j].rstrip()}")

print("\n3. BINARY SEARCH ITERATIONS FOR user_01")
print("-" * 70)

user_01 = data['profiles']['user_01']
events = get_effective_events('user_01', data['events'], data['event_map'])
request_date = '2024-03-03'
requested_amount = 25256.00

print(f"\nManual binary search trace for user_01:")
print(f"Balance: {user_01.current_balance:.2f}, Min: {user_01.min_balance:.2f}")
print(f"Request: {requested_amount:.2f}")
print()

# Replicate the binary search with debug output
is_safe_0, _, _ = sim.simulate(user_01, events, [], [], request_date)
print(f"Is 0 safe? {is_safe_0}")

is_safe_full, _, _ = sim.simulate(user_01, events, [(request_date, requested_amount)], [], request_date)
print(f"Is full amount ({requested_amount:.2f}) safe? {is_safe_full}")

low, high = 0.0, min(requested_amount, user_01.current_balance - user_01.min_balance + requested_amount)
high = max(high, 0.0)
print(f"Initial high bound: {high:.2f}")

best = 0.0
for iteration in range(60):
    mid = (low + high) / 2
    if mid <= 0:
        break
    is_safe, min_bal, breach = sim.simulate(user_01, events, [(request_date, mid)], [], request_date)
    
    if iteration < 10 or (iteration >= 55 and is_safe) or (iteration >= 55 and not is_safe and best > 0):
        print(f"Iter {iteration:2d}: mid={mid:10.2f}, safe={is_safe}, min_bal={min_bal:10.2f}, breach={breach}, best={best:10.2f}")
    
    if is_safe:
        best = mid
        low = mid
    else:
        high = mid
    
    if high - low < 0.01:
        print(f"  Converged at iteration {iteration}")
        break

print(f"\nFinal best (last SAFE tested): {best:.2f}")
print(f"Final high (first UNSAFE tested): {high:.2f}")
print(f"Difference: {high - best:.4f}")

# Now verify: does best pass simulate?
print("\n4. VERIFICATION: Does the returned value pass simulate()?")
print("-" * 70)
is_safe_best, min_bal_best, breach_best = sim.simulate(user_01, events, [(request_date, best)], [], request_date)
print(f"Testing best={best:.2f}: safe={is_safe_best}, min_bal={min_bal_best:.2f}, breach={breach_best}")

if not is_safe_best:
    print(f"\n*** CONTRADICTION FOUND ***")
    print(f"calc_amount_safe() returns {best:.2f}")
    print(f"But simulate() says this amount is UNSAFE (breach: {breach_best})")
    print(f"Minimum balance reached: {min_bal_best:.2f} < {user_01.min_balance:.2f}")
else:
    print(f"\nNo contradiction: best={best:.2f} passes simulate()")

# Check if the issue is day 90 boundary
print("\n5. DAY 90 BOUNDARY ANALYSIS")
print("-" * 70)
start_dt = _parse_date(request_date)
end_dt = start_dt + timedelta(days=90)
print(f"Request date: {request_date}")
print(f"Day 90 date: {_fmt_date(end_dt)} (this is the horizon endpoint)")
print()

# Check each day's balance with best amount
balance = user_01.current_balance
print("Daily balance with best amount (last 10 days of horizon):")
for day in range(80, 95):
    current_date = start_dt + timedelta(days=day)
    date_str = _fmt_date(current_date)
    year, month = current_date.year, current_date.month
    
    income = 0.0
    expenses = 0.0
    
    if 'user_01' not in sim.employment_ended:
        for inc_date, amt in sim._get_recurring_income_for_month('user_01', year, month):
            if inc_date.date() == current_date.date():
                income += amt
    
    for exp_date, amt, cat, etype in sim._get_recurring_expenses_for_month('user_01', year, month):
        if exp_date.date() == current_date.date():
            expenses += amt
    
    payment = best if day == 0 else 0.0
    
    balance = balance + income - expenses - payment
    
    if day >= 80:
        safe = "OK" if balance >= user_01.min_balance else "BREACH"
        print(f"  Day {day:2d} ({date_str}): balance={balance:10.2f}, min={user_01.min_balance:10.2f} -> {safe}")

print("\n6. CHECK: Does simulate() include day 90 in the loop?")
print("-" * 70)

# The key question: for day in range(horizon_days) iterates 0..89, not including day 90
print("simulate() uses: for day in range(horizon_days)")
print("This iterates day = 0, 1, 2, ..., 89 (90 iterations total)")
print("Day 90 (date 2024-06-01) is NOT simulated!")
print()
print("BUT the spec says '90-day forecast' - does this mean:")
print("  A) Days 0-89 (first 90 days, not including day 90)?")
print("  B) Days 0-90 (through day 90 inclusive, 91 days)?")
print()
print("The spec says: 'Forecast the user's balance for the next 90 days'")
print("This is ambiguous - could be either interpretation.")
print()
print("However, 2024-03-03 + 90 days = 2024-06-01")
print("If 'next 90 days' includes day 90, then 2024-06-01 should be checked.")
print()
print("The trace showed breach on 2024-06-01 (day 90) but the search ignores it!")
print("This is the contradiction.")
