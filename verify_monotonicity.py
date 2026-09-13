#!/usr/bin/env python3
"""C0: Verify monotonicity before fixing boundary issue"""
import sys, csv
from datetime import datetime, timedelta

sys.path.insert(0, '.')
from engine.main import load_all_data, Simulator, process_request, Request, get_effective_events

dataset_dir = 'dataset'
data = load_all_data(dataset_dir)
sim = Simulator(data)

print("=" * 70)
print("C0: MONOTONICITY VERIFICATION ON FAILING ROWS")
print("=" * 70)

# Load sample expectations
sample_rows = []
with open(f'{dataset_dir}/sample_requests.csv', 'r') as f:
    for row in csv.DictReader(f):
        sample_rows.append(row)

# Get predictions and find failing rows
failing_rows = []
for exp in sample_rows:
    req_id = exp['request_id']
    req = None
    for r in data['requests']:
        if r.request_id == req_id:
            req = r
            break
    if req is None:
        req = Request(
            request_id=exp['request_id'], user_id=exp['user_id'],
            request_date=exp['request_date'], request_type=exp['request_type'],
            requested_amount=float(exp['requested_amount']),
            desired_completion_date=exp['desired_completion_date'],
            allows_partial=exp.get('allows_partial_payment', '').strip().lower() == 'true',
            request_text=exp.get('request_text', '')
        )
    
    predicted = process_request(req, data, sim)
    try:
        pv = float(predicted['amount_safe_to_pay'])
        ev = float(exp['amount_safe_to_pay'])
        se = pv - ev
        if abs(se) > 0.01:
            failing_rows.append({
                'request_id': req_id,
                'user_id': exp['user_id'],
                'requested_amount': float(exp['requested_amount']),
                'request_date': exp['request_date'],
                'expected': ev,
                'predicted': pv,
                'signed_error': se,
            })
    except:
        pass

print(f"\nFailing rows: {len(failing_rows)}")

# Test monotonicity on 5 worst rows
print("\n" + "=" * 70)
print("SWEEP TEST: X from 0 to requested_amount in 5% steps")
print("=" * 70)

for row in sorted(failing_rows, key=lambda x: abs(x['signed_error']), reverse=True)[:5]:
    req_id = row['request_id']
    user_id = row['user_id']
    req_amt = row['requested_amount']
    req_date = row['request_date']
    
    user = data['profiles'].get(user_id)
    events = get_effective_events(user_id, data['events'], data['event_map'])
    
    print(f"\n{req_id} (user_{user_id}, {user.home_currency if user else '?'})")
    print(f"  Requested: {req_amt:,.2f}, Expected: {row['expected']:,.2f}, Predicted: {row['predicted']:,.2f}")
    print(f"  {'X':>10s} {'SAFE?':>8s} {'MIN BAL':>12s} {'BREACH':>12s}")
    print("  " + "-" * 50)
    
    prev_safe = None
    non_monotonic = False
    
    for pct in range(0, 105, 5):
        x = req_amt * pct / 100
        if x > req_amt:
            x = req_amt
        
        is_safe, min_bal, breach = sim.simulate(user, events, [(req_date, x)], [], req_date)
        safe_str = "YES" if is_safe else "NO"
        
        # Check monotonicity
        if prev_safe is not None:
            if prev_safe and not is_safe:
                # This is OK - going from safe to unsafe as X increases
                pass
            elif not prev_safe and is_safe:
                # This is NOT OK - going from unsafe to safe as X increases
                print(f"  *** NON-MONOTONIC at X={x:,.2f}: was unsafe, now safe!")
                non_monotonic = True
        
        prev_safe = is_safe
        
        if pct % 10 == 0 or pct == 100 or abs(x - row['predicted']) < 0.01 or abs(x - row['expected']) < 0.01:
            marker = ""
            if abs(x - row['predicted']) < 0.01:
                marker = " <-- PREDICTED"
            if abs(x - row['expected']) < 0.01:
                marker = " <-- EXPECTED"
            print(f"  {x:>10,.2f} {safe_str:>8s} {min_bal:>12.2f} {breach or '':>12s}{marker}")
    
    if non_monotonic:
        print(f"\n  *** ERROR: Safety is NOT downward-closed for {req_id}!")
    else:
        print(f"\n  OK: Safety is downward-closed (as X increases, safe->unsafe only)")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)

all_monotonic = True
for row in failing_rows[:5]:
    # Quick re-check
    user = data['profiles'].get(row['user_id'])
    events = get_effective_events(row['user_id'], data['events'], data['event_map'])
    
    # Test around the predicted value
    pred = row['predicted']
    eps = 0.01
    
    safe_below = sim.simulate(user, events, [(row['request_date'], max(0, pred - eps))], [], row['request_date'])[0]
    safe_at = sim.simulate(user, events, [(row['request_date'], pred)], [], row['request_date'])[0]
    safe_above = sim.simulate(user, events, [(row['request_date'], pred + eps)], [], row['request_date'])[0] if pred < row['requested_amount'] else True
    
    if not safe_below and safe_at:
        print(f"  {row['request_id']}: NON-MONOTONIC - {pred-eps:.2f} unsafe but {pred:.2f} safe")
        all_monotonic = False
    elif safe_at and not safe_above and pred < row['requested_amount']:
        print(f"  {row['request_id']}: OK - {pred:.2f} safe, {pred+eps:.2f} unsafe (correct boundary)")
    elif not safe_at:
        print(f"  {row['request_id']}: {pred:.2f} is NOT safe (contradiction with returned value)")
        all_monotonic = False

if all_monotonic:
    print("\nAll tested rows: safety IS downward-closed. Binary search is valid.")
    print("Boundary issue is about precision/tightness, not monotonicity.")
else:
    print("\n!!! MONOTONICITY VIOLATED - do not proceed with fix !!!")
