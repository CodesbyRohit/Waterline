#!/usr/bin/env python3
"""Phase B1: Column analysis and recurrence source of truth"""
import csv

dataset_dir = 'dataset'

print("=" * 70)
print("B1: COLUMN ANALYSIS")
print("=" * 70)

print("\n--- financial_events.csv columns ---")
with open(f'{dataset_dir}/financial_events.csv', 'r') as f:
    reader = csv.DictReader(f)
    print(f"Total columns: {len(reader.fieldnames)}")
    for i, col in enumerate(reader.fieldnames, 1):
        print(f"  {i}. {col}")

print("\n--- financial_profiles.csv columns ---")
with open(f'{dataset_dir}/financial_profiles.csv', 'r') as f:
    reader = csv.DictReader(f)
    print(f"Total columns: {len(reader.fieldnames)}")
    for i, col in enumerate(reader.fieldnames, 1):
        print(f"  {i}. {col}")

print("\n" + "=" * 70)
print("B1: FIELD ANALYSIS")
print("=" * 70)

print("""
ANALYSIS QUESTIONS:

1. Is there an explicit field marking an event as recurring?
   Answer: NO. There is NO column like 'is_recurring', 'frequency', or 'recurrence_type'.
   
   The closest fields are:
   - 'flexibility': values seen include 'fixed', 'reducible', 'stoppable', 
     'reducible_or_stoppable'
   - This describes HOW an expense can be modified, NOT whether it recurs.

2. Is there an explicit frequency field?
   Answer: NO. No 'frequency_days', 'period', or 'recurrence_interval' column exists.

3. Is there a field marking flexible vs essential?
   Answer: PARTIALLY.
   
   - 'flexibility' column exists with values: fixed, reducible, stoppable, reducible_or_stoppable
   - 'category' column exists: rent, utilities, groceries, dining, transport, etc.
   - 'status' column: settled, pending, scheduled, cancelled, failed, unrealized
   - 'direction': debit, credit
   
   The spec says "only recurring expenses marked as flexible may be changed."
   This refers to the 'flexibility' field:
   - 'fixed' = cannot be changed (essential/protected)
   - 'reducible' = can be reduced (flexible, user reducible_categories)
   - 'stoppable' = can be stopped (flexible, user stoppable_categories)
   - 'reducible_or_stoppable' = can be either

4. What does 'flexibility' actually mark?
   It marks INDIVIDUAL EVENTS, not categories. A user might have:
   - Some dining expenses marked 'fixed' (essential meals)
   - Some dining expenses marked 'reducible' (optional dining)
   
   The flexibility is per-event, not per-category.
""")

print("\n" + "=" * 70)
print("B1: FLEXIBILITY VALUE DISTRIBUTION")
print("=" * 70)

flex_counts = {}
with open(f'{dataset_dir}/financial_events.csv', 'r') as f:
    for row in csv.DictReader(f):
        flex = row['flexibility']
        flex_counts[flex] = flex_counts.get(flex, 0) + 1

print("\nFlexibility values in dataset:")
for val, count in sorted(flex_counts.items(), key=lambda x: -x[1]):
    print(f"  {val:30s}: {count:6d} events")

print("\n" + "=" * 70)
print("B1: USER_01 OBLIGATIONS IN 90-DAY WINDOW")
print("=" * 70)

print("""
USER_01 (ZAR, request_01, request_date: 2024-03-03, horizon: 90 days to 2024-06-01)

Events in the 90-day forecast window (2024-03-03 to 2024-06-01):

RECURRING EXPENSES (inferred from history):
+-----------------------------------------------------------------------+
| Category      | Day | Amount   | Source Events                    | Count |
|---------------|-----|----------|---------------------------------|-------|
| rent          | 2   | 5,148.00 | event_01,07,13,19,26,32        | 6     |
| utilities     | 6   | ~1,500   | event_02,08,14,20,27           | 5     |
| education     | 8   | 1,821.60 | event_03,09,15,21,28           | 5     |
| debt_repay    | 11  | 3,487.00 | event_04,10,16,22,29           | 5     |
| music_sub     | 11  | 235.40   | event_05,11,17,23,30           | 5     |
| delivery_subs | 13  | 306.90   | event_06,12,18,24,31           | 5     |
| groceries     | var | ~800 avg | event_33-58 (many)             | 26    |
| transport     | var | ~450 avg | event_59-84 (many)             | 26    |
| dining        | var | ~1,050   | event_85-97 (many)             | 13    |
+-----------------------------------------------------------------------+

NOTABLE: ALL of these are INFERRED from transaction history.
NONE have an explicit 'is_recurring' or 'frequency' field.
The code's detect_recurring_expenses() is doing pattern matching.

ONE-TIME / NON-RECURRING in window:
- event_102: transport, pending debit, 567.60 on 2024-03-02 (explicit, pending)
- event_103: salary, scheduled credit, 23,320.00 on 2024-03-15 (explicit, scheduled)
- event_98/99: shopping debit/credit pair (linked, already excluded from double-count)

SPECIAL CASES:
- event_25: salary 12,826 on 2024-02-15 (PRORATED first salary - should this be recurring?)
  - Code skips 'prorated' in description
  - But then uses event_103 as the recurring salary pattern
""")

print("\n" + "=" * 70)
print("B1: USER_04 OBLIGATIONS IN 90-DAY WINDOW")
print("=" * 70)

print("""
USER_04 (IDR, request_04, request_date: 2024-06-04, horizon: 90 days to 2024-09-02)

Events in the 90-day forecast window (2024-06-04 to 2024-09-02):

RECURRING EXPENSES (inferred from history):
+-----------------------------------------------------------------------+
| Category      | Day | Amount      | Source Events                    | Count |
|---------------|-----|-------------|---------------------------------|-------|
| rent          | 1   | 12,293,000  | event_256,263,271,278,285,291  | 6     |
| utilities     | 5   | ~2,000,000  | event_257,264,272,286          | 4     |
| music_sub     | 10  | 332,500     | event_258,265,273,280          | 4     |
| delivery_subs | 12  | 377,150     | event_259,266,274,281          | 4     |
| gym           | 9   | 1,027,900   | event_260,267,275,282          | 4     |
| entertainment | 13  | ~1,400,000  | event_261,268,276,283,290      | 5     |
| groceries     | var | ~1,500,000  | event_292-317 (many)           | 26    |
| transport     | var | ~850,000    | event_318-328+ (many)          | 11+   |
+-----------------------------------------------------------------------+

RECURRING INCOME (inferred):
- Salary: 38,190,000 on day 15 (event_255,262,269,277,284 - monthly pattern)
- Bonus: 10,498,464.28 on day 22 (event_270 - only ONE occurrence!)

KEY OBSERVATION:
- event_270 is a "Quarterly performance bonus" - ONE time event on 2024-03-22
- But detect_recurring_income() sees it as day 22 pattern and projects it forward!
- This is a FALSE POSITIVE - a one-time bonus being treated as recurring income
- This would OVER-estimate income and UNDER-estimate the safe amount problem

Wait - that would make predicted < expected, not predicted > expected.
Let me reconsider...

Actually, user_04 has predicted 12,151,765 vs expected 8,401,800.
That's OVER-prediction by 3.7M IDR.

So the issue is the opposite: expenses are under-counted or income over-counted.

Looking at user_04's events more carefully:
- The events we see (256-328) are mostly from Jan-May 2024
- Request date is 2024-06-04
- We need to forecast June through August 2024

The recurring detection sees patterns from earlier months and projects them.
But some patterns may be wrong:
- Is the "weekly" groceries pattern correct, or are those irregular?
- Is the transport pattern really weekly or bi-weekly?

CRITICAL: The code infers weekly patterns when avg_gap <= 10 days.
But many "weekly" expenses are actually irregular food shopping.
""")

print("\n" + "=" * 70)
print("B1: KEY FINDINGS")
print("=" * 70)

print("""
FINDING 1: The dataset has NO explicit recurrence field.
   - Pattern detection from history is NECESSARY but error-prone
   - The 'flexibility' field tells us HOW to modify, not IF it recurs
   - This is the source of potential bidirectional errors

FINDING 2: The 'flexibility' field is the "marked as flexible" reference.
   - Spec: "only recurring expenses marked as flexible may be changed"
   - This means: for spending_changes_needed, only stop/reduce events where
     flexibility is 'stoppable', 'reducible', or 'reducible_or_stoppable'
   - AND the category must be in user's reducible_categories or stoppable_categories

FINDING 3: user_04 has a likely false positive in recurring income.
   - event_270 (quarterly bonus, 10.5M IDR) appears only once
   - If detected as recurring day-22 income, it would inflate forecasted income
   - This could explain part of the 3.7M over-prediction

FINDING 4: Weekly pattern detection may be too aggressive.
   - Groceries and transport show up as "weekly" but are likely irregular
   - The avg_gap <= 10 threshold may catch bi-weekly or irregular patterns
   - This would inflate expense forecasts, causing under-prediction
   - But we see OVER-prediction, so this isn't the main issue for user_04

CONCLUSION: The recurring inference code is the likely source of error for
users WITHOUT foreign currency issues. Pattern matching from history is
fundamentally ambiguous and can go either direction.
""")
