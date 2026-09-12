# Token Usage and Cost Analysis

## Summary

This solution uses a **fully deterministic** financial decision engine. No LLM calls are made during the pipeline execution. All reasoning, simulation, and plan generation are performed by deterministic Python code.

## Model Usage

| Model | Provider | Calls | Input Tokens | Output Tokens | Cost |
|-------|----------|-------|-------------|--------------|------|
| None | N/A | 0 | 0 | 0 | $0.00 |

**Total calls:** 0  
**Total input tokens:** 0  
**Total output tokens:** 0  
**Total estimated cost:** $0.00  
**Average tokens per request:** 0  

## Architecture Note

The system separates interpretation from decision-making:

1. **Data Inestion Layer** (deterministic): Loads and parses CSV files, extracts financial events, profiles, payment options, messages, and images.

2. **Evidence Layer** (deterministic): Processes messages and images to extract structured financial facts. Message content is parsed using pattern matching to extract salary amounts, employment status changes, and other financial facts. No LLM is used.

3. **Financial Simulator** (deterministic): Projects the user's balance forward day-by-day over a 90-day horizon, accounting for:
   - Current available balance
   - Recurring income (detected from historical salary events)
   - Recurring expenses (detected from historical expense patterns, both monthly and weekly)
   - Scheduled/pending transactions
   - Proposed payment plans
   - Spending changes (stop/reduce flexible expenses)

4. **Plan Generator** (deterministic): Enumerates all valid candidate plans:
   - Full payment
   - Installments (matching supplied payment options)
   - Partial payment
   - Wait (defer to earliest safe date)
   - Spending-change variants

5. **Plan Ranker** (deterministic): Applies exact lexicographic ranking per the specification:
   1. Completes by desired_completion_date
   2. No spending changes
   3. Minimizes total cost
   4. Starts payment earlier
   5. Fewer payments
   6. Lowest payment_option_id

6. **Explanation Generator** (deterministic): Uses templated text with computed financial facts. No LLM generation.

## Cost Efficiency

Since the entire pipeline is deterministic:
- Zero API costs
- Zero token usage
- Fully reproducible results
- No hallucination risk in financial decisions
- All recommendations are independently verifiable by re-running the simulation
