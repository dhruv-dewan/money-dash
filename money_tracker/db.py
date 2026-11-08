"""SQLite schema and atomic persistence. Source facts are immutable."""
import json
import os
import sqlite3
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
INSERT OR IGNORE INTO state VALUES(1,0);
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('checking','card')));
CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), filename TEXT NOT NULL, hash TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, row_count INTEGER NOT NULL, min_date TEXT NOT NULL, max_date TEXT NOT NULL, UNIQUE(account_id,hash));
CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), transaction_date TEXT, posting_date TEXT NOT NULL, cents INTEGER NOT NULL, description TEXT NOT NULL, type TEXT NOT NULL, category TEXT NOT NULL, memo TEXT NOT NULL, metadata TEXT NOT NULL, fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL, suppressed INTEGER NOT NULL DEFAULT 0 CHECK(suppressed IN (0,1)), UNIQUE(account_id,fingerprint,occurrence));
CREATE TABLE IF NOT EXISTS import_rows(batch_id INTEGER NOT NULL REFERENCES batches(id), row_number INTEGER NOT NULL, transaction_id INTEGER NOT NULL REFERENCES transactions(id), facts TEXT NOT NULL, PRIMARY KEY(batch_id,row_number));
CREATE TABLE IF NOT EXISTS allocations(transaction_id INTEGER NOT NULL REFERENCES transactions(id), ordinal INTEGER NOT NULL, cents INTEGER NOT NULL, purpose TEXT NOT NULL, category TEXT NOT NULL, note TEXT NOT NULL, one_off INTEGER NOT NULL, budget_month TEXT, PRIMARY KEY(transaction_id,ordinal));
CREATE TRIGGER IF NOT EXISTS immutable_source BEFORE UPDATE OF account_id,transaction_date,posting_date,cents,description,type,category,memo,metadata,fingerprint,occurrence ON transactions BEGIN SELECT RAISE(ABORT,'source facts are immutable'); END;
'''


def connect(path=None):
    path = str(path or os.environ.get('MONEY_TRACKER_DB', 'data/money-tracker.sqlite3'))
    if path != ':memory:':
        path = str(Path(path).expanduser())
        Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1):
        raise ValueError('Unsupported database schema version')
    if version == 0:
        conn.executescript(SCHEMA)
        conn.execute('PRAGMA user_version=1')
        conn.commit()
    return conn


def query(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def revision(conn):
    return conn.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]


def touch(conn):
    conn.execute('UPDATE state SET revision=revision+1 WHERE id=1')


def create_account(conn, name, kind):
    if not name.strip() or kind not in ('checking', 'card'):
        raise ValueError('Provide a name and checking/card kind')
    with conn:
        result = conn.execute('INSERT INTO accounts(name,kind) VALUES(?,?)', (name.strip(), kind))
        touch(conn)
    return result.lastrowid


def transaction(conn, transaction_id):
    rows = query(conn, 'SELECT t.*, a.name account, a.kind FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE t.id=?', (transaction_id,))
    if not rows:
        raise ValueError('Transaction not found')
    return rows[0]


def save_allocations(conn, transaction_id, allocations):
    from .ledger import validate
    source = transaction(conn, transaction_id)
    validate(source['cents'], allocations)
    with conn:
        conn.execute('DELETE FROM allocations WHERE transaction_id=?', (transaction_id,))
        conn.executemany('INSERT INTO allocations VALUES(?,?,?,?,?,?,?,?)',
                         [(transaction_id, i, a['cents'], a['purpose'], a['category'], a['note'], int(a['one_off']), a['budget_month'] or None) for i, a in enumerate(allocations)])
        touch(conn)


def reset(conn, transaction_id):
    with conn:
        conn.execute('DELETE FROM allocations WHERE transaction_id=?', (transaction_id,))
        touch(conn)


def suppress(conn, transaction_id, value):
    transaction(conn, transaction_id)
    with conn:
        conn.execute('UPDATE transactions SET suppressed=? WHERE id=?', (int(value), transaction_id))
        touch(conn)


def insert_source(conn, account_id, row):
    occurrence = conn.execute('SELECT COALESCE(MAX(occurrence),0)+1 FROM transactions WHERE account_id=? AND fingerprint=?', (account_id, row['fingerprint'])).fetchone()[0]
    fields = ['transaction_date','posting_date','cents','description','type','category','memo']
    return conn.execute('INSERT INTO transactions(account_id,transaction_date,posting_date,cents,description,type,category,memo,metadata,fingerprint,occurrence) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        [account_id] + [row[k] for k in fields] + [json.dumps(row['metadata']), row['fingerprint'], occurrence]).lastrowid


def separate(conn, batch_id, row_number):
    """Correct a reused provenance row without changing existing facts/edits."""
    with conn:
        row = conn.execute('SELECT r.*, b.account_id FROM import_rows r JOIN batches b ON b.id=r.batch_id WHERE batch_id=? AND row_number=?', (batch_id, row_number)).fetchone()
        if not row:
            raise ValueError('Import row not found')
        count = conn.execute('SELECT count(*) FROM import_rows WHERE transaction_id=?', (row['transaction_id'],)).fetchone()[0]
        if count < 2:
            raise ValueError('This source has no reused import row to separate')
        new_id = insert_source(conn, row['account_id'], json.loads(row['facts']))
        conn.execute('UPDATE import_rows SET transaction_id=? WHERE batch_id=? AND row_number=?', (new_id, batch_id, row_number))
        touch(conn)
    return new_id
