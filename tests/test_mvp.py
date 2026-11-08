"""Only synthetic transactions; no personal exports."""
import csv
import io
import json
import sqlite3
import pytest
from money_tracker import chase, db, imports, ledger


def csv_bytes(kind='checking', rows=None, bom=False, trailing=False):
    headers = ['Details','Posting Date','Description','Amount','Type','Balance','Check or Slip #'] if kind=='checking' else ['Transaction Date','Post Date','Description','Category','Type','Amount','Memo']
    rows = rows or ([['DEBIT','08/26/2026','Synthetic housing','-8000.00','ACH','','']] if kind=='checking' else [['08/31/2026','09/02/2026','Synthetic cafe','Food','Sale','-10.00','']])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    writer.writerows([r+[''] if trailing else r for r in rows])
    return (('\ufeff' if bom else '')+out.getvalue()).encode()


@pytest.fixture
def conn():
    c = db.connect(':memory:')
    db.create_account(c,'Checking','checking')
    db.create_account(c,'Card one','card')
    db.create_account(c,'Card two','card')
    yield c
    c.close()


def load(c,data=None,account=1,name='synthetic.csv',**kwargs):
    return imports.commit(c,imports.preview(c,account,data or csv_bytes(),name),**kwargs)


def allocation(cents,purpose='Expense',month=None,one_off=False,note='',category='Housing'):
    return dict(cents=cents,purpose=purpose,budget_month=month,one_off=one_off,note=note,category=category)


def test_housing_persistence_and_reimport(tmp_path):
    path = tmp_path/'ledger.sqlite3'
    c = db.connect(path)
    db.create_account(c,'Checking','checking')
    load(c)
    split = [allocation(-300000,month='2026-09'),allocation(-250000,ledger.EXCLUSION,note='Refundable deposit'),allocation(-250000,ledger.EXCLUSION,note='Roommate advance')]
    db.save_allocations(c,1,split)
    repayment = csv_bytes(rows=[['CREDIT','09/12/2026','Synthetic roommate repayment','2500','ACH','','']])
    load(c,repayment)
    db.save_allocations(c,2,[allocation(250000,ledger.EXCLUSION,note='Roommate repayment')])
    assert ledger.checking(c,'2026-08')['outflow'] == 800000
    assert ledger.report(c,'2026-08')['totals']['spending'] == 300000
    assert ledger.report(c,'2026-08',True)['totals']['spending'] == 0
    assert ledger.report(c,'2026-09',True)['totals']['spending'] == 300000
    assert ledger.report(c,'2026-09')['totals']['spending'] == 0
    assert ledger.checking(c,'2026-09')['inflow'] == 250000
    assert load(c)['already']
    # Different bytes, same fingerprint: reuse while retaining split.
    assert load(c,csv_bytes(bom=True))['reused'] == 1
    c.close()
    c = db.connect(path)
    assert ledger.allocations(c,db.transaction(c,1)) == [dict(a,one_off=int(a['one_off'])) for a in split]
    assert ledger.report(c,'2026-09',True)['totals']['spending'] == 300000
    c.close()


@pytest.mark.parametrize('kind',['checking','card'])
def test_schemas_bom_and_reordering(kind):
    data = csv_bytes(kind,bom=True,trailing=kind=='checking')
    found, rows = chase.parse(data)
    assert found==kind and len(rows)==1
    assert rows[0]['cents']==(-800000 if kind=='checking' else -1000)
    source = list(csv.reader(io.StringIO(csv_bytes(kind).decode())))
    out = io.StringIO(); w=csv.writer(out)
    w.writerows([list(reversed(r)) for r in source])
    assert chase.parse(out.getvalue().encode())[1][0]['cents']==rows[0]['cents']


@pytest.mark.parametrize('amount',['NaN','Infinity','1.001','92233720368547758.08','garbage','1e3','1.000000000000000000000000000000000000000000001'])
def test_bad_amount_rollback(conn,amount):
    load(conn)
    before = conn.total_changes
    data = csv_bytes(rows=[['DEBIT','08/26/2026','Bad',amount,'ACH','','']])
    with pytest.raises(ValueError,match='row 2'):
        load(conn,data)
    assert conn.total_changes==before


@pytest.mark.parametrize('data',[
    b'Description,Amount\nx,1\n',
    b'Amount,Amount\n1,1\n',
    csv_bytes().replace(b'08/26/2026',b'02/30/2026'),
    csv_bytes().replace(b'08/26/2026',b'8/26/2026'),
    csv_bytes()+b'bad,row\n',
    csv_bytes(trailing=True).replace(b'ACH,,,',b'ACH,,,UNHEADED'),
    b'\xff',
])
def test_invalid_csv(conn,data):
    with pytest.raises(ValueError): load(conn,data)
    assert not ledger.sources(conn)


def test_mismatch_zero_and_card_dates(conn):
    with pytest.raises(ValueError,match='kind'): load(conn,csv_bytes('card'))
    load(conn,csv_bytes('card'),2)
    assert ledger.report(conn,'2026-08')['totals']['spending']==1000
    assert ledger.report(conn,'2026-09')['totals']['spending']==0
    assert db.transaction(conn,1)['posting_date']=='2026-09-02'
    zero = csv_bytes(rows=[['CREDIT','08/01/2026','Zero','0','ADJUSTMENT','','']])
    load(conn,zero)
    db.save_allocations(conn,2,[allocation(0,'Needs review')])
    assert len(ledger.sources(conn))==2
    with pytest.raises(ValueError): db.save_allocations(conn,2,[allocation(0),allocation(0)])


@pytest.mark.parametrize('counts',[(1,3),(3,1),(2,3),(3,2)])
def test_overlap_multiplicities(conn,counts):
    row=['08/31/2026','09/02/2026','Synthetic cafe','Food','Sale','-10.00','']
    for count in counts:
        load(conn,csv_bytes('card',[row]*count),2)
    assert len(ledger.sources(conn))==max(counts)
    assert ledger.report(conn,'2026-08')['totals']['spending']==max(counts)*1000


def test_independent_accounts_and_cross_hash(conn):
    data=csv_bytes('card')
    load(conn,data,2)
    with pytest.raises(ValueError,match='another account'): load(conn,data,3)
    load(conn,data,3,confirm_cross_account=True)
    assert len(ledger.sources(conn))==2


def test_annotations_mapping_suppression_and_separation(conn):
    row=['08/31/2026','09/02/2026','Synthetic cafe','Food','Sale','-10.00','']
    load(conn,csv_bytes('card',[row]*2),2)
    db.save_allocations(conn,1,[allocation(-1000,one_off=True)])
    db.suppress(conn,2,True)
    data=csv_bytes('card',[row],bom=True)
    p=imports.preview(conn,2,data,'overlap.csv')
    assert p['groups'][0]['required_rows']==[2]
    with pytest.raises(ValueError,match='Explicit'): imports.commit(conn,p)
    imports.commit(conn,p,{2:2})
    assert db.transaction(conn,2)['suppressed']==1
    assert ledger.report(conn,'2026-08')['totals']['spending']==1000
    assert ledger.report(conn,'2026-08',True)['totals']['spending']==0
    assert load(conn,data,2)['already']
    new=db.separate(conn,2,2)
    assert new==3 and not db.transaction(conn,new)['suppressed']
    assert ledger.report(conn,'2026-08')['totals']['spending']==2000
    db.suppress(conn,2,False)
    assert ledger.report(conn,'2026-08')['totals']['spending']==3000
    # User can explicitly preserve a separate purchase even for a single match.
    p=imports.preview(conn,2,csv_bytes('card',[row],bom=False)+b'\n','distinct.csv')
    imports.commit(conn,p,{2:None})
    assert len(ledger.sources(conn))==4


def test_metadata_resolves_differing_annotations(conn):
    row=['08/31/2026','09/02/2026','Synthetic cafe','Food','Sale','-10.00','']
    other=row.copy(); other[-1]='second receipt'
    load(conn,csv_bytes('card',[row,other]),2)
    db.suppress(conn,1,True)
    p=imports.preview(conn,2,csv_bytes('card',[other]),'single.csv')
    assert not p['groups'][0]['required_rows']
    assert p['mapping'][2]==2
    imports.commit(conn,p)


def test_stale_preview_and_mapping_rollback(conn):
    p=imports.preview(conn,1,csv_bytes(),'x.csv')
    db.create_account(conn,'Other','checking')
    with pytest.raises(ValueError,match='stale'): imports.commit(conn,p)
    assert not ledger.sources(conn)
    load(conn,csv_bytes('card'),2)
    p=imports.preview(conn,1,csv_bytes(),'bad-map.csv')
    with pytest.raises(ValueError,match='Mapping'): imports.commit(conn,p,{2:1})
    assert len(ledger.sources(conn))==1


@pytest.mark.parametrize('split',[
    [allocation(-1)],
    [allocation(-800000,'Payroll')],
    [allocation(-800000,month='2026-13')],
    [allocation(-800000,ledger.EXCLUSION)],
    [allocation(-900000),allocation(100000,'Other income')],
    [],
])
def test_invalid_edit_atomic_and_reset(conn,split):
    load(conn)
    good=[allocation(-800000)]
    db.save_allocations(conn,1,good)
    with pytest.raises(ValueError): db.save_allocations(conn,1,split)
    assert ledger.allocations(conn,db.transaction(conn,1))[0]['purpose']=='Expense'
    db.reset(conn,1)
    assert ledger.allocations(conn,db.transaction(conn,1))[0]['purpose']=='Needs review'


def test_payments_refunds_oneoffs_and_reviews(conn):
    rows=[['CREDIT','08/01/2026','STRIPE PAYROLL','6000','ACH','',''],['CREDIT','08/02/2026','STRIPE PAYROLL special','20000','ACH','',''],['DEBIT','08/03/2026','CHASE CREDIT CRD AUTOPAY','-500','ACH','',''],['CREDIT','08/04/2026','Venmo','42','ACH','',''],['DEBIT','08/05/2026','Store','-100','DEBIT_CARD','','']]
    load(conn,csv_bytes(rows=rows))
    cards=[['08/02/2026','08/04/2026','Payment thank you','','Payment','500',''],['08/03/2026','08/04/2026','Refund','Food','Return','150',''],['08/04/2026','08/05/2026','STATEMENT CREDIT bonus','','Adjustment','50','']]
    load(conn,csv_bytes('card',cards),2)
    t=ledger.report(conn,'2026-08')['totals']
    assert (t['payroll'],t['credits'],t['spending'],t['net'])==(600000,5000,-5000,610000)
    assert t['review_in']==2004200 and t['provisional']
    assert ledger.report(conn,'2026-08',True)['totals']['credits']==0
    db.save_allocations(conn,1,[allocation(600000,'Payroll',one_off=True)])
    assert ledger.report(conn,'2026-08',True)['totals']['payroll']==0
    assert sum(ledger.report(conn,'2026-08')['categories'].values())==t['spending']


@pytest.mark.parametrize('kind,description,typ,cents',[
    ('checking','Payment to Chase card ending in 1234','ACH',-100),
    ('checking','CHASE CREDIT CRD AUTOPAY','ACH',-100),
    ('card','Thank you','Payment',100),
])
def test_unmatched_payment_legs(kind,description,typ,cents):
    a=ledger.default(dict(kind=kind,description=description,type=typ,cents=cents,category=''))
    assert a['purpose']=='Transfer'


def test_source_immutability_and_override_month(conn):
    load(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('UPDATE transactions SET cents=0 WHERE id=1')
    conn.rollback()
    db.save_allocations(conn,1,[allocation(-800000,month='2027-04')])
    assert '2027-04' in ledger.months(conn)
    assert ledger.report(conn,'2027-04',True)['totals']['spending']==800000
