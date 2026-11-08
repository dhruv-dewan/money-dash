"""Local Streamlit interface. Writes happen only on explicit actions."""
from datetime import date
from decimal import Decimal, InvalidOperation
import sqlite3
import pandas as pd
import streamlit as st
from money_tracker import db, imports, ledger

st.set_page_config(page_title='Money tracker', page_icon='◈', layout='wide', initial_sidebar_state='expanded')


def money(cents):
    return f'${Decimal(int(cents)) / 100:,.2f}'


def table(rows):
    if not rows:
        st.caption('No rows in this selection.')
        return
    formatted = []
    for row in rows:
        formatted.append({k: (money(v) if k.endswith('cents') else v) for k,v in row.items() if k not in ('metadata','fingerprint')})
    st.dataframe(formatted, hide_index=True, width='stretch')


def main():
    conn = db.connect()
    try:
        render(conn)
    finally:
        conn.close()


def render(conn):
    st.title('◈ Money tracker')
    st.caption('Personal cash flow · Chase checking & cards · local SQLite storage')
    if 'notice' in st.session_state:
        st.success(st.session_state.pop('notice'))
    page = st.sidebar.radio('Workspace', ['Month', 'Import', 'Edit & review'])
    all_sources = ledger.sources(conn)
    if not all_sources:
        st.info('Start in Import: create one checking account and a separate account for each card, then upload a Chase CSV.')
    if page == 'Import':
        render_import(conn)
    elif page == 'Month':
        render_month(conn)
    else:
        render_editor(conn)


def render_import(conn):
    st.header('Import Chase CSV')
    with st.expander('Create an account', expanded=not db.query(conn,'SELECT * FROM accounts')):
        with st.form('account'):
            name = st.text_input('Account display name')
            kind = st.selectbox('Account kind', ['checking','card'])
            if st.form_submit_button('Create account'):
                db.create_account(conn,name,kind)
                st.session_state['notice'] = 'Account created.'
                st.rerun()
    accounts = db.query(conn,'SELECT * FROM accounts ORDER BY id')
    if not accounts:
        return
    account_id = st.selectbox('Import into account', [a['id'] for a in accounts], format_func=lambda i: next(f"{a['name']} ({a['kind']})" for a in accounts if a['id']==i))
    upload = st.file_uploader('Chase export',type=['csv'])
    if upload and st.button('Preview file'):
        st.session_state['preview'] = imports.preview(conn,account_id,upload.getvalue(),upload.name)
    plan = st.session_state.get('preview')
    if plan and plan['account_id'] == account_id:
        st.subheader(f"Preview · {plan['filename']}")
        st.caption(f"Detected {next(a['kind'] for a in accounts if a['id']==account_id)} schema. Preview is read-only.")
        if plan['already']:
            st.info('Previously imported file: exact bytes on this account. Committing is a no-op.')
        adds = sum(v is None for v in plan['mapping'].values())
        st.write(f"{len(plan['rows'])} rows · {adds} proposed adds · {len(plan['rows'])-adds} proposed reuses")
        table(plan['rows'])
        decisions = {}
        st.subheader('Duplicate review')
        st.caption('Expense classification is separate. New/distinct preserves an additional legitimate occurrence. Reuse never overwrites edits.')
        for group in plan['groups']:
            with st.expander(f"{group['incoming'][0]['description']} · incoming {len(group['incoming'])} / existing {len(group['existing'])}", expanded=True):
                table(group['existing'])
                for row in group['incoming']:
                    number = row['row_number']
                    proposed = plan['mapping'][number]
                    required = number in group['required_rows']
                    options = ['Choose mapping'] if required else []
                    options += ['New / distinct'] + [str(s['id']) for s in group['existing']]
                    initial = 0 if required else options.index(str(proposed) if proposed else 'New / distinct')
                    choice = st.selectbox(f"CSV row {number}" + (' — mapping required' if required else ''), options, index=initial, key=f"map-{plan['hash']}-{account_id}-{number}")
                    if choice != 'Choose mapping':
                        decisions[number] = None if choice == 'New / distinct' else int(choice)
        cross = False
        if plan['cross_account']:
            cross = st.checkbox('I confirm this exact file belongs on this different account')
        if st.button('Commit import',type='primary'):
            result = imports.commit(conn,plan,decisions,cross)
            st.session_state.pop('preview',None)
            st.session_state['notice'] = f"Import complete: {result['added']} added, {result['reused']} reused." + (' Previously imported file.' if result['already'] else '')
            st.rerun()
    st.subheader('Import history')
    table(db.query(conn,'SELECT b.*,a.name account FROM batches b JOIN accounts a ON a.id=b.account_id ORDER BY b.id DESC'))


def render_month(conn):
    month = st.selectbox('Month',ledger.months(conn))
    if month == date.today().strftime('%Y-%m'):
        st.caption('Current month · month-to-date')
    st.caption('Whole-month totals. Actual uses purchase dates (checking: posting); Typical uses allocation budget months and excludes one-offs. These views may contain different rows.')
    reports = [ledger.report(conn,month),ledger.report(conn,month,True)]
    for col,title,report in zip(st.columns(2),['Actual personal','Typical'],reports):
        with col:
            st.subheader(title)
            t = report['totals']
            if t['provisional']:
                st.warning('Provisional: unresolved allocations are excluded from counted totals.')
            for label,key in [('Take-home payroll','payroll'),('Other income','other_income'),('Standalone credits','credits'),('Net spending','spending'),('Net','net')]:
                st.metric(label,money(t[key]))
            st.text(f"Needs review: {money(t['review_in'])} inflow / {money(t['review_out'])} outflow")
            st.write('Net spending by category')
            table([dict(category=k,cents=v) for k,v in report['categories'].items()])
    st.subheader('Allocation drill-down')
    view = st.radio('Detail view',['Actual personal','Typical'],horizontal=True)
    rows = reports[view == 'Typical']['rows']
    c1,c2,c3,c4 = st.columns(4)
    account = c1.selectbox('Account filter',['All']+sorted({s['account'] for s in ledger.sources(conn)}))
    category = c2.selectbox('Category filter',['All']+sorted({r['category'] for r in rows}))
    review = c3.checkbox('Needs review only')
    transfers = c4.checkbox('Transfers only')
    rows = [r for r in rows if (account=='All' or r['account']==account) and (category=='All' or r['category']==category) and (not review or r['purpose']=='Needs review') and (not transfers or r['purpose']=='Transfer')]
    table(rows)
    if rows:
        selected = st.selectbox('Source to edit',sorted({r['transaction_id'] for r in rows}))
        if st.button('Edit selected source'):
            st.session_state['edit_id'] = selected
            st.info('Open Edit & review in the sidebar; your source is selected.')
    st.subheader('Checking activity · original posting amounts')
    checking = ledger.checking(conn,month)
    st.text(f"Inflow {money(checking['inflow'])} · Outflow {money(checking['outflow'])} · Net {money(checking['net'])}")
    table(checking['rows'])
    st.subheader('Imported months comparison')
    table([dict(month=m,actual_spending_cents=ledger.report(conn,m)['totals']['spending'],typical_spending_cents=ledger.report(conn,m,True)['totals']['spending']) for m in ledger.months(conn)])
    st.subheader('Observed account coverage')
    table(db.query(conn,'SELECT a.name,MIN(COALESCE(t.transaction_date,t.posting_date)) first_actual_date,MAX(COALESCE(t.transaction_date,t.posting_date)) last_actual_date,MIN(t.posting_date) first_posted,MAX(t.posting_date) last_posted FROM accounts a LEFT JOIN transactions t ON t.account_id=a.id GROUP BY a.id'))
    st.caption('Observed bounds do not establish complete statement coverage.')


def render_editor(conn):
    st.header('Edit & review')
    st.caption('Source amounts are immutable. Saved allocations replace the complete split atomically; Reset restores conservative defaults.')
    sources = ledger.sources(conn)
    if not sources:
        return
    review_only = st.checkbox('Show only needs-review sources')
    visible = [s for s in sources if not review_only or any(a['purpose']=='Needs review' for a in ledger.allocations(conn,s))]
    visible.sort(key=lambda s: abs(s['cents']),reverse=True)
    table([dict(id=s['id'],date=s['transaction_date'] or s['posting_date'],account=s['account'],description=s['description'],cents=s['cents'],suppressed=bool(s['suppressed'])) for s in visible])
    if not visible:
        st.success('No unresolved sources in this selection.')
        return
    ids = [s['id'] for s in visible]
    selected = st.session_state.get('edit_id')
    transaction_id = st.selectbox('Transaction',ids,index=ids.index(selected) if selected in ids else 0,format_func=lambda i: next(f"#{i} · {s['description']} · {money(s['cents'])}" for s in visible if s['id']==i))
    source = db.transaction(conn,transaction_id)
    table([source])
    st.write('Original source metadata')
    st.json(source['metadata'],expanded=False)
    saved = ledger.allocations(conn,source)
    if len(saved) == 1:
        a = saved[0]
        with st.expander('Quick edit · whole source'):
            with st.form(f'quick-{transaction_id}-{db.revision(conn)}'):
                st.caption(f"This allocation covers the full source amount: {money(source['cents'])}. Use the split editor below to allocate parts.")
                purpose = st.selectbox('Purpose', ledger.PURPOSES, index=ledger.PURPOSES.index(a['purpose']))
                category = st.text_input('Category', value=a['category'])
                note = st.text_input('Note', value=a['note'])
                budget = st.text_input('Budget month (YYYY-MM)', value=a['budget_month'] or '')
                one_off = st.checkbox('One-off', value=bool(a['one_off']))
                if st.form_submit_button('Save quick allocation'):
                    db.save_allocations(conn, transaction_id, [dict(cents=source['cents'], purpose=purpose, category=category, note=note, one_off=one_off, budget_month=budget or None)])
                    st.session_state['notice'] = 'Allocations saved. Monthly totals refreshed.'
                    st.rerun()
    editor = pd.DataFrame([dict(amount=f"{Decimal(a['cents'])/100:.2f}",purpose=a['purpose'],category=a['category'],note=a['note'],one_off=bool(a['one_off']),budget_month=a['budget_month'] or '') for a in saved])
    with st.form(f'allocations-{transaction_id}-{db.revision(conn)}'):
        st.caption('Edit the single row directly, or add rows for a split. Amounts are signed dollars, exact to cents. Budget month: YYYY-MM or blank.')
        edited = st.data_editor(editor,num_rows='dynamic',hide_index=True,width='stretch',column_config={'purpose':st.column_config.SelectboxColumn(options=ledger.PURPOSES,required=True),'amount':st.column_config.TextColumn(required=True),'one_off':st.column_config.CheckboxColumn(default=False)},key=f'grid-{transaction_id}-{db.revision(conn)}')
        save = st.form_submit_button('Save allocations',type='primary')
        reset = st.form_submit_button('Reset to defaults')
        if save:
            allocations = []
            for row in edited.astype(object).where(pd.notna(edited), '').to_dict('records'):
                try:
                    value = Decimal(str(row['amount']))*100
                    if not value.is_finite() or value != value.to_integral_value():
                        raise ValueError('Amounts must be exact cents')
                    allocations.append(dict(cents=int(value),purpose=row['purpose'],category=row['category'],note=row['note'],one_off=bool(row['one_off']),budget_month=row['budget_month'] or None))
                except InvalidOperation as exc:
                    raise ValueError('Enter valid signed dollar amounts') from exc
            db.save_allocations(conn,transaction_id,allocations)
            st.session_state['notice'] = 'Allocations saved. Monthly totals refreshed.'
            st.rerun()
        if reset:
            db.reset(conn,transaction_id)
            st.session_state['notice'] = 'Saved split removed; defaults restored.'
            st.rerun()
    st.subheader('Duplicate correction')
    if st.button('Restore suppressed source' if source['suppressed'] else 'Suppress confirmed duplicate'):
        db.suppress(conn,transaction_id,not source['suppressed'])
        st.rerun()
    provenance = db.query(conn,'SELECT r.batch_id,r.row_number,b.filename,r.facts FROM import_rows r JOIN batches b ON b.id=r.batch_id WHERE transaction_id=? ORDER BY batch_id,row_number',(transaction_id,))
    table(provenance)
    if len(provenance)>1:
        choices = [(p['batch_id'],p['row_number']) for p in provenance]
        separate = st.selectbox('Import row incorrectly reused',choices,format_func=lambda x:f'Batch {x[0]}, CSV row {x[1]}')
        if st.button('Separate import row into new source'):
            new_id = db.separate(conn,*separate)
            st.session_state['edit_id'] = new_id
            st.session_state['notice'] = f'New occurrence #{new_id} created with computed defaults. Existing edits retained.'
            st.rerun()


try:
    main()
except (ValueError, sqlite3.Error) as exc:
    st.error(str(exc))
    st.caption('No partial changes were saved. Correct the input, or preview again if the ledger changed.')
