#!/usr/bin/env python3
"""
Buy or Wait? - Financial Affordability Agent (v3)
=================================================
Deterministic financial decision engine.

v3 improvements over v2:
  * Blank event amounts are restored from OCR-extracted receipt data
    (engine/image_amounts.json, produced by tools/extract_image_amounts.py).
  * Full dated FX conversion: every foreign-currency event is converted to the
    user's home currency using the exchange rate for its settlement date and
    the stated from->to direction (inverse pairs supported).
  * Rebuilt recurring-expense detection: recurrence requires repeated settled
    debits at a consistent cadence and amount, and projections start only
    after the last historical occurrence (no more over-counting past
    one-off transactions as future expenses).
  * Safer forecast semantics: spending changes only affect genuinely recurring
    expenses, fixed-price scheduled rent is not double counted, and only
    scheduled credits (not pending ones) count as confirmed future income.
  * Date-window discipline: events and payment options dated before the
    request date are ignored for that request's forecast.
"""

import csv
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

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
# SMALL DATE / FORMAT HELPERS
# ──────────────────────────────────────────────────────────────────────────────


def _parse_date(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d")


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _day_of_month(d: str) -> Optional[int]:
    try:
        return int(d.split("-")[2])
    except (ValueError, IndexError, AttributeError):
        return None


def _month_add(d: datetime, k: int) -> datetime:
    """Add k calendar months, clamping the day to month length (Jan 31 -> Feb 28)."""
    m = d.month - 1 + k
    y = d.year + m // 12
    m = m % 12 + 1
    for day in (31, 30, 29, 28):
        try:
            return d.replace(year=y, month=m, day=day)
        except ValueError:
            continue
    return d.replace(year=y, month=m)


def _fmt_amt(a: float) -> str:
    """Format amounts like the sample outputs: integers bare, otherwise 2dp."""
    if a == int(a):
        return str(int(a))
    return f"{a:.2f}"


def _money(x: float) -> str:
    return f"{x:,.2f}"


# ──────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ──────────────────────────────────────────────────────────────────────────────


def _load_csv(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_all_data(dataset_dir):
    data = {}

    # Financial profiles
    profiles = {}
    for row in _load_csv(os.path.join(dataset_dir, "financial_profiles.csv")):
        max_months = None
        if row.get("max_installment_months", "").strip():
            max_months = int(row["max_installment_months"])
        profiles[row["user_id"]] = UserProfile(
            user_id=row["user_id"],
            home_currency=row["home_currency"],
            current_balance=float(row["current_available_balance"]),
            min_balance=float(row["minimum_balance_to_keep"]),
            priorities=row["financial_priorities"].split("|")
            if row["financial_priorities"]
            else [],
            protected_categories=row["expense_categories_to_protect"].split("|")
            if row["expense_categories_to_protect"]
            else [],
            reducible_categories=row[
                "expense_categories_user_is_willing_to_reduce"
            ].split("|")
            if row["expense_categories_user_is_willing_to_reduce"]
            else [],
            stoppable_categories=row[
                "expense_categories_user_is_willing_to_stop"
            ].split("|")
            if row["expense_categories_user_is_willing_to_stop"]
            else [],
            payment_methods=(row.get("payment_methods_user_will_consider", "") or "").split("|"),
            max_installment_months=max_months,
        )
    data["profiles"] = profiles

    # Financial events
    events = []
    for row in _load_csv(os.path.join(dataset_dir, "financial_events.csv")):
        amount = float(row["amount"]) if row["amount"].strip() else None
        min_amount = (
            float(row["minimum_allowed_amount"])
            if row.get("minimum_allowed_amount", "").strip()
            else None
        )
        events.append(
            FinancialEvent(
                event_id=row["event_id"],
                user_id=row["user_id"],
                event_type=row["event_type"],
                description=row["description"],
                category=row["category"],
                direction=row["direction"],
                amount=amount,
                currency=row["currency"],
                event_date=row["event_date"],
                settlement_date=row["settlement_date"].strip() or None,
                status=row["status"],
                linked_event_id=row["linked_event_id"].strip() or None,
                flexibility=row["flexibility"],
                minimum_allowed_amount=min_amount,
            )
        )
    data["events"] = events
    event_map = {e.event_id: e for e in events}
    data["event_map"] = event_map

    # Exchange rates (dated, directional)
    rates = {}
    for row in _load_csv(os.path.join(dataset_dir, "exchange_rates.csv")):
        rates[(row["rate_date"], row["from_currency"], row["to_currency"])] = float(row["rate"])
    data["exchange_rates"] = rates

    # Requests
    requests = []
    for row in _load_csv(os.path.join(dataset_dir, "requests.csv")):
        requests.append(
            Request(
                request_id=row["request_id"],
                user_id=row["user_id"],
                request_date=row["request_date"],
                request_type=row["request_type"],
                requested_amount=float(row["requested_amount"]),
                desired_completion_date=row["desired_completion_date"],
                allows_partial=row["allows_partial_payment"].strip().lower() == "true",
                request_text=row["request_text"],
            )
        )
    data["requests"] = requests

    # Payment options
    options_by_request = defaultdict(list)
    for row in _load_csv(os.path.join(dataset_dir, "request_payment_options.csv")):
        opt = PaymentOption(
            option_id=row["payment_option_id"],
            request_id=row["request_id"],
            method=row["payment_method"],
            payment_amount=float(row["payment_amount"]),
            num_payments=int(row["number_of_payments"]),
            first_payment_date=row["first_payment_date"],
            frequency_days=int(row["payment_frequency_days"])
            if row["payment_frequency_days"].strip()
            else 0,
            financing_fee=float(row["financing_fee"]),
            total_payable=float(row["total_payable_amount"]),
        )
        options_by_request[opt.request_id].append(opt)
    data["options_by_request"] = options_by_request

    # Messages
    messages_by_user = defaultdict(list)
    for row in _load_csv(os.path.join(dataset_dir, "messages.csv")):
        messages_by_user[row["user_id"]].append(
            {
                "message_id": row["message_id"],
                "user_id": row["user_id"],
                "request_id": row["request_id"].strip() or None,
                "related_event_id": row["related_event_id"].strip() or None,
                "sent_at": row["sent_at"],
                "source_type": row["source_type"],
                "message_text": row["message_text"],
            }
        )
    data["messages_by_user"] = messages_by_user

    # Images index: event_id -> image_id
    images_by_event = {}
    for row in _load_csv(os.path.join(dataset_dir, "images.csv")):
        if row["related_event_id"].strip():
            images_by_event[row["related_event_id"].strip()] = row["image_id"].strip()
    data["images_by_event"] = images_by_event

    # Sample requests (reference examples)
    data["sample_requests"] = _load_csv(os.path.join(dataset_dir, "sample_requests.csv"))

    return data


# ──────────────────────────────────────────────────────────────────────────────
# OCR-RESTORED AMOUNTS (blank-amount events)
# ──────────────────────────────────────────────────────────────────────────────

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_image_amounts():
    path = os.path.join(_ENGINE_DIR, "image_amounts.json")
    if not os.path.exists(path):
        return {}
    raw = json.load(open(path, encoding="utf-8"))
    return {k: float(v) for k, v in raw.items() if not k.startswith("_")}


# ──────────────────────────────────────────────────────────────────────────────
# FX CONVERSION
# ──────────────────────────────────────────────────────────────────────────────


def make_fx(data):
    rates = data["exchange_rates"]

    def convert(amount: float, date: str, from_ccy: str, to_ccy: str) -> float:
        if amount is None:
            return None
        if from_ccy == to_ccy:
            return amount
        r = rates.get((date, from_ccy, to_ccy))
        if r is None:
            r = rates.get((date, to_ccy, from_ccy))
            if r is not None and r != 0:
                r = 1.0 / r
        if r is None:
            # fall back to the nearest rate date on or before `date`
            cands = sorted(
                {d for (d, f, t) in rates if (f, t) in ((from_ccy, to_ccy), (to_ccy, from_ccy)) and d <= date}
            )
            if cands:
                d = cands[-1]
                r = rates.get((d, from_ccy, to_ccy))
                if r is None:
                    r0 = rates.get((d, to_ccy, from_ccy))
                    r = (1.0 / r0) if r0 else None
        if r is None:
            return amount  # unconvertible: leave as-is (conservative no-op)
        return amount * r

    return convert


# ──────────────────────────────────────────────────────────────────────────────
# EVENT LIFECYCLE RESOLUTION (effective events, home-currency amounts)
# ──────────────────────────────────────────────────────────────────────────────

# fixed-price categories: a scheduled debit here is the first instalment of a
# fixed monthly obligation whose future months already appear as their own
# scheduled events — do not also project it as a recurring expense
FIXED_PRICE_CATEGORIES = {"rent"}


def effective_events(user_id, events, event_map, convert, image_amounts):
    """Effective home-currency events for one user.

    Excludes cancelled / failed / unrealized events, pending credits (no cash
    value yet), and duplicate linked rows (a linked child describing the same
    transaction as its parent). Blank amounts are restored from OCR-extracted
    receipt data when available. All amounts are converted to the user's home
    currency using the dated FX table.
    """
    excluded = set()
    for event in events:
        if event.user_id != user_id:
            continue
        if event.status in ("cancelled", "failed", "unrealized"):
            excluded.add(event.event_id)
        if event.status == "pending" and event.direction == "credit":
            excluded.add(event.event_id)
        if event.linked_event_id and event.linked_event_id in event_map:
            parent = event_map[event.linked_event_id]
            if parent.user_id == user_id:
                # linked child describing the parent's lifecycle (e.g. refund
                # of a charge, fee leg of a loan): count only the parent
                if event.direction != parent.direction:
                    excluded.add(event.linked_event_id)

    out = []
    for e in events:
        if e.user_id != user_id or e.event_id in excluded:
            continue
        amt = e.amount
        if amt is None:
            amt = image_amounts.get(e.event_id)
        if amt is None:
            continue  # no amount and no evidence to restore it
        home = None  # resolved below via profile currency
        out.append((e, amt, home))

    return out  # (event, raw_amount, home_not_used) — FX applied in Simulator


def build_user_events(user, data, convert, image_amounts):
    """Return list of effective events with home-currency amounts."""
    home = user.home_currency
    result = []
    for e in data["events"]:
        if e.user_id != user.user_id:
            continue
        if e.status in ("cancelled", "failed", "unrealized"):
            continue
        if e.status == "pending" and e.direction == "credit":
            continue
        if e.linked_event_id and e.linked_event_id in data["event_map"]:
            parent = data["event_map"][e.linked_event_id]
            if parent.user_id == user.user_id and e.direction != parent.direction:
                continue
        amt = e.amount
        if amt is None:
            amt = image_amounts.get(e.event_id)
        if amt is None:
            continue
        date = e.settlement_date or e.event_date
        amt_home = convert(amt, date, e.currency, home)
        result.append((e, amt_home))
    return result


# ──────────────────────────────────────────────────────────────────────────────
# MESSAGE FACTS (salary / employment evidence — untrusted text, parsed only)
# ──────────────────────────────────────────────────────────────────────────────

_CURRENCY_TOKEN = r"(?:USD|EUR|ZAR|IDR|INR|Rp|Rs\.?|₹|R)"
_AMOUNT_RE = re.compile(_CURRENCY_TOKEN + r"\s*([\d,]+(?:\.\d+)?)")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _extract_amount(text):
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def extract_salary_facts(messages):
    """Parse untrusted message text into salary/employment facts.

    Embedded instructions never override the decision rules; these facts only
    supply amounts and dates the deterministic engine would otherwise lack.
    """
    facts = []
    for msg in messages:
        text = msg["message_text"]
        low = text.lower()

        if any(
            w in low
            for w in [
                "employment has ended",
                "no regular salary",
                "kontrak musiman",
                "seasonal contract has ended",
                "final salary",
            ]
        ):
            facts.append({"type": "employment_ended", "message_id": msg["message_id"]})
            continue

        if any(
            w in low
            for w in [
                "salary has increased",
                "gaji bulanan anda naik",
                "monthly salary has increased",
                "salary increased to",
            ]
        ):
            f = {"type": "salary_increase", "message_id": msg["message_id"]}
            f["amount"] = _extract_amount(text)
            d = _DATE_RE.search(text)
            f["effective_date"] = d.group(1) if d else None
            facts.append(f)
            continue

        if any(
            w in low
            for w in [
                "salary is reduced",
                "gaji bulanan sementara",
                "temporary monthly pay",
                "salary reduced to",
                "reduced to",
            ]
        ):
            f = {"type": "salary_reduction", "message_id": msg["message_id"]}
            f["amount"] = _extract_amount(text)
            facts.append(f)
            continue

        if any(
            w in low
            for w in ["first salary", "gaji pertama", "confirmed salary",
                      "regular salary", "confirmed base salary", "gaji rutin",
                      "resumes on"]
        ):
            f = {"type": "salary_update", "message_id": msg["message_id"]}
            f["amount"] = _extract_amount(text)
            d = _DATE_RE.search(text)
            f["effective_date"] = d.group(1) if d else None
            facts.append(f)
            continue

    return facts


# ──────────────────────────────────────────────────────────────────────────────
# RECURRING PATTERN DETECTION (strict)
# ──────────────────────────────────────────────────────────────────────────────


def detect_recurring_expenses(user_events):
    """Strictly detect recurring expense patterns from settled debit history.

    A pattern is confirmed only when the same user/category/event_type shows:
      * monthly: >= 2 settled debits at consistent amounts (CV <= 0.15), with
        a dominant day-of-month window (max gap within group <= 6 days)
      * weekly:  >= 3 settled debits with average cadence 5..10 days and
        consistent amounts (CV <= 0.5)

    Amounts are the typical (most common rounded) amounts. `last_date` records
    the final observed occurrence so projections start after history, never
    duplicating it.
    """
    groups = defaultdict(list)
    for e, amt in user_events:
        if e.status == "settled" and e.direction == "debit" and amt is not None:
            groups[(e.category, e.event_type)].append((e.event_date, amt))

    patterns = []
    for (cat, etype), inst in groups.items():
        if len(inst) < 2:
            continue
        inst.sort()
        dates = [_parse_date(d) for d, _ in inst]
        amounts = [a for _, a in inst]
        mean = sum(amounts) / len(amounts)
        cv = (sum((a - mean) ** 2 for a in amounts) / len(amounts)) ** 0.5 / mean if mean else 9e9
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_gap = sum(gaps) / len(gaps)

        if avg_gap <= 10 and len(inst) >= 3 and cv <= 0.5:
            # weekly pattern
            patterns.append(
                {
                    "cat": cat,
                    "etype": etype,
                    "freq": "weekly",
                    "step": max(7, int(round(avg_gap))),
                    "amounts": sorted(amounts),
                    "typical": mean,
                    "last_date": dates[-1],
                }
            )
        else:
            # monthly: group by day-of-month window, require amount consistency
            if cv > 0.15:
                continue
            by_day = defaultdict(list)
            for d, a in inst:
                day = int(d.split("-")[2])
                by_day[day].append((d, a))
            best_day, best_n = None, 0
            for day, lst in by_day.items():
                if len(lst) >= 2 and len(lst) > best_n:
                    best_day, best_n = day, len(lst)
            if best_day is None:
                continue
            day_amounts = [a for _, a in by_day[best_day]]
            rounded = Counter(round(a, 2) for a in day_amounts)
            typical = rounded.most_common(1)[0][0]
            patterns.append(
                {
                    "cat": cat,
                    "etype": etype,
                    "freq": "monthly",
                    "day": best_day,
                    "amounts": sorted(day_amounts),
                    "typical": typical,
                    "last_date": dates[-1],
                }
            )
    return patterns


def detect_recurring_income(user_events, salary_facts):
    """Detect recurring salary from settled + scheduled salary credits.

    Scheduled "next confirmed salary" events seed the pattern for users with
    too little settled history (e.g. only a prorated first salary). Because
    scheduled credits also flow through build_scheduled, projections start
    strictly after the latest observed occurrence — no double counting.

    Returns (list of {day, amount, last_date}, employment_ended_flag).
    """
    groups = defaultdict(list)
    employment_ended = False
    for e, amt in user_events:
        if e.status not in ("settled", "scheduled"):
            continue
        if e.direction != "credit" or e.category != "salary" or amt is None:
            continue
        low = e.description.lower()
        if "prorated" in low:
            continue
        if "final" in low or "ended" in low:
            employment_ended = True
            continue
        day = _day_of_month(e.event_date)
        if day is None:
            continue
        groups[day].append((e.event_date, amt))

    out = []
    for day, inst in groups.items():
        amounts = [round(a, 2) for _, a in inst]
        typical = Counter(amounts).most_common(1)[0][0]
        last = max(d for d, _ in inst)
        out.append({"day": day, "amount": typical, "last_date": last})

    # message-derived overrides (newer, explicit facts win)
    for f in salary_facts:
        if f["type"] == "employment_ended":
            employment_ended = True
        elif f["type"] in ("salary_increase", "salary_update") and f.get("amount"):
            d = f.get("effective_date")
            if d:
                day = _day_of_month(d)
                if day:
                    out = [o for o in out if o["day"] != day]
                    out.append({"day": day, "amount": f["amount"], "last_date": d})
    return out, employment_ended


# ──────────────────────────────────────────────────────────────────────────────
# SCHEDULED (CONFIRMED) FUTURE CASH FLOW
# ──────────────────────────────────────────────────────────────────────────────


def build_scheduled(user_events, recurring):
    """Map settlement date -> cash delta from scheduled/pending events.

    Debits always reserve cash (safe direction). Only *scheduled* credits
    count as confirmed future income; pending credits were already excluded.
    """
    rec_cats = {(p["cat"], p["etype"]) for p in recurring}
    by_date = defaultdict(float)
    for e, amt in user_events:
        if e.status not in ("pending", "scheduled"):
            continue
        if e.settlement_date is None or amt is None:
            continue
        if e.direction == "debit":
            by_date[e.settlement_date] -= amt
        elif e.direction == "credit" and e.status == "scheduled":
            by_date[e.settlement_date] += amt
    return by_date


# ──────────────────────────────────────────────────────────────────────────────
# CORE SIMULATOR
# ──────────────────────────────────────────────────────────────────────────────


class Simulator:
    def __init__(self, data):
        self.data = data
        self.event_map = data["event_map"]
        self.convert = make_fx(data)
        self.image_amounts = load_image_amounts()

        self._user_cache = {}

    # -- per-user preparation ------------------------------------------------

    def _prepare(self, user):
        if user.user_id in self._user_cache:
            return self._user_cache[user.user_id]

        user_events = build_user_events(
            user, self.data, self.convert, self.image_amounts
        )
        salary_facts = extract_salary_facts(
            self.data["messages_by_user"].get(user.user_id, [])
        )
        recurring = detect_recurring_expenses(user_events)
        income, employment_ended = detect_recurring_income(user_events, salary_facts)
        scheduled = build_scheduled(user_events, recurring)

        prep = {
            "events": user_events,
            "recurring": recurring,
            "income": income,
            "employment_ended": employment_ended,
            "scheduled": scheduled,
        }
        self._user_cache[user.user_id] = prep
        return prep

    # -- recurring cash-flow projection ---------------------------------------

    def _recurring_flows(self, user, horizon_end):
        """Materialise projected recurring income and expenses within horizon.

        Projections start strictly after the last historical occurrence of the
        pattern, so history is never double counted.
        """
        prep = self._prepare(user)
        income_flows = []  # (date, amount)
        if not prep["employment_ended"]:
            for p in prep["income"]:
                d = _parse_date(p["last_date"])
                while True:
                    d = _month_add(d, 1)
                    if d > horizon_end:
                        break
                    try:
                        d2 = d.replace(day=p["day"])
                    except ValueError:
                        continue
                    income_flows.append((_fmt_date(d2), p["amount"]))

        expense_flows = []  # (date, amount, cat, etype, pattern)
        for p in prep["recurring"]:
            d = p["last_date"]
            if p["freq"] == "monthly":
                while True:
                    d = _month_add(d, 1)
                    if d > horizon_end:
                        break
                    try:
                        d2 = d.replace(day=p["day"])
                    except ValueError:
                        continue
                    if d2 <= p["last_date"]:
                        continue
                    expense_flows.append((_fmt_date(d2), p["typical"], p["cat"], p["etype"], p))
            else:  # weekly
                while True:
                    d = d + timedelta(days=p["step"])
                    if d > horizon_end:
                        break
                    expense_flows.append((_fmt_date(d), p["typical"], p["cat"], p["etype"], p))
        return income_flows, expense_flows

    # -- the day-by-day balance check -----------------------------------------

    def simulate(self, user, start_date, payments=None, spending_changes=None,
                 horizon_days=90):
        """Simulate the balance for `horizon_days` from start_date.

        Returns (is_safe, min_balance, breach_date).
        """
        payments = payments or []
        spending_changes = spending_changes or []

        start_dt = _parse_date(start_date)
        end_dt = start_dt + timedelta(days=horizon_days)

        prep = self._prepare(user)

        # payments: only future-dated ones matter
        payments_by_date = defaultdict(float)
        for ds, amt in payments:
            if ds >= start_date:
                payments_by_date[ds] += amt

        # spending changes
        stopped = set()
        reduced = {}
        for kind, eid, new_amt in spending_changes:
            ev = self.event_map.get(eid)
            if ev is None:
                continue
            if kind == "stop":
                stopped.add((ev.category, ev.event_type))
            elif kind == "reduce_to" and new_amt is not None:
                reduced[(ev.category, ev.event_type)] = min(
                    reduced.get((ev.category, ev.event_type), 9e18), new_amt
                )

        income_flows, expense_flows = self._recurring_flows(user, end_dt)

        # per-day aggregation
        credits = defaultdict(float)
        debits = defaultdict(float)
        for ds, amt in income_flows:
            credits[ds] += amt
        for ds, amt, cat, etype, p in expense_flows:
            if (cat, etype) in stopped:
                continue
            amt2 = amt
            if (cat, etype) in reduced and p["freq"] == "monthly":
                amt2 = max(0.0, min(amt, reduced[(cat, etype)]))
            debits[ds] += amt2
        for ds, delta in prep["scheduled"].items():
            if delta < 0:
                # a scheduled debit of a fixed-price category is part of an
                # existing fixed obligation — never remove it via stop/reduce
                debits[ds] += -delta
            else:
                credits[ds] += delta

        # date-window discipline: ignore flows dated before the request date
        balance = user.current_balance
        min_obs = balance
        breach = None

        for i in range(horizon_days):
            ds = _fmt_date(start_dt + timedelta(days=i))
            balance += credits.get(ds, 0.0)
            balance -= debits.get(ds, 0.0)
            if ds in payments_by_date:
                balance -= payments_by_date[ds]
            if balance < min_obs:
                min_obs = balance
            if balance < user.min_balance and breach is None:
                breach = ds

        return breach is None, min_obs, breach


# ──────────────────────────────────────────────────────────────────────────────
# AMOUNT SAFE TO PAY
# ──────────────────────────────────────────────────────────────────────────────


def calc_amount_safe(user, sim, requested_amount, request_date):
    """Max amount payable today that keeps the 90-day check safe.

    Monotonic in the paid amount: settle the boundary with a binary search
    (60 iterations, 0.01 tolerance) after checking the two extremes.
    """
    safe0, _, _ = sim.simulate(user, request_date, [], [])
    if not safe0:
        return 0.0
    safe_full, _, _ = sim.simulate(
        user, request_date, [(request_date, requested_amount)], []
    )
    if safe_full:
        return requested_amount

    lo, hi = 0.0, requested_amount
    best = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if hi - lo < 0.01:
            break
        ok, _, _ = sim.simulate(user, request_date, [(request_date, mid)], [])
        if ok:
            best, lo = mid, mid
        else:
            hi = mid
    return round(best, 2)


def find_earliest_full_date(user, sim, requested_amount, request_date, horizon_days=90):
    start = _parse_date(request_date)
    for i in range(horizon_days):
        ds = _fmt_date(start + timedelta(days=i))
        ok, _, _ = sim.simulate(user, ds, [(ds, requested_amount)], [])
        if ok:
            return ds
    return None


# ──────────────────────────────────────────────────────────────────────────────
# PLAN GENERATION
# ──────────────────────────────────────────────────────────────────────────────


def _option_schedule(opt):
    """Expand a payment option into (date, amount) pairs.

    Confirmed from the sample outputs: schedules are day-based —
    first_payment_date + k * payment_frequency_days (e.g. 2026-04-19 with
    freq 31 gives 04-19, 05-20, 06-20; month-based arithmetic would give
    05-19 and would not match the reference plans).
    """
    payments = []
    d = _parse_date(opt.first_payment_date)
    for _ in range(opt.num_payments):
        payments.append((_fmt_date(d), opt.payment_amount))
        d = d + timedelta(days=opt.frequency_days or 30)
    return payments


def generate_plans(user, request, options, sim, amount_safe, earliest_full):
    candidates = []
    rd = request.request_date
    ra = request.requested_amount

    # A. full payment today (no spending changes)
    if "full_payment" in user.payment_methods:
        ok, _, _ = sim.simulate(user, rd, [(rd, ra)], [])
        if ok:
            candidates.append(
                CandidatePlan("full_payment", None, [(rd, ra)], [], ra, True, rd, 1)
            )
    # B. installments (must exactly match a supplied option)
    # Schedules are day-based: first_payment_date + k * payment_frequency_days.
    if "installments" in user.payment_methods:
        for opt in options:
            if opt.method != "installments":
                continue
            if _parse_date(opt.first_payment_date) < _parse_date(rd):
                continue  # option begins before the request exists
            if user.max_installment_months is not None:
                span_days = (opt.num_payments - 1) * opt.frequency_days if opt.frequency_days else 0
                if span_days > user.max_installment_months * 31:
                    continue
            payments = _option_schedule(opt)
            last = payments[-1][0]
            completes = last <= request.desired_completion_date
            if not completes:
                continue  # an installment plan must finish by the deadline
            ok, _, _ = sim.simulate(user, rd, payments, [])
            if ok:
                candidates.append(
                    CandidatePlan(
                        "installments", opt.option_id, payments, [],
                        opt.total_payable, True, payments[0][0], opt.num_payments,
                    )
                )

    # C. partial payment (exactly two payments per the spec)
    if (
        "partial_payment" in user.payment_methods
        and request.allows_partial
        and 0 < amount_safe < ra
        and earliest_full
        and earliest_full <= request.desired_completion_date
    ):
        remaining = round(ra - amount_safe, 2)
        pmts = [(rd, amount_safe), (earliest_full, remaining)]
        ok, _, _ = sim.simulate(user, rd, pmts, [])
        if ok:
            candidates.append(
                CandidatePlan("partial_payment", None, pmts, [], ra, True, rd, 2)
            )

    # D. wait (full payment on the earliest safe future date)
    if "full_payment" in user.payment_methods and earliest_full:
        ok, _, _ = sim.simulate(user, rd, [(earliest_full, ra)], [])
        if ok:
            completes = earliest_full <= request.desired_completion_date
            candidates.append(
                CandidatePlan("wait", None, [(earliest_full, ra)], [], ra,
                              completes, earliest_full, 1)
            )

    # E. full payment with permitted spending changes
    if "full_payment" in user.payment_methods:
        prep = sim._prepare(user)
        actions = []
        # collect stoppable and reducible recurring categories
        for p in prep["recurring"]:
            cat, etype = p["cat"], p["etype"]
            cat_events = [
                (e, a) for e, a in prep["events"]
                if e.category == cat and e.event_type == etype
                and e.flexibility in ("stoppable", "reducible", "reducible_or_stoppable")
                and e.status == "settled"
            ]
            if not cat_events:
                continue
            latest = max(cat_events, key=lambda x: x[0].event_date)[0]
            if cat in user.stoppable_categories and etype not in {a[1] for a in actions}:
                actions.append(("stop", latest.event_id, None))
            if (
                cat in user.reducible_categories
                and latest.minimum_allowed_amount is not None
                and latest.minimum_allowed_amount < p["typical"]
            ):
                actions.append(("reduce_to", latest.event_id, latest.minimum_allowed_amount))

        # single changes first, then pairs (<=3 allowed by spec; mutual
        # exclusivity handled by using distinct event ids)
        singles = [[a] for a in actions]
        pairs = [
            [a1, a2]
            for i, a1 in enumerate(actions)
            for a2 in actions[i + 1:]
            if a2[1] != a1[1]  # stop and reduce must reference different events
        ]
        for sc in singles + pairs:
            ok, _, _ = sim.simulate(user, rd, [(rd, ra)], sc)
            if ok:
                candidates.append(
                    CandidatePlan("full_payment", None, [(rd, ra)], sc, ra, True, rd, 1)
                )

    return candidates


# ──────────────────────────────────────────────────────────────────────────────
# RANKING (exact spec order)
# ──────────────────────────────────────────────────────────────────────────────


def rank_plans(candidates):
    """Spec order: complete by deadline, fewer changes, cheaper total,
    earlier start, fewer payments. Deadline feasibility is per-plan."""
    return sorted(
        candidates,
        key=lambda p: (
            0 if p.completes_by_deadline else 1,
            len(p.spending_changes),
            round(p.total_cost, 2),
            p.earliest_payment_date,
            p.num_payments,
            p.payment_option_id or "zzzz",
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
# EXPLANATION GENERATION
# ──────────────────────────────────────────────────────────────────────────────


def _cat_of(sim, eid):
    ev = sim.event_map.get(eid)
    return ev.category if ev else "expense"


def gen_explanation(plan, req, user, amount_safe, earliest_full, sim):
    c = user.home_currency
    ra = req.requested_amount

    if plan is None or plan.method == "not_recommended":
        if amount_safe > 0 and not earliest_full:
            return (
                f"Do not proceed with the {c} {_money(ra)} request. Although "
                f"{c} {_money(amount_safe)} is available today, the full "
                f"amount cannot be completed safely within the 90-day "
                f"forecast while protecting the {c} {_money(user.min_balance)} "
                f"minimum."
            )
        return (
            f"Do not proceed with the {c} {_money(ra)} request by "
            f"{req.desired_completion_date}. None of the available options "
            f"keeps the {c} {_money(user.min_balance)} minimum protected."
        )

    if plan.method == "full_payment" and not plan.spending_changes:
        if amount_safe >= ra:
            return (
                f"Pay {c} {_money(ra)} today. This leaves at least "
                f"{c} {_money(user.min_balance)} available over the next "
                f"90 days."
            )
        return (
            f"Pay {c} {_money(ra)} on {plan.earliest_payment_date}. Paying "
            f"earlier would take the balance below the {c} "
            f"{_money(user.min_balance)} minimum."
        )

    if plan.method == "full_payment" and plan.spending_changes:
        parts = []
        for kind, eid, amt in plan.spending_changes:
            if kind == "stop":
                parts.append(f"stop the {_cat_of(sim, eid)} expense ({eid})")
            else:
                parts.append(
                    f"reduce the {_cat_of(sim, eid)} expense ({eid}) to "
                    f"{c} {_money(amt)}"
                )
        return (
            f"{'; '.join(parts).capitalize()}, then pay {c} {_money(ra)} "
            f"today. This leaves at least {c} {_money(user.min_balance)} "
            f"available over the next 90 days."
        )

    if plan.method == "installments":
        n = plan.num_payments
        amt = plan.payments[0][1]
        start = plan.payments[0][0]
        return (
            f"Use {n} installments of {c} {_money(amt)}, starting {start}. "
            f"This leaves at least {c} {_money(user.min_balance)} available."
        )

    if plan.method == "partial_payment":
        first, second = plan.payments
        return (
            f"Pay {c} {_money(first[1])} today and the remaining {c} "
            f"{_money(second[1])} on {second[0]}. This completes the full "
            f"request and keeps the {c} {_money(user.min_balance)} minimum "
            f"protected."
        )

    if plan.method == "wait":
        return (
            f"Pay {c} {_money(ra)} in full on {plan.earliest_payment_date}. "
            f"Paying earlier would take the balance below the {c} "
            f"{_money(user.min_balance)} minimum."
        )

    return (
        f"Do not proceed. None of the available options keeps the {c} "
        f"{_money(user.min_balance)} minimum protected."
    )


# ──────────────────────────────────────────────────────────────────────────────
# REQUEST PROCESSING
# ──────────────────────────────────────────────────────────────────────────────


def process_request(req, data, sim):
    user = data["profiles"].get(req.user_id)
    if not user:
        return {
            "request_id": req.request_id,
            "amount_safe_to_pay": "0.00",
            "affordability_status": "not_affordable",
            "recommended_payment_method": "not_recommended",
            "payment_plan": "none",
            "earliest_date_for_full_payment": "",
            "spending_changes_needed": "none",
            "decision_explanation": "User profile not found.",
        }

    amount_safe = calc_amount_safe(user, sim, req.requested_amount, req.request_date)
    earliest_full = find_earliest_full_date(
        user, sim, req.requested_amount, req.request_date
    )
    options = data["options_by_request"].get(req.request_id, [])

    candidates = generate_plans(user, req, options, sim, amount_safe, earliest_full)
    ranked = rank_plans(candidates)
    best = ranked[0] if ranked else None

    # derive output fields
    # earliest_date_for_full_payment is plan-independent: the first date one
    # safe full payment is possible with no spending changes (sample-verified,
    # e.g. request_06 earliest 2026-01-15 despite a same-day stop plan).
    if best is None:
        if (
            earliest_full
            and "full_payment" in user.payment_methods
            and earliest_full <= req.desired_completion_date
        ):
            status = "affordable_later"
            method = "wait"
            payments = [(earliest_full, req.requested_amount)]
            spending = []
        else:
            status = "not_affordable"
            method = "not_recommended"
            payments = []
            spending = []
    else:
        method = best.method
        payments = best.payments
        spending = best.spending_changes
        if method == "full_payment" and not spending:
            if best.earliest_payment_date == req.request_date:
                status = "affordable_now"
            else:
                status = "affordable_later"
        elif method == "wait":
            status = "affordable_later"
        else:
            status = "affordable_with_plan"
    efpd = earliest_full or ""

    plan_str = (
        "|".join(f"{d}:{_fmt_amt(a)}" for d, a in payments) if payments else "none"
    )
    sc_str = "none"
    if spending:
        sc_str = "|".join(
            f"stop:{eid}" if k == "stop" else f"reduce_to:{eid}:{_fmt_amt(a)}"
            for k, eid, a in spending
        )

    explanation = gen_explanation(best, req, user, amount_safe, earliest_full, sim)

    return {
        "request_id": req.request_id,
        "amount_safe_to_pay": f"{amount_safe:.2f}",
        "affordability_status": status,
        "recommended_payment_method": method,
        "payment_plan": plan_str,
        "earliest_date_for_full_payment": efpd,
        "spending_changes_needed": sc_str,
        "decision_explanation": explanation,
    }


# ──────────────────────────────────────────────────────────────────────────────
# OUTPUT VERIFICATION
# ──────────────────────────────────────────────────────────────────────────────


def verify_output(rows, data):
    errors = []
    if len(rows) != len(data["requests"]):
        errors.append(
            f"Row count: got {len(rows)}, expected {len(data['requests'])}"
        )
    valid_status = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
    valid_methods = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}
    req_by_id = {r.request_id: r for r in data["requests"]}

    for row in rows:
        rid = row.get("request_id")
        req = req_by_id.get(rid)
        if row.get("affordability_status") not in valid_status:
            errors.append(f"Invalid status in {rid}")
        if row.get("recommended_payment_method") not in valid_methods:
            errors.append(f"Invalid method in {rid}")
        try:
            amt = float(row.get("amount_safe_to_pay", 0))
            ra = req.requested_amount if req else 0
            if not (0 <= amt <= ra + 1e-9):
                errors.append(f"Bounds violated in {rid}: {amt} vs {ra}")
        except (ValueError, TypeError):
            errors.append(f"Invalid amount in {rid}")

        # partial-payment invariants
        if row.get("recommended_payment_method") == "partial_payment":
            parts = row.get("payment_plan", "").split("|")
            if len(parts) != 2:
                errors.append(f"Partial plan must have 2 payments in {rid}")
            else:
                try:
                    a = float(parts[0].split(":")[1])
                    b = float(parts[1].split(":")[1])
                    if abs(a + b - req.requested_amount) > 0.02 or abs(a - amt) > 0.02:
                        errors.append(f"Partial sums wrong in {rid}")
                except (ValueError, IndexError):
                    errors.append(f"Partial plan malformed in {rid}")

        # installment plans must match a supplied option
        if row.get("recommended_payment_method") == "installments" and req:
            opts = data["options_by_request"].get(rid, [])
            ok = False
            for o in opts:
                d = _parse_date(o.first_payment_date)
                sched = []
                for _ in range(o.num_payments):
                    sched.append((_fmt_date(d), o.payment_amount))
                    d = d + timedelta(days=o.frequency_days or 30)
                plan_pairs = [
                    (p.split(":")[0], float(p.split(":")[1]))
                    for p in row.get("payment_plan", "").split("|") if ":" in p
                ]
                if plan_pairs and all(
                    abs(a[1] - b[1]) < 0.01 and a[0] == b[0]
                    for a, b in zip(plan_pairs, sched)
                ) and len(plan_pairs) == len(sched):
                    ok = True
                    break
            if not ok:
                errors.append(f"Installment plan does not match an option in {rid}")

        # spending changes must reference flexible events, different ids
        sc = row.get("spending_changes_needed", "none")
        if sc not in ("", "none"):
            eids = []
            for part in sc.split("|"):
                if part.startswith("stop:"):
                    eids.append(part.split(":")[1])
                elif part.startswith("reduce_to:"):
                    bits = part.split(":")
                    eids.append(bits[1])
                    if len(bits) < 3:
                        errors.append(f"reduce_to missing amount in {rid}")
            if len(set(eids)) != len(eids):
                errors.append(f"Duplicate event in spending changes in {rid}")
            if len(eids) > 3:
                errors.append(f"Too many spending changes in {rid}")
    return errors


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────


def run_pipeline(dataset_dir, output_path):
    print("Loading data...")
    data = load_all_data(dataset_dir)

    print("Initializing simulator (FX, OCR amounts, recurrence)...")
    sim = Simulator(data)

    print(f"Processing {len(data['requests'])} requests...")
    rows = []
    for i, req in enumerate(data["requests"]):
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(data['requests'])}...")
        rows.append(process_request(req, data, sim))

    print("Verifying...")
    errors = verify_output(rows, data)
    for e in errors[:10]:
        print(f"  - {e}")
    if errors:
        print(f"  {len(errors)} verification issues found")

    print(f"Writing {len(rows)} rows to {output_path}")
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "request_id",
                "amount_safe_to_pay",
                "affordability_status",
                "recommended_payment_method",
                "payment_plan",
                "earliest_date_for_full_payment",
                "spending_changes_needed",
                "decision_explanation",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print("Done!")
    return rows


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    run_pipeline(
        os.path.join(project_root, "dataset"),
        os.path.join(project_root, "output.csv"),
    )
