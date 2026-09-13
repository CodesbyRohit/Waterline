#!/usr/bin/env python3
"""B2: Daily cash-flow trace for user_01"""
import sys, csv
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, '.')
from engine.main import load_all_data, Simulator, get_effective_events, _parse_date, _fmt_date

dataset_dir = 'dataset'
data = load_all_data(dataset_dir)
sim = Simulator(data)

user_01 = data['profiles']['user_01']
events = get_effective_events('user_01', data['events'], data['event_map'])

print("=" * 100)
print("B2: USER_01 DAILY CASH-FLOW TRACE")
print("=" * 100)
print(f"Balance: ZAR {user_01.current_balance:,.2f}, Min: ZAR {user_01.min_balance:,.2f}")
print(f"Request: ZAR 25,256.00 on 2024-03-03, complete by 2024-03-20")
print()

# Run two simulations
for label, payment_amount in [("PREDICTED (14,862.19)", 14862.19), ("EXPECTED (25,256.00)", 25256.00)]:
    print(f"{'='*100}")
    print(f"SIMULATION: {label}")
    print(f"{'='*100}")
    print(f"{'DATE':12s} {'OPENING':>12s} {'INCOME':>12s} {'EXPENSES':>12s} {'PAYMENT':>12s} {'CLOSING':>12s} {'MIN BAL':>10s} {'SAFE?':>8s}")
    print("-" * 100)
    
    balance = user_01.current_balance
    breach_date = None
    
    for day in range(95):
        current_date = _parse_date('2024-03-03') + timedelta(days=day)
        date_str = _fmt_date(current_date)
        year, month = current_date.year, current_date.month
        
        opening = balance
        income_total = 0.0
        expense_total = 0.0
        payment = 0.0
        
        # Recurring income
        if 'user_01' not in sim.employment_ended:
            for inc_date, amt in sim._get_recurring_income_for_month('user_01', year, month):
                if inc_date.date() == current_date.date():
                    income_total += amt
        
        # Recurring expenses
        for exp_date, amt, cat, etype in sim._get_recurring_expenses_for_month('user_01', year, month):
            if exp_date.date() == current_date.date():
                expense_total += amt
        
        # Explicit scheduled/pending events
        for e in events:
            if e.settlement_date == date_str:
                if e.status in ('pending', 'scheduled'):
                    if e.direction == 'credit':
                        income_total += e.amount
                    else:
                        expense_total += e.amount
        
        # Proposed payment
        if date_str == '2024-03-03':
            payment = payment_amount
        
        balance = opening + income_total - expense_total - payment
        
        safe = "YES" if balance >= user_01.min_balance else "NO ***"
        if balance < user_01.min_balance and breach_date is None:
            breach_date = date_str
        
        # Print first 15 days and around breach
        if day < 15 or (breach_date and day <= 20):
            print(f"{date_str:12s} {opening:>12.2f} {income_total:>12.2f} {expense_total:>12.2f} {payment:>12.2f} {balance:>12.2f} {user_01.min_balance:>10.2f} {safe:>8s}")
        
        if breach_date and day > 20:
            break
    
    print()
    if breach_date:
        print(f"RESULT: BREACH on {breach_date} - balance fell below {user_01.min_balance:,.2f}")
        # Show what happened
        print(f"  On {breach_date}, the closing balance was below minimum.")
        print(f"  This is caused by cumulative recurring expenses exceeding available funds.")
    else:
        print(f"RESULT: SAFE - no breach in 90-day window")
    print()

print("=" * 100)
print("ANALYSIS: WHY 25,256 IS REJECTED")
print("=" * 100)

# Find the exact breach day
balance = user_01.current_balance
for day in range(95):
    current_date = _parse_date('2024-03-03') + timedelta(days=day)
    date_str = _fmt_date(current_date)
    year, month = current_date.year, current_date.month
    
    income_total = 0.0
    expense_total = 0.0
    
    if 'user_01' not in sim.employment_ended:
        for inc_date, amt in sim._get_recurring_income_for_month('user_01', year, month):
            if inc_date.date() == current_date.date():
                income_total += amt
    
    for exp_date, amt, cat, etype in sim._get_recurring_expenses_for_month('user_01', year, month):
        if exp_date.date() == current_date.date():
            expense_total += amt
    
    for e in events:
        if e.settlement_date == date_str and e.status in ('pending', 'scheduled'):
            if e.direction == 'credit':
                income_total += e.amount
            else:
                expense_total += e.amount
    
    if date_str == '2024-03-03':
        balance = balance + income_total - expense_total - 25256.00
    else:
        balance = balance + income_total - expense_total
    
    if balance < user_01.min_balance:
        breach_day = day
        print(f"BREACH on {date_str}:")
        print(f"  Opening: {balance - income_total + expense_total + (25256.00 if date_str == '2024-03-03' else 0):,.2f}")
        print(f"  Income: +{income_total:,.2f}")
        print(f"  Expenses: -{expense_total:,.2f}")
        print(f"  Payment: -{25256.00 if date_str == '2024-03-03' else 0:,.2f}")
        print(f"  Closing: {balance:,.2f} (below min {user_01.min_balance:,.2f})")
        print()
        print(f"The simulator rejects 25,256 because on {date_str} the balance")
        print(f"would fall to {balance:,.2f}, which is below the {user_01.min_balance:,.2f} minimum.")
        break
