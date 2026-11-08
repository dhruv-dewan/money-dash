# MVP verification

All transactions used in verification were synthetic. No private exports,
populated databases or screenshots are committed.

## Automated suite

Command: `.venv/bin/python -m pytest -q`

Result: **39 passed** (37 core cases plus 2 Streamlit UI tests), no skipped or
expected-failure cases. Python 3.12.14, Streamlit 1.51.0, SQLite standard library.

The housing acceptance test verifies integer cents: August checking outflow
800000, August Actual spending 300000, August Typical spending 0, September
Typical spending 300000, and September roommate repayment 250000 excluded from
personal views. Re-import and closing/reopening SQLite retain the split.

The Streamlit AppTest exercises the dynamic split editor, explicit Save, September
selection showing $3,000 Typical / $0 Actual spending, a fresh app session, and
Reset. The other UI case covers first-run empty state and account creation.

## Real browser and server restart

Command:

```sh
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
  .venv/bin/python tests/browser_smoke.py
```

Result: **PASS** with Playwright 1.56.0 and installed headless Chromium. This test
starts its own localhost server on port 8517 and a temporary SQLite database,
creates a checking account, uploads a synthetic CSV, previews and commits it,
uses the whole-source quick editor to classify the $8,000 withdrawal and assign
September, then verifies September Typical $8,000 / Actual $0. It stops/restarts
the Streamlit process and confirms those values in a fresh browser page. It
cleans up its processes and temporary data. The three-part housing split itself
is verified by AppTest and the core acceptance test, rather than browser canvas
automation.

## Other checks

- `python -m compileall -q money_tracker app.py`: passed.
- `python -m pip check`: no broken requirements.
- Python 3.11 binary dependency resolution against `requirements-lock.txt`:
  passed using pip's `--dry-run --ignore-installed --python-version 3.11
  --only-binary=:all:`. Actual runtime tests used Python 3.12; macOS was not
  available for an execution test.
- Local Streamlit health endpoint returned `ok`; functional readiness is supported
  by the UI and browser tests above, not just the health endpoint.
- `git diff --check`: passed.

No real Chase exports or bank services were used. Changed bank descriptions and
indistinguishable partial-export rows require manual duplicate review as described
in the README. External sync, balances, auth and automatic payment linking are
outside the MVP scope.
