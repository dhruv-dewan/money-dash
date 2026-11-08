"""Conservative classification, split validation and shared report selection."""
import re
from datetime import date
from . import db

PURPOSES = ['Payroll', 'Other income', 'Expense', 'Refund/reimbursement', 'Standalone credit', 'Transfer', 'Deposit/pass-through/other exclusion', 'Needs review']
EXCLUSION = PURPOSES[6]


def default(source):
    desc, kind, typ, cents = source['description'].upper(), source['kind'], source['type'].strip().upper(), source['cents']
    purpose, one_off = 'Needs review', False
    if (kind == 'card' and typ == 'PAYMENT') or 'CHASE CREDIT CRD AUTOPAY' in desc or 'PAYMENT TO CHASE CARD ENDING IN' in desc:
        purpose = 'Transfer'
    elif cents < 0 and ((kind == 'card' and typ in ('SALE', 'FEE')) or (kind == 'checking' and (typ in ('DEBIT_CARD', 'DEBIT CARD', 'FEE') or 'MONTHLY SERVICE FEE' in desc))):
        purpose = 'Expense'
    elif kind == 'card' and cents > 0 and typ == 'RETURN':
        purpose = 'Refund/reimbursement'
    elif kind == 'checking' and cents > 0 and 'STRIPE' in desc and 'PAYROLL' in desc:
        # An explicit large-credit review item; user can confirm payroll in editor.
        purpose = 'Payroll' if cents <= 1_000_000 else 'Needs review'
    elif cents > 0 and ('STATEMENT CREDIT' in desc or 'CASH BACK BONUS' in desc):
        purpose, one_off = 'Standalone credit', True
    return dict(cents=cents, purpose=purpose, category=source['category'] or 'Uncategorized', note='', one_off=one_off, budget_month=None)


def validate(cents, allocations):
    if not allocations:
        raise ValueError('At least one allocation is required')
    if cents == 0 and (len(allocations) != 1 or allocations[0]['cents'] != 0):
        raise ValueError('Zero sources require one zero allocation')
    for a in allocations:
        n = a['cents']
        if type(n) is not int or abs(n) > 2**63-1 or (n and (n > 0) != (cents > 0)):
            raise ValueError('Use integer cents with the original sign')
        p = a['purpose']
        if p not in PURPOSES:
            raise ValueError('Unknown purpose')
        if n and ((p in ('Payroll','Other income','Refund/reimbursement','Standalone credit') and n < 0) or (p == 'Expense' and n > 0)):
            raise ValueError('Purpose is incompatible with amount sign')
        month = a['budget_month']
        if month and not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month):
            raise ValueError('Budget month must be YYYY-MM')
        if month and month.startswith('0000'):
            raise ValueError('Budget year must be positive')
        if p == EXCLUSION and not a['note'].strip():
            raise ValueError('An exclusion needs a note')
        if not isinstance(a['category'], str) or not isinstance(a['note'], str):
            raise ValueError('Category and note must be text')
    if sum(a['cents'] for a in allocations) != cents:
        raise ValueError('Allocations must sum exactly to the source amount')


def allocations(conn, source):
    return db.query(conn, 'SELECT cents,purpose,category,note,one_off,budget_month FROM allocations WHERE transaction_id=? ORDER BY ordinal', (source['id'],)) or [default(source)]


def sources(conn):
    return db.query(conn, 'SELECT t.*,a.name account,a.kind FROM transactions t JOIN accounts a ON a.id=t.account_id ORDER BY COALESCE(transaction_date,posting_date),t.id')


def entries(conn):
    result = []
    for s in sources(conn):
        actual = (s['transaction_date'] or s['posting_date'])[:7]
        for a in allocations(conn, s):
            result.append(dict(a, transaction_id=s['id'], account=s['account'], kind=s['kind'], description=s['description'], source_date=s['transaction_date'] or s['posting_date'], posting_date=s['posting_date'], source_cents=s['cents'], actual_month=actual, effective_month=a['budget_month'] or actual, suppressed=s['suppressed']))
    return result


def months(conn):
    found = {'2026-07', date.today().strftime('%Y-%m')}
    for a in entries(conn):
        found.update([a['actual_month'], a['effective_month'], a['posting_date'][:7]])
    return sorted(m for m in found if m >= '2026-07')


def report(conn, month, typical=False):
    selected = [a for a in entries(conn) if not a['suppressed'] and (a['effective_month'] if typical else a['actual_month']) == month and not (typical and a['one_off'])]
    totals = dict(payroll=0, other_income=0, credits=0, spending=0, review_in=0, review_out=0)
    categories = {}
    for a in selected:
        n, p = a['cents'], a['purpose']
        key = {'Payroll':'payroll', 'Other income':'other_income', 'Standalone credit':'credits'}.get(p)
        if key:
            totals[key] += n
        elif p in ('Expense', 'Refund/reimbursement'):
            totals['spending'] -= n
            categories[a['category']] = categories.get(a['category'], 0) - n
        elif p == 'Needs review':
            totals['review_in' if n >= 0 else 'review_out'] += abs(n)
    totals['net'] = totals['payroll'] + totals['other_income'] + totals['credits'] - totals['spending']
    totals['provisional'] = any(a['purpose'] == 'Needs review' for a in selected)
    return dict(totals=totals, categories=categories, rows=selected)


def checking(conn, month):
    rows = [s for s in sources(conn) if s['kind'] == 'checking' and not s['suppressed'] and s['posting_date'][:7] == month]
    return dict(inflow=sum(max(0,s['cents']) for s in rows), outflow=-sum(min(0,s['cents']) for s in rows), net=sum(s['cents'] for s in rows), rows=rows)
