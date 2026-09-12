#!/usr/bin/env python3
"""
Buy or Wait? - Financial Affordability Agent (v2)
=================================================
Core deterministic financial decision engine.
"""

import csv
import os
import sys
import re
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from collections import defaultdict, Counter

# ──────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    user_id: str
    home_currency: str
    current_balance: float
    min_balance: float
    priorities: List[str]
    protected_categories: List[str]
    reducible_categories: List[str]
    stoppable_categories: List[str]
    payment_methods: List[str]
    max_installment_months: Optional[int] = None

@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Optional[float]
    currency: str
    event_date: str
    settlement_date: Optional[str]
    status: str
    linked_event_id: Optional[str]
    flexibility: str
    minimum_allowed_amount: Optional[float] = None

@dataclass
class PaymentOption:
    option_id: str
    request_id: str
    method: str
    payment_amount: float
    num_payments: int
    first_payment_date: str
    frequency_days: int
    financing_fee: float
    total_payable: float

@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: str
    request_type: str
    requested_amount: float
    desired_completion_date: str
    allows_partial: bool
    request_text: str

@dataclass
class CandidatePlan:
    method: str
    payment_option_id: Optional[str]
    payments: List[Tuple[str, float]]
    spending_changes: List[Tuple[str, str, Optional[float]]]
    total_cost: float
    completes_by_deadline: bool
    earliest_payment_date: str
    num_payments: int

# ──────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ──────────────────────────────────────────────────────────────────────────────

def load_csv(filepath):
    rows = []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows

def load_all_data(dataset_dir):
    data = {}
    
    # Financial profiles
    profiles = {}
    for row in load_csv(os.path.join(dataset_dir, 'financial_profiles.csv')):
        max_months = None
        if row.get('max_installment_months', '').strip():
            max_months = int(row['max_installment_months'])
        profiles[row['user_id']] = UserProfile(
            user_id=row['user_id'],
            home_currency=row['home_currency'],
            current_balance=float(row['current_available_balance']),
            min_balance=float(row['minimum_balance_to_keep']),
            priorities=row['financial_priorities'].split('|') if row['financial_priorities'] else [],
            protected_categories=row['expense_categories_to_protect'].split('|') if row['expense_categories_to_protect'] else [],
            reducible_categories=row['expense_categories_user_is_willing_to_reduce'].split('|') if row['expense_categories_user_is_willing_to_reduce'] else [],
            stoppable_categories=row['expense_categories_user_is_willing_to_stop'].split('|') if row['expense_categories_user_is_willing_to_stop'] else [],
            payment_methods=(row.get('payment_methods_user_will_consider', '') or '').split('|'),
            max_installment_months=max_months
        )
    data['profiles'] = profiles
    
    # Financial events
    events = []
    for row in load_csv(os.path.join(dataset_dir, 'financial_events.csv')):
        amount = float(row['amount']) if row['amount'].strip() else None
        min_amount = float(row['minimum_allowed_amount']) if row.get('minimum_allowed_amount', '').strip() else None
        events.append(FinancialEvent(
            event_id=row['event_id'],
            user_id=row['user_id'],
            event_type=row['event_type'],
            description=row['description'],
            category=row['category'],
            direction=row['direction'],
            amount=amount,
            currency=row['currency'],
            event_date=row['event_date'],
            settlement_date=row['settlement_date'] if row['settlement_date'].strip() else None,
            status=row['status'],
            linked_event_id=row['linked_event_id'] if row['linked_event_id'].strip() else None,
            flexibility=row['flexibility'],
            minimum_allowed_amount=min_amount
        ))
    data['events'] = events
    
    event_map = {e.event_id: e for e in events}
    data['event_map'] = event_map
    
    # Exchange rates
    rates = {}
    for row in load_csv(os.path.join(dataset_dir, 'exchange_rates.csv')):
        key = (row['rate_date'], row['from_currency'], row['to_currency'])
        rates[key] = float(row['rate'])
    data['exchange_rates'] = rates
    
    # Requests
    requests = []
    for row in load_csv(os.path.join(dataset_dir, 'requests.csv')):
        requests.append(Request(
            request_id=row['request_id'],
            user_id=row['user_id'],
            request_date=row['request_date'],
            request_type=row['request_type'],
            requested_amount=float(row['requested_amount']),
            desired_completion_date=row['desired_completion_date'],
            allows_partial=row['allows_partial_payment'].strip().lower() == 'true',
            request_text=row['request_text']
        ))
    data['requests'] = requests
    
    # Payment options
    options = []
    for row in load_csv(os.path.join(dataset_dir, 'request_payment_options.csv')):
        options.append(PaymentOption(
            option_id=row['payment_option_id'],
            request_id=row['request_id'],
            method=row['payment_method'],
            payment_amount=float(row['payment_amount']),
            num_payments=int(row['number_of_payments']),
            first_payment_date=row['first_payment_date'],
            frequency_days=int(row['payment_frequency_days']) if row['payment_frequency_days'].strip() else 0,
            financing_fee=float(row['financing_fee']),
            total_payable=float(row['total_payable_amount'])
        ))
    data['payment_options'] = options
    options_by_request = defaultdict(list)
    for opt in options:
        options_by_request[opt.request_id].append(opt)
    data['options_by_request'] = options_by_request
    
    # Messages
    messages = []
    for row in load_csv(os.path.join(dataset_dir, 'messages.csv')):
        messages.append({
            'message_id': row['message_id'],
            'user_id': row['user_id'],
            'request_id': row['request_id'] if row['request_id'].strip() else None,
            'related_event_id': row['related_event_id'] if row['related_event_id'].strip() else None,
            'sent_at': row['sent_at'],
            'source_type': row['source_type'],
            'message_text': row['message_text']
        })
    data['messages'] = messages
    msgs_by_user = defaultdict(list)
    for m in messages:
        msgs_by_user[m['user_id']].append(m)
    data['messages_by_user'] = msgs_by_user
    
    # Images
    images = []
    for row in load_csv(os.path.join(dataset_dir, 'images.csv')):
        images.append({
            'image_id': row['image_id'],
            'user_id': row['user_id'],
            'request_id': row['request_id'] if row['request_id'].strip() else None,
            'related_event_id': row['related_event_id'] if row['related_event_id'].strip() else None
        })
    data['images'] = images
    
    # Sample requests
    sample_requests = []
    for row in load_csv(os.path.join(dataset_dir, 'sample_requests.csv')):
        sample_requests.append(row)
    data['sample_requests'] = sample_requests
    
    return data

# ──────────────────────────────────────────────────────────────────────────────
# RECURRING PATTERN DETECTION
# ──────────────────────────────────────────────────────────────────────────────

def _parse_date(d):
    return datetime.strptime(d, '%Y-%m-%d')

def _fmt_date(dt):
    return dt.strftime('%Y-%m-%d')

def _day_of_month(d):
    try:
        return int(d.split('-')[2])
    except:
        return None

def _month_key(d):
    """Return YYYY-MM from a date string."""
    return d[:7]

def detect_recurring_expenses(events):
    """
    Detect recurring expense patterns per user+category.
    Handles both monthly (same day-of-month) and weekly patterns.
    Returns: user_id -> category -> list of {day_of_month, amount, frequency_days}
    """
    groups = defaultdict(lambda: defaultdict(list))
    for e in events:
        if e.status == 'settled' and e.direction == 'debit' and e.amount is not None:
            day = _day_of_month(e.event_date)
            if day is not None:
                groups[e.user_id][(e.category, e.event_type)].append((e.amount, day, e.event_date))
    
    recurring = defaultdict(lambda: defaultdict(list))
    
    for user_id, cat_groups in groups.items():
        for (cat, etype), instances in cat_groups.items():
            if len(instances) < 2:
                continue
            
            # Sort by date
            instances.sort(key=lambda x: x[2])
            
            # Detect frequency from gaps
            gaps = []
            for i in range(1, len(instances)):
                d1 = _parse_date(instances[i-1][2])
                d2 = _parse_date(instances[i][2])
                gaps.append((d2 - d1).days)
            
            if not gaps:
                continue
            
            avg_gap = sum(gaps) / len(gaps)
            
            if avg_gap <= 10:  # Weekly pattern
                # Calculate average weekly amount
                total_amount = sum(a for a, _, _ in instances)
                total_days = (_parse_date(instances[-1][2]) - _parse_date(instances[0][2])).days
                if total_days > 0:
                    weekly_amount = total_amount / (total_days / 7)
                else:
                    weekly_amount = total_amount / len(instances)
                
                # Store with the last known date as reference
                last_date = instances[-1][2]
                recurring[user_id][cat].append({
                    'type': 'weekly',
                    'amount': weekly_amount,
                    'event_type': etype,
                    'reference_date': last_date,
                    'frequency_days': max(7, round(avg_gap)),
                })
            else:  # Monthly pattern
                # Group by day of month
                by_day = defaultdict(list)
                for amount, day, _ in instances:
                    by_day[day].append(amount)
                
                for day, amounts in by_day.items():
                    if len(amounts) >= 2:
                        rounded = [round(a, 2) for a in amounts]
                        counter = Counter(rounded)
                        most_common_amount = counter.most_common(1)[0][0]
                        recurring[user_id][cat].append({
                            'type': 'monthly',
                            'day': day,
                            'amount': most_common_amount,
                            'event_type': etype,
                        })
    
    return recurring

def detect_recurring_income(events, messages_by_user=None):
    """
    Detect recurring income per user.
    Returns: user_id -> {day_of_month: amount, last_salary_date, employment_ended}
    """
    groups = defaultdict(lambda: defaultdict(list))
    employment_ended = set()
    last_salary_date = {}
    
    for e in events:
        if e.status == 'settled' and e.direction == 'credit' and e.amount is not None and e.category == 'salary':
            desc_lower = e.description.lower()
            # Skip one-time events like 'Prorated first salary'
            if 'prorated' in desc_lower:
                continue
            # Check for employment ended
            if 'final' in desc_lower or 'ended' in desc_lower:
                employment_ended.add(e.user_id)
                continue
            day = _day_of_month(e.event_date)
            if day is not None:
                groups[e.user_id][day].append((e.amount, e.event_date, e.description))
                if e.user_id not in last_salary_date or e.event_date > last_salary_date[e.user_id]:
                    last_salary_date[e.user_id] = e.event_date
    
    income = {}
    for user_id, day_groups in groups.items():
        income[user_id] = {}
        for day, instances in day_groups.items():
            if len(instances) >= 2:
                # Use most common amount
                amounts = [round(a, 2) for a, _, _ in instances]
                counter = Counter(amounts)
                income[user_id][day] = counter.most_common(1)[0][0]
            elif len(instances) == 1:
                # Single event - use it as the recurring income
                amount, date, desc = instances[0]
                income[user_id][day] = amount
    
    # Also check messages for employment-termination signals
    employment_ended_users = set()
    if messages_by_user:
        for user_id, msgs in messages_by_user.items():
            if user_id not in income:
                income[user_id] = {}
            for msg in msgs:
                text = msg['message_text'].lower()
                
                # Employment ended
                if any(w in text for w in ['employment has ended', 'no regular salary payments',
                                             'kontrak musiman saat ini telah berakhir',
                                             'seasonal contract has ended']):
                    employment_ended_users.add(user_id)
                    continue
                
                # Check for salary increase/change
                if any(w in text for w in ['salary has increased', 'gaji bulanan anda naik',
                                             'monthly salary has increased']):
                    match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
                    if match:
                        amt = float(match.group(1).replace(',', ''))
                        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg['message_text'])
                        if date_match:
                            day = _day_of_month(date_match.group(1))
                            if day:
                                income[user_id][day] = amt
                
                # First salary / salary confirmation
                if any(w in text for w in ['first salary', 'gaji pertama', 'first salary from']):
                    match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
                    if match:
                        amt = float(match.group(1).replace(',', ''))
                        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg['message_text'])
                        if date_match:
                            day = _day_of_month(date_match.group(1))
                            if day and day not in income[user_id]:
                                income[user_id][day] = amt
                
                # Confirmed/regular salary
                if any(w in text for w in ['confirmed salary', 'regular salary', 'confirmed base salary',
                                             'gaji rutin', 'regular salary of']):
                    match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
                    if match:
                        amt = float(match.group(1).replace(',', ''))
                        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg['message_text'])
                        if date_match:
                            day = _day_of_month(date_match.group(1))
                            if day:
                                income[user_id][day] = amt
                
                # Salary resume
                if 'resumes on' in text:
                    match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
                    if match:
                        amt = float(match.group(1).replace(',', ''))
                        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg['message_text'])
                        if date_match:
                            day = _day_of_month(date_match.group(1))
                            if day:
                                income[user_id][day] = amt
    
    # Return income dict and employment_ended set
    income['_employment_ended'] = employment_ended_users
    return income

# ──────────────────────────────────────────────────────────────────────────────
# EVENT LIFECYCLE RESOLUTION
# ──────────────────────────────────────────────────────────────────────────────

def get_effective_events(user_id, events, event_map):
    """
    Get effective financial events for a user:
    - Exclude cancelled/failed events
    - Exclude unrealized investments
    - Don't double-count linked charge/refund pairs
    - Pending credits don't count as available cash
    """
    user_events = [e for e in events if e.user_id == user_id]
    excluded = set()
    
    for event in user_events:
        if event.status in ('cancelled', 'failed'):
            excluded.add(event.event_id)
        if event.status == 'unrealized':
            excluded.add(event.event_id)
        if event.status == 'pending' and event.direction == 'credit':
            excluded.add(event.event_id)
        if event.linked_event_id and event.linked_event_id in event_map:
            parent = event_map[event.linked_event_id]
            if parent.user_id == user_id:
                if parent.direction == 'debit' and event.direction == 'credit':
                    excluded.add(event.linked_event_id)
    
    return [e for e in user_events if e.event_id not in excluded and e.amount is not None]

# ──────────────────────────────────────────────────────────────────────────────
# MESSAGE EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

def extract_salary_from_messages(messages):
    """
    Extract salary/employment facts from messages for financial forecasting.
    Returns list of facts.
    """
    facts = []
    for msg in messages:
        text = msg['message_text'].lower()
        fact = {'message_id': msg['message_id'], 'type': 'unknown'}
        
        # Employment ended
        if any(w in text for w in ['employment has ended', 'no regular salary payments', 
                                     'kontrak musiman saat ini telah berakhir',
                                     'seasonal contract has ended']):
            fact['type'] = 'employment_ended'
            facts.append(fact)
            continue
        
        # Salary increase
        if any(w in text for w in ['salary has increased', 'gaji bulanan anda naik',
                                     'monthly salary has increased']):
            fact['type'] = 'salary_increase'
            match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
            if match:
                fact['amount'] = float(match.group(1).replace(',', ''))
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg['message_text'])
            if date_match:
                fact['effective_date'] = date_match.group(1)
            facts.append(fact)
            continue
        
        # Salary reduction / temporary pay
        if any(w in text for w in ['salary is reduced', 'gaji bulanan sementara',
                                     'temporary monthly pay', 'reduced to']):
            fact['type'] = 'salary_reduction'
            match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
            if match:
                fact['amount'] = float(match.group(1).replace(',', ''))
            facts.append(fact)
            continue
        
        # First salary / salary confirmation
        if any(w in text for w in ['first salary', 'gaji pertama', 'first salary from']):
            fact['type'] = 'first_salary'
            match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
            if match:
                fact['amount'] = float(match.group(1).replace(',', ''))
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg['message_text'])
            if date_match:
                fact['effective_date'] = date_match.group(1)
            facts.append(fact)
            continue
        
        # Regular/confirmed salary
        if any(w in text for w in ['confirmed salary', 'regular salary', 'confirmed base salary',
                                     'gaji rutin', 'regular salary of']):
            fact['type'] = 'salary_update'
            match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
            if match:
                fact['amount'] = float(match.group(1).replace(',', ''))
            facts.append(fact)
            continue
        
        # Salary resuming
        if any(w in text for w in ['salary of', 'resumes on']):
            fact['type'] = 'salary_resume'
            match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
            if match:
                fact['amount'] = float(match.group(1).replace(',', ''))
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg['message_text'])
            if date_match:
                fact['effective_date'] = date_match.group(1)
            facts.append(fact)
            continue
        
        # Payroll credit / salary credit
        if any(w in text for w in ['payroll credit', 'salary credit', 'gaji rutin untuk']):
            fact['type'] = 'salary_credit'
            match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
            if match:
                fact['amount'] = float(match.group(1).replace(',', ''))
            facts.append(fact)
            continue
        
        # Invoice approved
        if any(w in text for w in ['invoice payment of', 'client approved', 'pembayaran faktur']):
            fact['type'] = 'invoice_approved'
            match = re.search(r'(?:USD|EUR|ZAR|IDR|INR)\s*([\d,]+(?:\.\d+)?)', msg['message_text'])
            if match:
                fact['amount'] = float(match.group(1).replace(',', ''))
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', msg['message_text'])
            if date_match:
                fact['settlement_date'] = date_match.group(1)
            facts.append(fact)
            continue
        
        # Rent increase
        if any(w in text for w in ['renewed lease increases', 'rent increases', 'menaikkan biaya sewa']):
            fact['type'] = 'rent_increase'
            percent_match = re.search(r'(\d+)%', msg['message_text'])
            if percent_match:
                fact['increase_pct'] = float(percent_match.group(1))
            facts.append(fact)
            continue
        
        # Prize scams
        if any(w in text for w in ['pay the release charge', 'pay the processing',
                                     'pay now to receive', 'pay now to avoid']):
            fact['type'] = 'scam_attempt'
            facts.append(fact)
            continue
        
        # Internal transfers
        if any(w in text for w in ['transfer between your two accounts',
                                     'transfer antara dua rekening']):
            fact['type'] = 'internal_transfer'
            facts.append(fact)
            continue
    
    return facts

# ──────────────────────────────────────────────────────────────────────────────
# CORE SIMULATOR
# ──────────────────────────────────────────────────────────────────────────────

class Simulator:
    def __init__(self, data):
        self.data = data
        self.event_map = data['event_map']
        self.recurring_expenses = detect_recurring_expenses(data['events'])
        raw_income = detect_recurring_income(data['events'], data.get('messages_by_user'))
        # Separate employment_ended from income data
        self.employment_ended = raw_income.pop('_employment_ended', set())
        self.recurring_income = raw_income
        self._salary_facts = {}
        for user_id, msgs in data.get('messages_by_user', {}).items():
            self._salary_facts[user_id] = extract_salary_from_messages(msgs)
    
    def _get_recurring_expenses_for_month(self, user_id, year, month, start_date=None):
        """Get recurring expenses expected in a given month."""
        expenses = []
        user_recurring = self.recurring_expenses.get(user_id, {})
        
        for category, patterns in user_recurring.items():
            for pattern in patterns:
                if pattern['type'] == 'monthly':
                    day = pattern['day']
                    try:
                        dt = datetime(year, month, day)
                        expenses.append((dt, pattern['amount'], category, pattern['event_type']))
                    except ValueError:
                        pass
                elif pattern['type'] == 'weekly':
                    # Project weekly expenses from reference date
                    ref_dt = _parse_date(pattern['reference_date'])
                    freq = pattern['frequency_days']
                    month_start = datetime(year, month, 1)
                    month_end = datetime(year, month + 1, 1) if month < 12 else datetime(year + 1, 1, 1)
                    
                    # Find all occurrences in this month
                    current = ref_dt
                    while current < month_end:
                        if current >= month_start and current.month == month:
                            expenses.append((current, pattern['amount'], category, pattern['event_type']))
                        current += timedelta(days=freq)
        
        return expenses
    
    def _get_recurring_income_for_month(self, user_id, year, month):
        """Get recurring income expected in a given month."""
        income = []
        user_income = self.recurring_income.get(user_id, {})
        
        for day, amount in user_income.items():
            try:
                date = datetime(year, month, day)
                income.append((date, amount))
            except ValueError:
                pass
        
        return income
    
    def _get_scheduled_events(self, user_id, start_date, end_date, events):
        """Get scheduled/pending events within date range."""
        scheduled = []
        for e in events:
            if e.user_id != user_id:
                continue
            if e.status not in ('pending', 'scheduled'):
                continue
            if e.settlement_date is None:
                continue
            if e.amount is None:
                continue
            
            # Only count debits as expenses; credits only if scheduled (confirmed)
            if e.direction == 'debit':
                scheduled.append((e.settlement_date, e.amount, e.direction, e.event_id))
            elif e.direction == 'credit' and e.status == 'scheduled':
                scheduled.append((e.settlement_date, e.amount, e.direction, e.event_id))
        
        return scheduled
    
    def simulate(self, user, events, proposed_payments=None, spending_changes=None,
                 start_date=None, horizon_days=90):
        """
        Simulate balance over horizon_days from start_date.
        Returns: (is_safe, min_balance, breach_date)
        """
        if start_date is None:
            start_date = self.data['requests'][0].request_date if self.data['requests'] else _fmt_date(datetime.now())
        
        proposed_payments = proposed_payments or []
        spending_changes = spending_changes or []
        
        # Parse spending changes
        stopped_categories = set()
        reduced_events = {}
        for ct, eid, amt in spending_changes:
            if ct == 'stop':
                if eid in self.event_map:
                    stopped_categories.add(self.event_map[eid].category)
            elif ct == 'reduce_to':
                if eid in self.event_map:
                    reduced_events[eid] = amt
        
        # Build proposed payments lookup
        payments_by_date = defaultdict(float)
        for date_str, amount in proposed_payments:
            payments_by_date[date_str] += amount
        
        # Get scheduled events
        start_dt = _parse_date(start_date)
        end_dt = start_dt + timedelta(days=horizon_days)
        scheduled = self._get_scheduled_events(user.user_id, start_date, end_dt.strftime('%Y-%m-%d'), events)
        
        # Build scheduled events by date
        scheduled_by_date = defaultdict(lambda: {'credit': 0.0, 'debit': 0.0})
        for date_str, amount, direction, eid in scheduled:
            if direction == 'credit':
                scheduled_by_date[date_str]['credit'] += amount
            else:
                scheduled_by_date[date_str]['debit'] += amount
        
        # Simulate day by day
        balance = user.current_balance
        min_observed = balance
        breach_date = None
        
        for day in range(horizon_days):
            current_date = start_dt + timedelta(days=day)
            date_str = _fmt_date(current_date)
            year, month = current_date.year, current_date.month
            
            # Add scheduled credits
            if date_str in scheduled_by_date:
                balance += scheduled_by_date[date_str]['credit']
            
            # Add recurring income (skip if employment ended)
            if user.user_id not in self.employment_ended:
                for inc_date, amount in self._get_recurring_income_for_month(user.user_id, year, month):
                    if inc_date.date() == current_date.date():
                        balance += amount
            
            # Subtract recurring expenses
            for exp_date, amount, category, etype in self._get_recurring_expenses_for_month(user.user_id, year, month):
                if exp_date.date() == current_date.date():
                    if category not in stopped_categories:
                        balance -= amount
            
            # Subtract scheduled debits
            if date_str in scheduled_by_date:
                balance -= scheduled_by_date[date_str]['debit']
            
            # Subtract proposed payments
            if date_str in payments_by_date:
                balance -= payments_by_date[date_str]
            
            # Track minimum
            if balance < min_observed:
                min_observed = balance
            
            if balance < user.min_balance:
                if breach_date is None:
                    breach_date = date_str
        
        is_safe = breach_date is None
        return is_safe, min_observed, breach_date

# ──────────────────────────────────────────────────────────────────────────────
# AMOUNT SAFE TO PAY
# ──────────────────────────────────────────────────────────────────────────────

def calc_amount_safe(user, events, sim, requested_amount, request_date):
    """Binary search for max safe amount today."""
    # Check if 0 is safe
    is_safe_0, _, _ = sim.simulate(user, events, [], [], request_date)
    if not is_safe_0:
        return 0.0
    
    # Check if full amount is safe
    is_safe_full, _, _ = sim.simulate(user, events, [(request_date, requested_amount)], [], request_date)
    if is_safe_full:
        return requested_amount
    
    # Binary search
    low, high = 0.0, min(requested_amount, user.current_balance - user.min_balance + requested_amount)
    high = max(high, 0.0)
    best = 0.0
    
    for _ in range(60):
        mid = (low + high) / 2
        if mid <= 0:
            break
        is_safe, _, _ = sim.simulate(user, events, [(request_date, mid)], [], request_date)
        if is_safe:
            best = mid
            low = mid
        else:
            high = mid
        if high - low < 0.01:
            break
    
    return round(best, 2)

# ──────────────────────────────────────────────────────────────────────────────
# EARLIEST DATE FOR FULL PAYMENT
# ──────────────────────────────────────────────────────────────────────────────

def find_earliest_full_date(user, events, sim, requested_amount, request_date, horizon_days=90):
    start_dt = _parse_date(request_date)
    for day in range(horizon_days):
        dt = start_dt + timedelta(days=day)
        ds = _fmt_date(dt)
        is_safe, _, _ = sim.simulate(user, events, [(ds, requested_amount)], [], request_date)
        if is_safe:
            return ds
    return None

# ──────────────────────────────────────────────────────────────────────────────
# PLAN GENERATION
# ──────────────────────────────────────────────────────────────────────────────

def generate_plans(user, request, options, sim, events, amount_safe, earliest_full_date, request_date):
    candidates = []
    
    # A. Full payment (no spending changes)
    if 'full_payment' in user.payment_methods:
        is_safe, _, _ = sim.simulate(user, events, [(request_date, request.requested_amount)], [], request_date)
        if is_safe:
            candidates.append(CandidatePlan(
                method='full_payment', payment_option_id=None,
                payments=[(request_date, request.requested_amount)],
                spending_changes=[], total_cost=request.requested_amount,
                completes_by_deadline=True, earliest_payment_date=request_date, num_payments=1
            ))
    
    # B. Installments
    if 'installments' in user.payment_methods:
        for opt in options:
            if opt.method != 'installments':
                continue
            if user.max_installment_months is not None:
                max_pmts = user.max_installment_months * 30 / opt.frequency_days if opt.frequency_days > 0 else 0
                if opt.num_payments > max_pmts:
                    continue
            
            payments = []
            first_dt = _parse_date(opt.first_payment_date)
            for i in range(opt.num_payments):
                pmt_dt = first_dt + timedelta(days=i * opt.frequency_days)
                payments.append((_fmt_date(pmt_dt), opt.payment_amount))
            
            last_date = payments[-1][0] if payments else request_date
            completes = last_date <= request.desired_completion_date
            
            is_safe, _, _ = sim.simulate(user, events, payments, [], request_date)
            if is_safe:
                candidates.append(CandidatePlan(
                    method='installments', payment_option_id=opt.option_id,
                    payments=payments, spending_changes=[],
                    total_cost=opt.total_payable, completes_by_deadline=completes,
                    earliest_payment_date=payments[0][0] if payments else request_date,
                    num_payments=opt.num_payments
                ))
    
    # C. Partial payment
    if 'partial_payment' in user.payment_methods and request.allows_partial:
        if 0 < amount_safe < request.requested_amount and earliest_full_date:
            remaining = request.requested_amount - amount_safe
            pmts = [(request_date, amount_safe), (earliest_full_date, remaining)]
            is_safe, _, _ = sim.simulate(user, events, pmts, [], request_date)
            if is_safe and earliest_full_date <= request.desired_completion_date:
                candidates.append(CandidatePlan(
                    method='partial_payment', payment_option_id=None,
                    payments=pmts, spending_changes=[], total_cost=request.requested_amount,
                    completes_by_deadline=True, earliest_payment_date=request_date, num_payments=2
                ))
    
    # D. Wait
    if 'full_payment' in user.payment_methods and earliest_full_date:
        is_safe, _, _ = sim.simulate(user, events, [(earliest_full_date, request.requested_amount)], [], request_date)
        if is_safe:
            completes = earliest_full_date <= request.desired_completion_date
            candidates.append(CandidatePlan(
                method='wait', payment_option_id=None,
                payments=[(earliest_full_date, request.requested_amount)],
                spending_changes=[], total_cost=request.requested_amount,
                completes_by_deadline=completes, earliest_payment_date=earliest_full_date, num_payments=1
            ))
    
    # E. Full payment with spending changes
    flexible_events = [e for e in events
                       if e.flexibility in ('stoppable', 'reducible', 'reducible_or_stoppable')
                       and e.status == 'settled' and e.direction == 'debit' and e.amount is not None]
    
    flex_by_cat = defaultdict(list)
    for e in flexible_events:
        flex_by_cat[e.category].append(e)
    
    for category, cat_events in flex_by_cat.items():
        if not cat_events:
            continue
        can_stop = category in user.stoppable_categories
        can_reduce = category in user.reducible_categories
        if not can_stop and not can_reduce:
            continue
        
        latest = max(cat_events, key=lambda e: e.event_date)
        
        if can_stop:
            sc = [('stop', latest.event_id, None)]
            is_safe, _, _ = sim.simulate(user, events, [(request_date, request.requested_amount)], sc, request_date)
            if is_safe:
                candidates.append(CandidatePlan(
                    method='full_payment', payment_option_id=None,
                    payments=[(request_date, request.requested_amount)],
                    spending_changes=sc, total_cost=request.requested_amount,
                    completes_by_deadline=True, earliest_payment_date=request_date, num_payments=1
                ))
        
        if can_reduce and latest.minimum_allowed_amount is not None:
            sc = [('reduce_to', latest.event_id, latest.minimum_allowed_amount)]
            is_safe, _, _ = sim.simulate(user, events, [(request_date, request.requested_amount)], sc, request_date)
            if is_safe:
                candidates.append(CandidatePlan(
                    method='full_payment', payment_option_id=None,
                    payments=[(request_date, request.requested_amount)],
                    spending_changes=sc, total_cost=request.requested_amount,
                    completes_by_deadline=True, earliest_payment_date=request_date, num_payments=1
                ))
    
    return candidates

# ──────────────────────────────────────────────────────────────────────────────
# RANKING
# ──────────────────────────────────────────────────────────────────────────────

def rank_plans(candidates):
    def sort_key(p):
        return (
            0 if p.completes_by_deadline else 1,
            len(p.spending_changes),
            p.total_cost,
            p.earliest_payment_date,
            p.num_payments,
            p.payment_option_id or 'zzz',
        )
    return sorted(candidates, key=sort_key)

# ──────────────────────────────────────────────────────────────────────────────
# EXPLANATION GENERATION
# ──────────────────────────────────────────────────────────────────────────────

def gen_explanation(plan, req, user, amount_safe, earliest_full):
    c = user.home_currency
    
    if plan is None or plan.method == 'not_recommended':
        if earliest_full:
            return (f"Although {c} {amount_safe:,.2f} is available today, "
                   f"the full amount cannot be completed safely within 90 days.")
        return f"None of the available options keeps the {c} {user.min_balance:,.2f} minimum protected."
    
    if plan.method == 'full_payment' and not plan.spending_changes:
        if amount_safe >= req.requested_amount:
            return f"Pay {c} {req.requested_amount:,.2f} today. This leaves at least {c} {user.min_balance:,.2f} available over the next 90 days."
        else:
            return f"Pay {c} {req.requested_amount:,.2f} on {plan.earliest_payment_date}. Paying earlier would take the balance below the {c} {user.min_balance:,.2f} minimum."
    
    if plan.method == 'full_payment' and plan.spending_changes:
        parts = []
        for ct, eid, amt in plan.spending_changes:
            if ct == 'stop':
                # Find the event to get the category name
                evt = user._event_map.get(eid) if hasattr(user, '_event_map') else None
                parts.append(f"Stop the {eid} expense")
            elif ct == 'reduce_to':
                parts.append(f"Reduce the {eid} expense to {c} {amt:,.2f}")
        change_str = " and ".join(parts)
        return f"{change_str}, then pay {c} {req.requested_amount:,.2f} today. This leaves at least {c} {user.min_balance:,.2f} available."
    
    if plan.method == 'installments':
        n = plan.num_payments
        amt = plan.payments[0][1] if plan.payments else 0
        start = plan.payments[0][0] if plan.payments else ''
        return f"Use {n} installments of {c} {amt:,.2f}, starting {start}. This leaves at least {c} {user.min_balance:,.2f} available."
    
    if plan.method == 'partial_payment':
        first = plan.payments[0]
        second = plan.payments[1]
        return f"Pay {c} {first[1]:,.2f} today and the remaining {c} {second[1]:,.2f} on {second[0]}. This completes the full request and keeps the {c} {user.min_balance:,.2f} minimum protected."
    
    if plan.method == 'wait':
        return f"Pay {c} {req.requested_amount:,.2f} in full on {plan.earliest_payment_date}. Paying earlier would take the balance below the {c} {user.min_balance:,.2f} minimum."
    
    return f"Do not proceed. None of the available options keeps the {c} {user.min_balance:,.2f} minimum protected."

# ──────────────────────────────────────────────────────────────────────────────
# REQUEST PROCESSING
# ──────────────────────────────────────────────────────────────────────────────

def process_request(req, data, sim):
    user = data['profiles'].get(req.user_id)
    if not user:
        return {
            'request_id': req.request_id,
            'amount_safe_to_pay': '0.00',
            'affordability_status': 'not_affordable',
            'recommended_payment_method': 'not_recommended',
            'payment_plan': 'none',
            'earliest_date_for_full_payment': '',
            'spending_changes_needed': 'none',
            'decision_explanation': 'User profile not found.'
        }
    
    events = get_effective_events(req.user_id, data['events'], data['event_map'])
    options = data['options_by_request'].get(req.request_id, [])
    
    # Calculate
    amount_safe = calc_amount_safe(user, events, sim, req.requested_amount, req.request_date)
    earliest_full = find_earliest_full_date(user, events, sim, req.requested_amount, req.request_date)
    
    # Generate plans
    candidates = generate_plans(user, req, options, sim, events, amount_safe, earliest_full, req.request_date)
    ranked = rank_plans(candidates)
    
    best = ranked[0] if ranked else None
    
    # Derive status
    if best is None:
        if earliest_full and earliest_full <= req.desired_completion_date:
            status = 'affordable_later'
            payment_method = 'wait'
            payments = [(earliest_full, req.requested_amount)]
            spending = []
            total_cost = req.requested_amount
            completes = True
            efpd = earliest_full
        else:
            status = 'not_affordable'
            payment_method = 'not_recommended'
            payments = []
            spending = []
            total_cost = 0
            completes = False
            efpd = earliest_full or ''
    else:
        payment_method = best.method
        payments = best.payments
        spending = best.spending_changes
        total_cost = best.total_cost
        completes = best.completes_by_deadline
        efpd = earliest_full or ''
        
        if payment_method == 'full_payment' and not spending:
            if amount_safe >= req.requested_amount:
                status = 'affordable_now'
                efpd = req.request_date
            elif completes:
                status = 'affordable_later'
            else:
                status = 'not_affordable'
        elif payment_method == 'wait':
            if completes:
                status = 'affordable_later'
            else:
                status = 'not_affordable'
                payment_method = 'not_recommended'
        elif completes:
            status = 'affordable_with_plan'
        else:
            status = 'not_affordable'
            payment_method = 'not_recommended'
    
    # Format payment plan - use .2f only if amount has decimals
    def fmt_amt(a):
        if a == int(a):
            return str(int(a))
        return f"{a:.2f}"
    if payments:
        plan_str = '|'.join(f"{d}:{fmt_amt(a)}" for d, a in payments)
    else:
        plan_str = 'none'
    
    # Format spending changes
    if spending:
        sc_parts = []
        for ct, eid, amt in spending:
            if ct == 'stop':
                sc_parts.append(f"stop:{eid}")
            elif ct == 'reduce_to':
                sc_parts.append(f"reduce_to:{eid}:{fmt_amt(amt)}")
        sc_str = '|'.join(sc_parts)
    else:
        sc_str = 'none'
    
    # Format earliest date
    if payment_method == 'full_payment' and status == 'affordable_now':
        efpd_fmt = req.request_date
    elif payment_method in ('wait', 'full_payment') and status in ('affordable_later', 'affordable_with_plan'):
        efpd_fmt = efpd
    elif payment_method == 'installments':
        efpd_fmt = efpd
    elif payment_method == 'partial_payment':
        efpd_fmt = efpd
    else:
        efpd_fmt = efpd if efpd else ''
    
    # Cap amount_safe
    amount_safe = min(amount_safe, req.requested_amount)
    
    explanation = gen_explanation(best, req, user, amount_safe, earliest_full)
    
    return {
        'request_id': req.request_id,
        'amount_safe_to_pay': f"{amount_safe:.2f}",
        'affordability_status': status,
        'recommended_payment_method': payment_method,
        'payment_plan': plan_str,
        'earliest_date_for_full_payment': efpd_fmt,
        'spending_changes_needed': sc_str,
        'decision_explanation': explanation
    }

# ──────────────────────────────────────────────────────────────────────────────
# OUTPUT VERIFICATION
# ──────────────────────────────────────────────────────────────────────────────

def verify_output(rows, requests):
    errors = []
    if len(rows) != len(requests):
        errors.append(f"Row count: got {len(rows)}, expected {len(requests)}")
    
    valid_status = {'affordable_now', 'affordable_with_plan', 'affordable_later', 'not_affordable'}
    valid_methods = {'full_payment', 'partial_payment', 'installments', 'wait', 'not_recommended'}
    
    for row in rows:
        if row.get('affordability_status') not in valid_status:
            errors.append(f"Invalid status in {row.get('request_id')}: {row.get('affordability_status')}")
        if row.get('recommended_payment_method') not in valid_methods:
            errors.append(f"Invalid method in {row.get('request_id')}: {row.get('recommended_payment_method')}")
        try:
            amt = float(row.get('amount_safe_to_pay', 0))
            if amt < 0:
                errors.append(f"Negative amount in {row.get('request_id')}")
        except:
            errors.append(f"Invalid amount in {row.get('request_id')}")
    
    return errors

# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def run_pipeline(dataset_dir, output_path):
    print("Loading data...")
    data = load_all_data(dataset_dir)
    
    print("Initializing simulator...")
    sim = Simulator(data)
    
    print(f"Processing {len(data['requests'])} requests...")
    rows = []
    for i, req in enumerate(data['requests']):
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(data['requests'])}...")
        row = process_request(req, data, sim)
        rows.append(row)
    
    print("Verifying...")
    errors = verify_output(rows, data['requests'])
    if errors:
        print(f"  {len(errors)} errors found")
        for e in errors[:5]:
            print(f"  - {e}")
    
    print(f"Writing {len(rows)} rows to {output_path}")
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'request_id', 'amount_safe_to_pay', 'affordability_status',
            'recommended_payment_method', 'payment_plan',
            'earliest_date_for_full_payment', 'spending_changes_needed',
            'decision_explanation'
        ])
        writer.writeheader()
        writer.writerows(rows)
    
    print("Done!")
    return rows

if __name__ == '__main__':
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    dataset_dir = os.path.join(project_root, 'dataset')
    output_path = os.path.join(project_root, 'output.csv')
    run_pipeline(dataset_dir, output_path)
