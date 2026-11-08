"""Read-only previews, occurrence-count deduplication, atomic import commits."""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
from . import chase, db, ledger


def annotation(conn, source):
    return json.dumps([source['suppressed'], ledger.allocations(conn, source)], sort_keys=True)


def preview(conn, account_id, data, filename):
    kind, rows = chase.parse(data, filename)
    account = conn.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
    if not account or account['kind'] != kind:
        raise ValueError('CSV format does not match selected account kind')
    digest = hashlib.sha256(data).hexdigest()
    previous = db.query(conn, 'SELECT * FROM batches WHERE hash=?', (digest,))
    groups = defaultdict(list)
    for row in rows:
        groups[row['fingerprint']].append(row)
    review, mapping = [], {}
    for fp, incoming in groups.items():
        existing = db.query(conn, 'SELECT t.*,a.kind FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE account_id=? AND fingerprint=? ORDER BY occurrence', (account_id, fp))
        used = set()
        ambiguous = []
        for row in incoming:
            available = [s for s in existing if s['id'] not in used]
            # All provenance copies are evidence, including metadata from later files.
            matching = [s for s in available if any(json.loads(p['facts'])['metadata'] == row['metadata'] for p in db.query(conn, 'SELECT facts FROM import_rows WHERE transaction_id=?', (s['id'],)))]
            candidates = matching or available
            target = candidates[0]['id'] if candidates else None
            if len({annotation(conn, s) for s in candidates}) > 1:
                ambiguous.append(row['row_number'])
            mapping[row['row_number']] = target
            if target:
                used.add(target)
        if existing or len(incoming) > 1:
            review.append(dict(fingerprint=fp, incoming=incoming, existing=[dict(s, allocations=ledger.allocations(conn,s)) for s in existing], required_rows=ambiguous))
    return dict(account_id=account_id, filename=Path(filename).name, hash=digest, rows=rows,
                revision=db.revision(conn), mapping=mapping, groups=review,
                already=any(b['account_id']==account_id for b in previous),
                cross_account=any(b['account_id']!=account_id for b in previous))


def commit(conn, plan, decisions=None, confirm_cross_account=False):
    decisions = decisions or {}
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if db.revision(conn) != plan['revision']:
            raise ValueError('Preview is stale; preview the file again')
        if plan['already']:
            return dict(added=0, reused=0, already=True)
        if plan['cross_account'] and not confirm_cross_account:
            raise ValueError('File hash already used on another account; confirm account selection')
        required = {n for g in plan['groups'] for n in g['required_rows']}
        if not required <= decisions.keys():
            raise ValueError('Explicit occurrence mapping is required for differently annotated matches')
        mapping = dict(plan['mapping'])
        mapping.update(decisions)
        if not decisions.keys() <= {r['row_number'] for r in plan['rows']}:
            raise ValueError('Unknown import row decision')
        used = set()
        for row in plan['rows']:
            target = mapping[row['row_number']]
            if target is not None:
                existing = db.transaction(conn, target)
                if target in used or existing['account_id'] != plan['account_id'] or existing['fingerprint'] != row['fingerprint']:
                    raise ValueError('Mapping must use each matching account occurrence at most once')
                used.add(target)
        dates = [r['transaction_date'] or r['posting_date'] for r in plan['rows']]
        batch = conn.execute('INSERT INTO batches(account_id,filename,hash,row_count,min_date,max_date) VALUES(?,?,?,?,?,?)', (plan['account_id'],plan['filename'],plan['hash'],len(dates),min(dates),max(dates))).lastrowid
        added = reused = 0
        for row in plan['rows']:
            target = mapping[row['row_number']]
            if target is None:
                target = db.insert_source(conn, plan['account_id'], row)
                added += 1
            else:
                reused += 1
            conn.execute('INSERT INTO import_rows VALUES(?,?,?,?)', (batch,row['row_number'],target,json.dumps(row)))
        db.touch(conn)
        return dict(added=added, reused=reused, already=False)
