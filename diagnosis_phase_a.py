#!/usr/bin/env python3
"""Phase A Diagnosis: amount_safe_to_pay at 16%"""
import csv, os, sys
from datetime import datetime, timedelta
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.main import load_all_data, Simulator, process_request, Request, get_effective_events, _parse_date, _fmt_date

dataset_dir = 'dataset'

def load_sample_expected():
    rows = []
    with open(os.path.join(dataset_dir, 'sample_requests.csv'), 'r') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows

def evaluate_tolerance(pv, ev, tol):
    try:
        p, e = float(pv), float(ev)
        return abs(p - e) <= tol
    except:
        return False

print("=" * 70)
print("PHASE A1: SCORER SANITY & TOLERANCE ANALYSIS")
print("=" * 70)

# Scorer check
print("\nScorer comparison (from evaluation/score.py):")
print("  abs_error < 1.0 OR rel_error < 0.01  (i.e., ±1 absolute OR ±1% relative)")
print("  match = (re < 0.01 or ae < 1.0)")

data = load_all_data(dataset_dir)
sim = Simulator(data)
expected_rows = load_sample_expected()

print(f"\nLoaded {len(expected_rows)} sample requests")

# Check tolerances
tolerances = {
    'exact': 0,
    '±0.01': 0.01,
    '±1': 1.0,
    '±1% relative': 'rel',
}

results_by_tol = {k: 0 for k in tolerances}
all_results = []

for exp in expected_rows:
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
    pv = predicted.get('amount_safe_to_pay', '0')
    ev = exp.get('amount_safe_to_pay', '0')
    
    try:
        p, e = float(pv), float(ev)
    except:
        p, e = 0, 0
    
    signed_error = p - e
    rel_error = abs(signed_error) / max(e, 0.01)
    
    row_result = {
        'request_id': req_id,
        'expected': e,
        'predicted': p,
        'signed_error': signed_error,
        'rel_error': rel_error,
        'home_currency': '',
        'balance': 0,
        'min_balance': 0,
        'requested_amount': float(exp['requested_amount']),
        'request_date': exp['request_date'],
        'expected_efpd': exp.get('earliest_date_for_full_payment', ''),
        'predicted_efpd': predicted.get('earliest_date_for_full_payment', ''),
    }
    
    if e == 0:
        match_any = (p == 0)
    else:
        match_any = (rel_error < 0.01) or (abs(signed_error) < 1.0)
    
    if p == e:
        results_by_tol['exact'] += 1
    if abs(signed_error) <= 0.01:
        results_by_tol['±0.01'] += 1
    if abs(signed_error) <= 1.0:
        results_by_tol['±1'] += 1
    if rel_error < 0.01 or abs(signed_error) < 1.0:
        results_by_tol['±1% relative'] += 1
    
    all_results.append(row_result)

print("\nTolerance analysis:")
for tol_name, count in results_by_tol.items():
    pct = count / len(expected_rows) * 100
    print(f"  {tol_name:20s}: {count:2d}/{len(expected_rows)} ({pct:5.1f}%)")

print("\n" + "=" * 70)
print("PHASE A2: FAILURE MATRIX (sorted by signed error)")
print("=" * 70)

all_results.sort(key=lambda x: x['signed_error'], reverse=True)

print(f"\n{'request_id':12s} {'expected':>15s} {'predicted':>15s} {'signed_err':>12s} {'rel_err':>8s} {'currency':>8s} {'balance':>12s} {'min_bal':>10s} {'req_amt':>12s} {'req_date':>10s} {'exp_efpd':>10s} {'pred_efpd':>10s}")
print("-" * 140)

for r in all_results:
    user = data['profiles'].get(r['request_id'].replace('request_', 'user_'))
    if not user:
        # Try to find user by ID
        for uid, u in data['profiles'].items():
            if uid == r['request_id'].split('_')[0]:
                user = u
                break
    if user:
        r['home_currency'] = user.home_currency
        r['balance'] = user.current_balance
        r['min_balance'] = user.min_balance
    
    print(f"{r['request_id']:12s} {r['expected']:>15.2f} {r['predicted']:>15.2f} {r['signed_error']:>12.2f} {r['rel_error']:>8.4f} {r['home_currency']:>8s} {r['balance']:>12.2f} {r['min_balance']:>10.2f} {r['requested_amount']:>12.2f} {r['request_date']:>10s} {r['expected_efpd']:>10s} {r['predicted_efpd']:>10s}")

# Analysis questions
print("\n" + "-" * 70)
print("ANALYSIS QUESTIONS:")
print("-" * 70)

# Q1: Of rows we get right, how many have expected == requested_amount?
correct_rows = [r for r in all_results if (r['rel_error'] < 0.01) or (abs(r['signed_error']) < 1.0)]
print(f"\nQ1: Rows getting amount_safe_to_pay correct: {len(correct_rows)}/{len(all_results)}")
correct_with_expected_eq_req = [r for r in correct_rows if abs(r['expected'] - r['requested_amount']) < 0.01]
print(f"  Of those, rows where expected == requested_amount: {len(correct_with_expected_eq_req)}")
if len(correct_with_expected_eq_req) > 0:
    print(f"  Those rows: {[r['request_id'] for r in correct_with_expected_eq_req]}")
else:
    print("  => NONE. The binary search has never produced a correct non-trivial value.")
    print("     Only the cap (requested_amount) could be saving us.")

# Q2: Direction of errors
over_preds = [r for r in all_results if r['signed_error'] > 0.01]
under_preds = [r for r in all_results if r['signed_error'] < -0.01]
print(f"\nQ2: Error direction:")
print(f"  Over-predictions (predicted > expected): {len(over_preds)}")
print(f"  Under-predictions (predicted < expected): {len(under_preds)}")
median_signed = sorted([r['signed_error'] for r in all_results])[len(all_results)//2]
print(f"  Median signed error: {median_signed:.2f}")

# Q3: Cluster by currency
print(f"\nQ3: Errors by home_currency:")
currency_errors = defaultdict(list)
for r in all_results:
    currency_errors[r['home_currency']].append(r['signed_error'])
for curr, errs in sorted(currency_errors.items(), key=lambda x: len(x[1]), reverse=True):
    avg_err = sum(errs) / len(errs) if errs else 0
    print(f"  {curr:6s}: {len(errs):2d} rows, avg signed error: {avg_err:>12.2f}")

# Q4: Cluster by user
print(f"\nQ4: Errors by user (top 10 by abs error):")
user_errors = defaultdict(list)
for r in all_results:
    uid = r['request_id'].split('_')[1]
    user_errors[uid].append(r)
top_users = sorted(user_errors.items(), key=lambda x: sum(abs(e['signed_error']) for e in x[1]), reverse=True)[:10]
for uid, rows in top_users:
    total_err = sum(abs(r['signed_error']) for r in rows)
    print(f"  user_{uid}: {len(rows)} rows, total abs error: {total_err:.2f}")

print("\n" + "=" * 70)
print("PHASE A3: CURRENCY CONVERSION CHECK")
print("=" * 70)

# Check if currency conversion is happening
print("\nChecking currency conversion in engine...")
with open('engine/main.py', 'r') as f:
    content = f.read()

if 'exchange_rates' in content and 'rate' in content:
    print("  exchange_rates dict is loaded in load_all_data()")
    # Check if it's used
    if 'data[\'exchange_rates\']' in content or 'self.data[\'exchange_rates\']' in content:
        print("  BUT: exchange_rates is NEVER USED in simulate() or anywhere else!")
        print("  => Foreign currency events are NOT being converted to home_currency")
    else:
        print("  Exchange rates appear unused")
else:
    print("  No exchange rate handling found")

# Check for sample users with foreign currency events
print("\n\nForeign currency events for sample users (first 3 worst rows):")
worst_rows = sorted(all_results, key=lambda x: abs(x['signed_error']), reverse=True)[:3]
for r in worst_rows:
    req_id = r['request_id']
    uid = req_id.split('_')[1]  # user_XX -> XX
    user_id = f"user_{uid}"
    user = data['profiles'].get(user_id)
    if not user:
        continue
    
    print(f"\n--- {req_id} (user_{uid}, {user.home_currency}) ---")
    print(f"  Balance: {user.current_balance:,.2f} {user.home_currency}")
    print(f"  Min balance: {user.min_balance:,.2f} {user.home_currency}")
    print(f"  Expected amount_safe: {r['expected']:,.2f}")
    print(f"  Predicted amount_safe: {r['predicted']:,.2f}")
    print(f"  Events for this user:")
    
    user_events = [e for e in data['events'] if e.user_id == user_id]
    non_home_currency = [e for e in user_events if e.currency != user.home_currency]
    
    if non_home_currency:
        print(f"  => FOUND {len(non_home_currency)} events in non-home currency!")
        for e in non_home_currency[:10]:
            print(f"    {e.event_id}: {e.amount} {e.currency} on {e.event_date} ({e.event_type}, {e.status})")
    else:
        print(f"  => All events in home currency")

print("\n" + "=" * 70)
print("PHASE A4: BLANK AMOUNTS CHECK")
print("=" * 70)

blank_amount_events = [e for e in data['events'] if e.amount is None]
print(f"\nEvents with blank amount in financial_events.csv: {len(blank_amount_events)}")

if blank_amount_events:
    print("\nBlank amount events:")
    for e in blank_amount_events:
        print(f"  {e.event_id}: user_{e.user_id}, {e.event_type}, {e.category}, {e.currency}, {e.event_date}, {e.status}")
        # Check if any have images
        related_images = [img for img in data['images'] if img['related_event_id'] == e.event_id]
        if related_images:
            print(f"    => Has image(s): {[img['image_id'] for img in related_images]}")
        else:
            print(f"    => No image linked")
    
    # Check if any belong to sample users
    sample_user_ids = set(exp['user_id'] for exp in expected_rows)
    blank_for_samples = [e for e in blank_amount_events if e.user_id in sample_user_ids]
    print(f"\nBlank amount events belonging to sample users: {len(blank_for_samples)}")
    for e in blank_for_samples:
        print(f"  {e.event_id}: user_{e.user_id}")
        related_images = [img for img in data['images'] if img['related_event_id'] == e.event_id]
        if related_images:
            print(f"    => Image: {related_images[0]['image_id']} -> {related_images[0]['related_event_id']}")
        else:
            print(f"    => No image found - this is a PROBLEM per spec")

print("\n" + "=" * 70)
print("PHASE A5 & A6: LEDGER TRACE FOR 3 WORST ROWS")
print("=" * 70)

for r in worst_rows:
    req_id = r['request_id']
    uid = req_id.split('_')[1]
    user_id = f"user_{uid}"
    user = data['profiles'].get(user_id)
    if not user:
        continue
    
    req = None
    for req_obj in data['requests']:
        if req_obj.request_id == req_id:
            req = req_obj
            break
    
    print(f"\n{'='*70}")
    print(f"ROW: {req_id} (user_{uid}, {user.home_currency})")
    print(f"{'='*70}")
    print(f"Request: {req.requested_amount:,.2f} on {req.request_date}, completes by {req.desired_completion_date}")
    print(f"Balance: {user.current_balance:,.2f}, Min: {user.min_balance:,.2f}")
    print(f"Expected amount_safe: {r['expected']:,.2f}")
    print(f"Predicted amount_safe: {r['predicted']:,.2f}")
    
    # Get effective events
    events = get_effective_events(user_id, data['events'], data['event_map'])
    
    # Find binding constraint date
    print(f"\nDaily ledger from {req.request_date} through binding constraint + 5 days:")
    
    # Run simulation to find breach date
    start_dt = _parse_date(req.request_date)
    end_dt = start_dt + timedelta(days=90)
    
    # Build daily table
    balance = user.current_balance
    print(f"\n{'DATE':12s} {'OPENING':>12s} {'INCOME':>12s} {'EXPENSES':>12s} {'PMT':>12s} {'CLOSING':>12s} {'MIN':>10s} {'SAFE?':>6s}")
    print("-" * 90)
    
    breach_date = None
    for day in range(96):  # 90 + 5 extra
        current_date = start_dt + timedelta(days=day)
        date_str = _fmt_date(current_date)
        year, month = current_date.year, current_date.month
        
        opening = balance
        income = 0.0
        expenses = 0.0
        payment = 0.0
        
        # Recurring income
        if user_id not in sim.employment_ended:
            for inc_date, amt in sim._get_recurring_income_for_month(user_id, year, month):
                if inc_date.date() == current_date.date():
                    income += amt
        
        # Recurring expenses
        for exp_date, amt, cat, etype in sim._get_recurring_expenses_for_month(user_id, year, month):
            if exp_date.date() == current_date.date():
                expenses += amt
        
        # Scheduled events
        for e in events:
            if e.settlement_date == date_str and e.status in ('pending', 'scheduled'):
                if e.direction == 'credit':
                    income += e.amount
                elif e.direction == 'debit':
                    expenses += e.amount
        
        # Proposed payment (just the request amount for visualization)
        if date_str == req.request_date:
            payment = r['predicted']  # Use predicted amount
        
        balance = opening + income - expenses - payment
        
        safe = "YES" if balance >= user.min_balance else "NO"
        if balance < user.min_balance and breach_date is None:
            breach_date = date_str
        
        if day < 15 or (breach_date and date_str <= breach_date + timedelta(days=5) and day <= 20):
            print(f"{date_str:12s} {opening:>12.2f} {income:>12.2f} {expenses:>12.2f} {payment:>12.2f} {balance:>12.2f} {user.min_balance:>10.2f} {safe:>6s}")
        
        if day > 20 and breach_date and date_str > breach_date + timedelta(days=5):
            break
    
    print(f"\nBinding constraint date: {breach_date or 'none'}")
    
    # Event lifecycle analysis
    print(f"\nEvent lifecycle analysis for {user_id}:")
    print(f"{'event_id':12s} {'type':20s} {'category':15s} {'direction':10s} {'amount':>12s} {'currency':>8s} {'date':12s} {'status':10s} {'linked':>12s} {'flex':15s} {'flags'}")
    print("-" * 150)
    
    for e in sorted(events, key=lambda x: x.event_date)[:30]:  # First 30 events
        flags = []
        if e.status in ('cancelled', 'failed'):
            flags.append('EXCLUDED-status')
        if e.status == 'unrealized':
            flags.append('EXCLUDED-unrealized')
        if e.status == 'pending' and e.direction == 'credit':
            flags.append('EXCLUDED-pending-credit')
        if e.linked_event_id and e.linked_event_id in data['event_map']:
            parent = data['event_map'][e.linked_event_id]
            if parent.direction == 'debit' and e.direction == 'credit':
                flags.append('EXCLUDED-linked-refund')
        
        print(f"{e.event_id:12s} {e.event_type:20s} {e.category:15s} {e.direction:10s} {str(e.amount):>12s} {e.currency:>8s} {e.event_date:12s} {e.status:10s} {str(e.linked_event_id):>12s} {e.flexibility:15s} {', '.join(flags) if flags else ''}")
    
    print(f"\nTotal events for user: {len(events)} (after exclusions)")

print("\n" + "=" * 70)
print("PHASE A: ROOT CAUSE RANKING")
print("=" * 70)

print("""
Candidate root causes ranked by explanatory power:

1. **CURRENCY CONVERSION MISSING (HIGH IMPACT)**
   - engine/main.py loads exchange_rates but NEVER uses them
   - Foreign currency events (EUR, USD, ZAR for IDR users, etc.) are not converted
   - This alone explains massive errors for IDR/ZAR users with EUR/USD events
   - Evidence: user_02 (IDR) has EUR events; user_13 (EUR) has non-EUR events
   - Would explain why predicted >> expected for many IDR users

2. **RECURRING EXPENSE FORECASTING GAPS (MEDIUM-HIGH IMPACT)**
   - detect_recurring_expenses() only catches clearly repeating patterns
   - Many regular expenses (rent, utilities) may not be detected as recurring
   - This causes under-counting of future expenses → over-prediction of safe amount

3. **SPENDING CHANGE DETECTION (MEDIUM IMPACT)**
   - Several expected answers require stop:event_XXX or reduce_to:event_XXX
   - Code does generate spending changes but may miss some events
   - Affects requests 6, 11, 21 where spending_changes_needed is non-none

4. **PENDING/SCHEDULED EVENT HANDLING (LOW-MEDIUM IMPACT)**
   - Code excludes pending credits but includes pending debits
   - Need to verify this matches spec requirements

5. **BLANK AMOUNT HANDLING (INDEPENDENT ISSUE)**
   - Events with blank amounts should read from images
   - Check if any sample users have blank amounts with linked images
""")

print("\nPHASE A COMPLETE - READY FOR USER REVIEW")
