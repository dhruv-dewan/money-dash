"""Strict Chase CSV parsing, independent of database and UI."""
import csv
import io
import json
import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext

CHECKING = {'Details', 'Posting Date', 'Description', 'Amount', 'Type', 'Balance', 'Check or Slip #'}
CARD = {'Transaction Date', 'Post Date', 'Description', 'Category', 'Type', 'Amount', 'Memo'}
MAX_CENTS = 2**63 - 1


def parse(data: bytes, filename='upload.csv'):
    reader = None
    try:
        reader = csv.reader(io.StringIO(data.decode('utf-8-sig')), strict=True)
        headers = [h.strip() for h in next(reader)]
        if len(set(headers)) != len(headers) or '' in headers:
            raise ValueError('duplicate or empty headers')
        fields = set(headers)
        if CHECKING <= fields:
            kind = 'checking'
        elif CARD <= fields:
            kind = 'card'
        else:
            raise ValueError('missing required Chase headers')
        rows = []
        for record in reader:
            line = reader.line_num
            if not record or all(not x.strip() for x in record):
                continue
            if kind == 'checking' and len(record) == len(headers) + 1 and record[-1] == '':
                record.pop()
            if len(record) != len(headers):
                raise ValueError(f'row {line}: unexpected field count')
            raw = dict(zip(headers, record))
            try:
                def date(key):
                    value = raw[key].strip()
                    if not re.fullmatch(r'\d{2}/\d{2}/\d{4}', value):
                        raise ValueError(f'invalid {key}')
                    return datetime.strptime(value, '%m/%d/%Y').date().isoformat()
                posted = date('Posting Date' if kind == 'checking' else 'Post Date')
                transacted = date('Transaction Date') if kind == 'card' else None
                amount_text = raw['Amount'].strip()
                if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', amount_text):
                    raise ValueError('invalid amount')
                with localcontext() as context:
                    context.prec = max(28, len(amount_text) + 4)
                    amount = Decimal(amount_text) * 100
                if not amount.is_finite() or amount != amount.to_integral_value() or abs(amount) > MAX_CENTS:
                    raise ValueError('amount must be finite, exact cents within SQLite range')
                cents = int(amount)
                normalized = ' '.join(raw['Description'].split())
                fingerprint = hashlib.sha256(json.dumps([transacted, posted, cents, raw['Type'].strip(), normalized]).encode()).hexdigest()
                rows.append(dict(row_number=line, transaction_date=transacted, posting_date=posted,
                                 cents=cents, description=raw['Description'], type=raw['Type'],
                                 category=raw.get('Category', ''), memo=raw.get('Memo', ''),
                                 metadata=raw, fingerprint=fingerprint))
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f'row {line}: {exc}') from exc
        if not rows:
            raise ValueError('no transaction rows')
        return kind, rows
    except (ValueError, UnicodeError, csv.Error, StopIteration) as exc:
        raise ValueError(f'{filename}, row {reader.line_num if reader else 1}: {exc}') from exc
