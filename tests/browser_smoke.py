"""Optional real-browser smoke test; uses only a disposable synthetic ledger.

Install playwright==1.56.0 and run `python -m playwright install chromium`, or set
PLAYWRIGHT_CHROMIUM_EXECUTABLE to an installed Chromium. Run from the repo root:
python tests/browser_smoke.py
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8517
URL = f'http://127.0.0.1:{PORT}'
SYNTHETIC = b'Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\nDEBIT,08/26/2026,Synthetic housing,-8000.00,ACH,,\n'


def start(path, log):
    process = subprocess.Popen([sys.executable,'-m','streamlit','run','app.py','--server.address','127.0.0.1','--server.port',str(PORT),'--server.headless','true'],cwd=ROOT,env=dict(os.environ,MONEY_TRACKER_DB=str(path)),stdout=log,stderr=log)
    for _ in range(200):
        try:
            with urllib.request.urlopen(URL+'/_stcore/health',timeout=1) as response:
                if response.read()==b'ok':
                    return process
        except OSError:
            pass
        if process.poll() is not None:
            raise RuntimeError('Streamlit exited; inspect browser test server log')
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError('Streamlit startup timeout')


def stop(process):
    process.terminate()
    process.wait(timeout=10)


def september(page):
    page.get_by_role('combobox').first.click()
    page.get_by_role('option',name='2026-09',exact=True).click()
    metric=page.locator('[data-testid=stMetricValue]')
    from playwright.sync_api import expect
    expect(metric.nth(8)).to_have_text('$8,000.00')
    expect(metric.nth(3)).to_have_text('$0.00')


def main():
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        path=Path(directory)/'synthetic.sqlite3'
        log=open(Path(directory)/'server.log','w')
        process=start(path,log)
        executable=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {}))
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.goto(URL)
            page.get_by_text('Import',exact=True).click()
            page.get_by_label('Account display name').fill('Synthetic checking')
            page.get_by_role('button',name='Create account',exact=True).click()
            page.get_by_text('Account created.',exact=True).wait_for()
            page.locator('input[type=file]').set_input_files({'name':'synthetic.csv','mimeType':'text/csv','buffer':SYNTHETIC})
            page.get_by_role('button',name='Preview file').click()
            page.get_by_text('1 rows · 1 proposed adds · 0 proposed reuses',exact=False).wait_for()
            page.get_by_role('button',name='Commit import',exact=True).click()
            page.get_by_text('Import complete:',exact=False).wait_for()
            page.get_by_text('Edit & review',exact=True).click()
            page.get_by_text('Quick edit · whole source',exact=True).click()
            page.get_by_role('combobox').nth(1).click()
            page.get_by_role('option',name='Expense',exact=True).click()
            page.get_by_label('Budget month (YYYY-MM)',exact=True).fill('2026-09')
            page.get_by_label('Category',exact=True).fill('Synthetic rent')
            page.get_by_role('button',name='Save quick allocation',exact=True).click()
            page.get_by_text('Allocations saved.',exact=False).wait_for()
            page.get_by_text('Month',exact=True).click()
            september(page)
            page.close()
            stop(process)
            process=start(path,log)
            page=browser.new_page()
            page.goto(URL)
            september(page)
            print('PASS: browser upload → preview → commit → quick edit → month change → server restart → persisted totals')
        finally:
            browser.close()
            if process.poll() is None:
                stop(process)
            log.close()


if __name__=='__main__':
    main()
