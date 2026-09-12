#!/usr/bin/env python3
"""
Evaluation/Regression Harness v2
"""
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.main import load_all_data, Simulator, process_request, Request

def load_sample_expected(dataset_dir):
    rows = []
    with open(os.path.join(dataset_dir, 'sample_requests.csv'), 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows

def evaluate_row(predicted, expected):
    results = {'request_id': expected.get('request_id', ''), 'fields': {}, 'all_match': True}
    
    for field in ['affordability_status', 'recommended_payment_method']:
        pv = predicted.get(field, '')
        ev = expected.get(field, '')
        m = pv == ev
        results['fields'][field] = {'predicted': pv, 'expected': ev, 'match': m}
        if not m: results['all_match'] = False
    
    for field in ['earliest_date_for_full_payment']:
        pv = predicted.get(field, '').strip()
        ev = expected.get(field, '').strip()
        m = pv == ev
        results['fields'][field] = {'predicted': pv, 'expected': ev, 'match': m}
        if not m: results['all_match'] = False
    
    for field in ['amount_safe_to_pay']:
        try:
            pv = float(predicted.get(field, 0))
            ev = float(expected.get(field, 0))
            ae = abs(pv - ev)
            re = ae / max(ev, 0.01)
            m = re < 0.01 or ae < 1.0
            results['fields'][field] = {'predicted': pv, 'expected': ev, 'abs_error': ae, 'rel_error': re, 'match': m}
            if not m: results['all_match'] = False
        except:
            results['fields'][field] = {'predicted': predicted.get(field, ''), 'expected': expected.get(field, ''), 'match': False}
            results['all_match'] = False
    
    for field in ['payment_plan', 'spending_changes_needed']:
        pv = predicted.get(field, '').strip()
        ev = expected.get(field, '').strip()
        m = pv == ev
        results['fields'][field] = {'predicted': pv, 'expected': ev, 'match': m}
        if not m: results['all_match'] = False
    
    return results

def run_evaluation(dataset_dir=None):
    if dataset_dir is None:
        dataset_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dataset')
    
    print("=" * 70)
    print("EVALUATION HARNESS - Buy or Wait?")
    print("=" * 70)
    
    data = load_all_data(dataset_dir)
    sim = Simulator(data)
    expected_rows = load_sample_expected(dataset_dir)
    print(f"Loaded {len(expected_rows)} sample requests")
    
    correct = {'status': 0, 'method': 0, 'amount': 0, 'date': 0, 'plan': 0, 'spending': 0}
    results = []
    
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
        result = evaluate_row(predicted, exp)
        results.append(result)
        
        if result['fields'].get('affordability_status', {}).get('match'): correct['status'] += 1
        if result['fields'].get('recommended_payment_method', {}).get('match'): correct['method'] += 1
        if result['fields'].get('amount_safe_to_pay', {}).get('match'): correct['amount'] += 1
        if result['fields'].get('earliest_date_for_full_payment', {}).get('match'): correct['date'] += 1
        if result['fields'].get('payment_plan', {}).get('match'): correct['plan'] += 1
        if result['fields'].get('spending_changes_needed', {}).get('match'): correct['spending'] += 1
    
    total = len(results)
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    
    metrics = [
        ('affordability_status', correct['status']),
        ('recommended_payment_method', correct['method']),
        ('amount_safe_to_pay', correct['amount']),
        ('earliest_date_for_full_payment', correct['date']),
        ('payment_plan', correct['plan']),
        ('spending_changes_needed', correct['spending']),
    ]
    
    for name, c in metrics:
        pct = (c / total * 100) if total > 0 else 0
        bar = '#' * int(pct / 5) + '.' * (20 - int(pct / 5))
        print(f"  {name:40s} {c:3d}/{total:3d} ({pct:5.1f}%) {bar}")
    
    print("\n" + "=" * 70)
    print("FAILED REQUESTS")
    print("=" * 70)
    
    failures = [r for r in results if not r['all_match']]
    for r in failures:
        print(f"\n--- {r['request_id']} ---")
        for field, info in r['fields'].items():
            if not info.get('match', True):
                print(f"  {field}:")
                print(f"    predicted: {info.get('predicted', 'N/A')}")
                print(f"    expected:  {info.get('expected', 'N/A')}")
                if 'abs_error' in info:
                    print(f"    error: {info['abs_error']:.2f} (rel: {info['rel_error']:.4f})")
    
    overall = sum(1 for r in results if r['all_match']) / total * 100 if total > 0 else 0
    print(f"\n{'=' * 70}")
    print(f"OVERALL EXACT MATCH: {sum(1 for r in results if r['all_match'])}/{total} ({overall:.1f}%)")
    print(f"{'=' * 70}")
    
    return results

if __name__ == '__main__':
    run_evaluation()
