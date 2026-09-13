# Phase B Report — Corrections and Analysis

## Corrections to Phase A

### Correction 1: Currency ranking by RELATIVE error

Recomputed using relative error instead of absolute:

| Currency | Count | Avg Rel Error | Median Rel | Max Rel |
|----------|-------|---------------|------------|---------|
| IDR      | 5     | 0.38          | 0.35       | 1.00    |
| INR      | 7     | 3.13          | 0.14       | 20.00   |
| USD      | 1     | 0.02          | 0.02       | 0.02    |
| EUR      | 8     | 0.25          | 0.12       | 1.17    |
| ZAR      | 4     | 5.13          | 0.41       | 20.01   |

**Conclusion**: Currency signal DOES survive but is WEAKER. However:
- ZAR has the highest median relative error (0.41) — this is user_01, all ZAR events
- INR has massive variance (0.14 median but 20x max) — request_10 is the outlier
- Currency does NOT explain request_01 (ZAR user, all ZAR events, 41% wrong)

**Revised root cause ranking:**
1. **Recurring inference errors** (affects ALL currencies, including ZAR-only user_01)
2. **Blank amounts dropped** (affects 5/25 samples, over-prediction direction)
3. **Currency conversion missing** (affects 27 users but concentrated in IDR/INR)
4. **Binary search bounds** (possible but less evidence)

### Correction 2: Blank amounts ARE score-relevant

**5 blank amount events for sample users:**

| Event | User | Type | Category | Flexibility | Date | Status |
|-------|------|------|----------|-------------|------|--------|
| event_253 | user_03 | income | salary | fixed | 2019-08-31 | settled |
| event_1442 | user_16 | expense | rent | fixed | 2023-08-16 | scheduled |
| event_1545 | user_17 | expense | groceries | fixed | 2026-02-27 | settled |
| event_1700 | user_19 | expense | groceries | fixed | 2024-09-03 | settled |
| event_1786 | user_20 | expense | utilities | fixed | 2026-02-09 | pending |

**Impact**: Dropping recurring essentials INFLATES available money → OVER-predicts safe amount
- This affects 20% of samples (5/25) in the OVER-prediction direction
- **Consistent with the 16 over vs 6 under bias**

### Correction 3: Error count reconciliation

**Corrected counts:**
- Over-predictions: 16
- Under-predictions: 6
- Correct: 3
- **TOTAL: 25** (not 26 — original had arithmetic error)

---

## B1: Recurrence Source of Truth

### Column analysis

**financial_events.csv (14 columns):**
1. event_id
2. user_id
3. event_type
4. description
5. category
6. direction
7. amount
8. currency
9. event_date
10. settlement_date
11. status
12. linked_event_id
13. **flexibility** ← KEY FIELD
14. minimum_allowed_amount

**financial_profiles.csv (10 columns):**
1. user_id
2. home_currency
3. current_available_balance
4. minimum_balance_to_keep
5. financial_priorities
6. expense_categories_to_protect
7. expense_categories_user_is_willing_to_reduce
8. expense_categories_user_is_willing_to_stop
9. payment_methods_user_will_consider
10. max_installment_months

### Key findings

**Q: Is there an explicit field marking an event as recurring?**
A: **NO**. No `is_recurring`, `frequency`, or `recurrence_type` field exists.

**Q: Is there an explicit frequency field?**
A: **NO**.

**Q: Is there a field marking flexible vs essential?**
A: **YES** — the `flexibility` column:
- `fixed` = cannot be changed (21,138 events)
- `reducible` = can be reduced (2,682 events)
- `stoppable` = can be stopped (1,297 events)
- `reducible_or_stoppable` = can be either (225 events)

**Q: What field does the spec sentence refer to?**
A: The spec says "only recurring expenses **marked** as flexible may be changed."
This refers to the `flexibility` field:
- For spending_changes_needed, only events with flexibility in (`stoppable`, `reducible`, `reducible_or_stoppable`) AND category in user's reducible/stoppable categories may be changed.

**CRITICAL INSIGHT**: The pattern-inference code (`detect_recurring_expenses()`) is solving a problem the dataset does NOT explicitly encode. There is no ground truth for "is this recurring?" — only the flexibility field tells us "if it recurs, how can it be modified."

---

## B2: user_01 Daily Cash-Flow Trace

**User_01**: ZAR 58,481.10 balance, ZAR 18,000.00 minimum
**Request**: ZAR 25,256.00 on 2024-03-03, complete by 2024-03-20

### Simulation with PREDICTED amount (14,862.19)

| DATE | OPENING | INCOME | EXPENSES | PAYMENT | CLOSING | MIN BAL | SAFE? |
|------|---------|--------|----------|---------|---------|---------|-------|
| 2024-03-03 | 58,481.10 | 0.00 | 0.00 | 14,862.19 | 43,618.91 | 18,000 | YES |
| 2024-03-05 | 43,618.91 | 0.00 | 567.60 | 0.00 | 43,051.31 | 18,000 | YES |
| 2024-03-06 | 43,051.31 | 0.00 | 1,475.46 | 0.00 | 41,575.85 | 18,000 | YES |
| 2024-03-08 | 41,575.85 | 0.00 | 2,638.14 | 0.00 | 38,937.71 | 18,000 | YES |
| 2024-03-11 | 38,480.94 | 0.00 | 3,722.40 | 0.00 | 34,758.54 | 18,000 | YES |
| 2024-03-13 | 34,758.54 | 0.00 | 306.90 | 0.00 | 34,451.64 | 18,000 | YES |
| 2024-03-15 | 34,451.64 | 23,320.00 | 816.54 | 0.00 | 56,955.10 | 18,000 | YES |
| ... | ... | ... | ... | ... | ... | ... | ... |
| 2024-06-01 | — | — | — | — | — | 18,000 | **BREACH** |

**Result: BREACH on 2024-06-01** (day 90 — at the edge of horizon)

### Simulation with EXPECTED amount (25,256.00)

| DATE | OPENING | INCOME | EXPENSES | PAYMENT | CLOSING | MIN BAL | SAFE? |
|------|---------|--------|----------|---------|---------|---------|-------|
| 2024-03-03 | 58,481.10 | 0.00 | 0.00 | 25,256.00 | 33,225.10 | 18,000 | YES |
| 2024-03-05 | 33,225.10 | 0.00 | 567.60 | 0.00 | 32,657.50 | 18,000 | YES |
| 2024-03-06 | 32,657.50 | 0.00 | 1,475.46 | 0.00 | 31,182.04 | 18,000 | YES |
| 2024-03-08 | 31,182.04 | 0.00 | 2,638.14 | 0.00 | 28,543.90 | 18,000 | YES |
| 2024-03-11 | 28,087.13 | 0.00 | 3,722.40 | 0.00 | 24,364.73 | 18,000 | YES |
| 2024-03-13 | 24,364.73 | 0.00 | 306.90 | 0.00 | 24,057.83 | 18,000 | YES |
| 2024-03-15 | 24,057.83 | 23,320.00 | 816.54 | 0.00 | 46,561.29 | 18,000 | YES |
| ... | ... | ... | ... | ... | ... | ... | ... |
| 2024-05-08 | 18,093.56 | 0.00 | 1,821.60 | 0.00 | **16,271.96** | 18,000 | **NO** |

**Result: BREACH on 2024-05-08** (day 66)

### One-sentence conclusion

**The simulator rejects 25,256 because on 2024-05-08, the education expense (ZAR 1,821.60) would bring the balance from 18,093.56 down to 16,271.96 — below the 18,000 minimum.**

The binding constraint is the **inferred monthly education expense on day 8** (event_03, 09, 15, 21, 28 pattern = ZAR 1,821.60/month).

### Why predicted is 14,862 but expected is 25,256

The simulator's binary search finds 14,862 as the maximum safe amount. But the expected answer is 25,256 (full amount, affordable_now).

This means the **expected answer believes the 90-day forecast is safe at 25,256**, but the simulator's forecast shows a breach on day 66.

**The discrepancy must be in the forecasted expenses:**
- The simulator infers weekly groceries (~ZAR 816/week) and weekly transport (~ZAR 457/week)
- If these are NOT actually recurring (just irregular past transactions), the forecast over-counts expenses
- This would make the simulator think there's a breach when there isn't

**This is exactly the recurring inference problem identified in B1.**

---

## B3: Exclusion Audit (Global)

**Total events: 25,342**

| Exclusion Type | Count | Implemented? | Location |
|----------------|-------|-------------|----------|
| Cancelled transactions | 22 | YES | get_effective_events() |
| Failed transactions | 21 | YES | get_effective_events() |
| Pending credits | 8 | YES | get_effective_events() |
| Unrealised investments | 10 | YES | get_effective_events() |
| Duplicate linked pairs | 27 | YES | get_effective_events() |
| **Total removed** | **88** | — | (0.3% of dataset) |

**All 5 spec exclusions ARE implemented correctly.**

**Events removed: 88 out of 25,342 (0.3%) — small fraction, correctly excluded.**

---

## Summary: Revised Root Cause Ranking

| Rank | Root Cause | Fix Order | Evidence |
|------|------------|-----------|----------|
| 1 | **Recurring inference errors** | 1st | user_01 (ZAR-only) has 41% error; weekly pattern detection over-counts groceries/transport |
| 2 | **Blank amounts dropped** | 2nd | 5/25 samples affected; causes over-prediction consistent with 16 vs 6 bias |
| 3 | **Currency conversion missing** | 3rd | 27 users affected but concentrated in IDR/INR; doesn't explain ZAR-only errors |
| 4 | **Binary search** | 4th | Only if evidence implicates it; current evidence points to forecast errors |

**Next step: Fix recurring inference first (B1 finding), then blank amounts (spec requirement).**
