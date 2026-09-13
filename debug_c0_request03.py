#!/usr/bin/env python3
"""Debug request_03 binary search to find why it returns unsafe value"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, '.')
from engine.main import load_all_data, Simulator, get_effective_events, _parse_date, _fmt_date, calc_amount_safe

data = load_all_data('dataset')
sim = Simulator(data)

user_03 = data['profiles']['user_03']
events = get_effective_events('user_03', data['events'], data['event_map'])

print("=" * 70)
print("DEBUG: request_03 binary search")
print("=" * 70)

print(f"\nUser_03: IDR {user_03.current_balance:,.2f} balance, min {user_03.min_balance:,.2f}")
print(f"Request: IDR 5,491,000.00 on 2019-09-03")
print()

# Manual binary search with full debug
req_date = '2019-09-03'
req_amt = 5491000.00

print("Step 1: Check bounds")
is_safe_0, _, _ = sim.simulate(user_03, events, [], [], req_date)
print(f"  Is 0 safe? {is_safe_0}")

is_safe_full, _, _ = sim.simulate(user_03, events, [(req_date, req_amt)], [], req_date)
print(f"  Is full amount safe? {is_safe_full}")

low, high = 0.0, min(req_amt, user_03.current_balance - user_03.min_balance + req_amt)
high = max(high, 0.0)
print(f"  Initial bounds: low={low:.2f}, high={high:.2f}")

print("\nStep 2: Binary search iterations (showing all):")
best = 0.0
converged = False

for iteration in range(60):
    mid = (low + high) / 2
    if mid <= 0:
        print(f"  Iter {iteration}: mid={mid:.2f} - breaking (mid<=0)")
        break
    
    is_safe, min_bal, breach = sim.simulate(user_03, events, [(req_date, mid)], [], req_date)
    
    # Print every iteration
    print(f"  Iter {iteration:2d}: mid={mid:12.2f}, safe={is_safe}, min_bal={min_bal:12.2f}, breach={breach}, best={best:12.2f}, low={low:12.2f}, high={high:12.2f}")
    
    if is_safe:
        best = mid
        low = mid
    else:
        high = mid
    
    if high - low < 0.01:
        print(f"  -> Converged at iter {iteration} (high-low={high-low:.4f})")
        converged = True
        break

print(f"\nStep 3: Results")
print(f"  Final best (last safe tested): {best:.2f}")
print(f"  Final high (first unsafe): {high:.2f}")
print(f"  Converged: {converged}")

print("\nStep 4: Verify best value")
is_safe_best, min_bal_best, breach_best = sim.simulate(user_03, events, [(req_date, best)], [], req_date)
print(f"  Testing best={best:.2f}:")
print(f"    safe={is_safe_best}, min_bal={min_bal_best:.2f}, breach={breach_best}")

if not is_safe_best:
    print(f"\n  *** BUG CONFIRMED: best={best:.2f} is UNSAFE ***")
    print(f"  The binary search returned a value it itself rejects!")
    
    # Check if this is a precision issue
    print(f"\n  Checking nearby values:")
    for delta in [-0.02, -0.01, -0.005, 0, 0.005, 0.01, 0.02]:
        test_val = best + delta
        is_safe, min_bal, breach = sim.simulate(user_03, events, [(req_date, test_val)], [], req_date)
        print(f"    {test_val:12.2f}: safe={is_safe}, min_bal={min_bal:12.2f}, breach={breach}")

print("\nStep 5: Check what calc_amount_safe() actually returns")
actual_result = calc_amount_safe(user_03, events, sim, req_amt, req_date)
print(f"  calc_amount_safe() returned: {actual_result:.2f}")

is_safe_actual, min_bal_actual, breach_actual = sim.simulate(user_03, events, [(req_date, actual_result)], [], req_date)
print(f"  Testing that value: safe={is_safe_actual}, min_bal={min_bal_actual:.2f}, breach={breach_actual}")

if not is_safe_actual:
    print(f"\n  *** THE BUG IS IN calc_amount_safe() ***")
    print(f"  It returns {actual_result:.2f} which simulate() says is UNSAFE")
