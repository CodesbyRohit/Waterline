#!/usr/bin/env python3
"""Phase A Diagnosis Part 2 - Focus on currency and blank amounts"""
import csv, os, sys
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.main import load_all_data, Simulator, get_effective_events, _parse_date, _fmt_date, detect_recurring_expenses, detect_recurring_income

dataset_dir = 'dataset'

data = load_all_data(dataset_dir)
sim = Simulator(data)

print("=" * 70)
print("CURRENCY CONVERSION DEEP DIVE")
print("=" * 70)

# Find all users with foreign currency events
print("\nUsers with foreign currency events (not matching home currency):")
users_with_foreign = []
for user_id, user in data['profiles'].items():
    user_events = [e for e in data['events'] if e.user_id == user_id]
    foreign_events = [e for e in user_events if e.currency != user.home_currency]
    if foreign_events:
        users_with_foreign.append((user_id, user, foreign_events))
        print(f"\n{user_id} ({user.home_currency}): {len(foreign_events)} foreign events")
        for e in foreign_events[:5]:
            print(f"  {e.event_id}: {e.amount} {e.currency} on {e.event_date} ({e.event_type}, {e.status})")
        if len(foreign_events) > 5:
            print(f"  ... and {len(foreign_events) - 5} more")

print(f"\nTotal users with foreign currency events: {len(users_with_foreign)}")

print("\n" + "=" * 70)
print("BLANK AMOUNT DEEP DIVE")
print("=" * 70)

blank_events = [e for e in data['events'] if e.amount is None]
print(f"\nTotal blank amount events: {len(blank_events)}")

# Check sample users specifically
sample_user_ids = set()
with open(os.path.join(dataset_dir, 'sample_requests.csv'), 'r') as f:
    for row in csv.DictReader(f):
        sample_user_ids.add(row['user_id'])

print(f"\nBlank events for sample users: {len([e for e in blank_events if e.user_id in sample_user_ids])}")
print("\nThese MUST be read from images per spec. Current code behavior:")
print("  get_effective_events() drops events with amount=None (line: 'e.amount is not None')")
print("  => Blank amount events are COMPLETELY EXCLUDED from the ledger")
print("\nThis is a spec violation. Images provide the amounts.")

# Show sample users affected
for e in blank_events:
    if e.user_id in sample_user_ids:
        print(f"\n  {e.event_id}: user_{e.user_id}, {e.event_type}, {e.category}")
        print(f"    Date: {e.event_date}, Status: {e.status}, Currency: {e.currency}")
        # Find image
        for img in data['images']:
            if img['related_event_id'] == e.event_id:
                print(f"    Image: {img['image_id']} -> would need OCR to extract amount")
                break

print("\n" + "=" * 70)
print("RECURRING DETECTION ANALYSIS")
print("=" * 70)

# Check which sample users have detected recurring expenses/income
print("\nRecurring expense detection for sample users:")
recurring_exp = detect_recurring_expenses(data['events'])
recurring_inc = detect_recurring_income(data['events'], data.get('messages_by_user', {}))

for exp_row in csv.DictReader(open(os.path.join(dataset_dir, 'sample_requests.csv'))):
    user_id = exp_row['user_id']
    if user_id in recurring_exp:
        print(f"\n{user_id} recurring expenses:")
        for cat, patterns in recurring_exp[user_id].items():
            for p in patterns:
                if p['type'] == 'monthly':
                    print(f"  {cat}: monthly on day {p['day']}, amount {p['amount']:.2f}")
                else:
                    print(f"  {cat}: weekly, amount {p['amount']:.2f}")
    else:
        print(f"\n{user_id}: NO recurring expenses detected")
    
    if user_id in recurring_inc and user_id != '_employment_ended':
        print(f"{user_id} recurring income:")
        for day, amt in recurring_inc[user_id].items():
            print(f"  day {day}: {amt:.2f}")

print("\n" + "=" * 70)
print("USER_01 DEEP ANALYSIS (request_01, ZAR)")
print("=" * 70)

user_01 = data['profiles']['user_01']
print(f"\nUser 01 profile:")
print(f"  Currency: {user_01.home_currency}")
print(f"  Balance: {user_01.current_balance:.2f}")
print(f"  Min balance: {user_01.min_balance:.2f}")
print(f"  Payment methods: {user_01.payment_methods}")
print(f"  Protected: {user_01.protected_categories}")
print(f"  Stoppable: {user_01.stoppable_categories}")

# All events for user_01
user_01_events = [e for e in data['events'] if e.user_id == 'user_01']
print(f"\nAll {len(user_01_events)} events for user_01:")
for e in sorted(user_01_events, key=lambda x: x.event_date):
    excluded = ""
    if e.status in ('cancelled', 'failed'):
        excluded = " [EXCLUDED: status]"
    elif e.status == 'unrealized':
        excluded = " [EXCLUDED: unrealized]"
    elif e.status == 'pending' and e.direction == 'credit':
        excluded = " [EXCLUDED: pending credit]"
    print(f"  {e.event_id}: {e.event_type:15s} {e.category:15s} {e.direction:6s} {str(e.amount):>10s} {e.currency:4s} {e.event_date} {e.status:10s}{excluded}")

print("\n" + "=" * 70)
print("ROOT CAUSE SUMMARY")
print("=" * 70)

print("""
ROOT CAUSE #1: CURRENCY CONVERSION NOT IMPLEMENTED (CRITICAL)
  - exchange_rates.csv is loaded but never used
  - When a user's home_currency is IDR but events are in USD/EUR/ZAR,
    those events should be converted using the rate for the settlement date
  - This affects users: user_02, user_25 (IDR users with USD/EUR events)
  - Impact: Massive over-prediction of safe amount for these users
  - Evidence: user_25 has 6 USD income events totaling $10,800
    that should be converted to IDR at ~15,833.33 = ~171M IDR income
    but are being counted as $10,800 (tiny vs 32M IDR balance)

ROOT CAUSE #2: BLANK AMOUNTS EXCLUDED INSTEAD OF READ FROM IMAGES (SPEC VIOLATION)
  - 16 events have blank amounts
  - 5 of these belong to sample users (requests 3, 16, 17, 19, 20)
  - Spec requires reading these from images via images.csv
  - Current code excludes them entirely (amount is None)
  - This is an independent spec compliance issue

ROOT CAUSE #3: INCORRECT RECURRING EXPENSE FORECASTING
  - For user_01 (ZAR, request_01): expected 25256, got 14862
  - Over-prediction of 10393 means expenses are under-counted
  - Need to verify recurring detection is capturing all regular expenses

ROOT CAUSE #4: INCORRECT INCOME FORECASTING
  - For user_25: predicted 0 when expected 1,425,000
  - This is UNDER-prediction, opposite direction from most errors
  - Suggests income is being double-counted or expenses over-counted
  - user_25 has USD income being treated as tiny amounts
""")
