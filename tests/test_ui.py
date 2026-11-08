from pathlib import Path
from streamlit.testing.v1 import AppTest
from money_tracker import db, imports, ledger
from test_mvp import csv_bytes

APP = str(Path(__file__).resolve().parents[1]/'app.py')


def test_editor_save_month_change_restart_and_reset(tmp_path, monkeypatch):
    path = tmp_path/'ui.sqlite3'
    monkeypatch.setenv('MONEY_TRACKER_DB',str(path))
    c=db.connect(path)
    db.create_account(c,'Synthetic checking','checking')
    imports.commit(c,imports.preview(c,1,csv_bytes(),'synthetic.csv'))
    c.close()
    app=AppTest.from_file(APP).run()
    assert not app.exception
    app.sidebar.radio[0].set_value('Edit & review').run()
    assert not app.exception
    c=db.connect(path); revision=db.revision(c); c.close()
    key=f'grid-1-{revision}'
    app.session_state[key]={'edited_rows':{0:{'amount':'-3000.00','purpose':'Expense','budget_month':'2026-09','category':'Rent'}},'added_rows':[
        {'amount':'-2500.00','purpose':ledger.EXCLUSION,'category':'Deposit','note':'Refundable deposit','one_off':False,'budget_month':''},
        {'amount':'-2500.00','purpose':ledger.EXCLUSION,'category':'Advance','note':'Roommate advance','one_off':False,'budget_month':''},
    ],'deleted_rows':[]}
    next(b for b in app.button if b.label=='Save allocations').click().run()
    assert not app.exception
    assert any('Allocations saved' in s.value for s in app.success)
    app.sidebar.radio[0].set_value('Month').run()
    app.selectbox[0].set_value('2026-09').run()
    assert not app.exception
    assert [m.value for m in app.metric if m.label=='Net spending']==['$0.00','$3,000.00']
    restarted=AppTest.from_file(APP).run()
    restarted.selectbox[0].set_value('2026-09').run()
    assert [m.value for m in restarted.metric if m.label=='Net spending']==['$0.00','$3,000.00']
    restarted.sidebar.radio[0].set_value('Edit & review').run()
    next(b for b in restarted.button if b.label=='Reset to defaults').click().run()
    c=db.connect(path)
    assert ledger.allocations(c,db.transaction(c,1))[0]['purpose']=='Needs review'
    c.close()


def test_empty_state_and_account_creation(tmp_path,monkeypatch):
    monkeypatch.setenv('MONEY_TRACKER_DB',str(tmp_path/'empty.sqlite3'))
    app=AppTest.from_file(APP).run()
    assert not app.exception
    assert any('Start in Import' in i.value for i in app.info)
    app.sidebar.radio[0].set_value('Import').run()
    app.text_input[0].set_value('Card one')
    app.selectbox[0].set_value('card')
    next(b for b in app.button if b.label=='Create account').click().run()
    assert not app.exception
    assert any('Account created' in s.value for s in app.success)
