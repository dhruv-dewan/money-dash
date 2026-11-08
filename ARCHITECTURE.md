# Architecture guide

## Boundaries

`app.py` handles Streamlit widgets, dollar formatting and explicit actions. Core
modules do not import Streamlit. `chase.parse` returns typed row dictionaries and
raw source metadata without writing. `imports.preview` reads the ledger without
writing. `imports.commit` locks for a write, verifies the database revision and
commits a complete file or nothing. All SQL values use bound parameters.

## Storage model (SQLite schema version 1)

- `accounts`: stable integer ID, unique user-chosen name, checking/card kind.
- `batches`: account, original basename, exact SHA-256, timestamp, row count and
  observed actual date bounds; unique on (account, hash).
- `transactions`: immutable original dates, signed cents, description, type,
  category, memo and complete metadata. Each account/fingerprint has sequential
  occurrence numbers. A trigger rejects updates to facts; suppression is mutable.
- `import_rows`: (batch, CSV row number) to canonical transaction plus full row
  facts. Later metadata is preserved here without changing the original source.
- `allocations`: ordered saved sets with signed cents, purpose, category, note,
  one-off, nullable budget month. No saved set means one computed default.
- `state`: monotonically increasing revision invalidates old import previews.

Foreign keys are enabled on every connection. Stable IDs never depend on CSV
line numbers. File hash is exact bytes; fingerprints use both dates, signed cents,
type and whitespace-normalized descriptions (references retained), within one
account. Category, memo and checking metadata are evidence, not identity.

## Import algorithm

For each fingerprint, incoming rows reuse existing occurrences in order, preferring
ones with matching metadata in any retained provenance. Excess incoming rows add
occurrences; fewer incoming rows never delete existing ones. Suppressed occurrences
participate in matching. Competing candidates with different effective annotations
or suppression require explicit mapping unless metadata resolves the choice.
The UI exposes every overlapping group, including single matches, so the user can
choose distinct. Commit validates account/fingerprint compatibility and prohibits
reusing one occurrence twice in the same file. No reuse overwrites allocations.

An identical hash on the account is a no-op. Cross-account hash reuse requires a
checkbox confirmation. A transaction-level `BEGIN IMMEDIATE` plus revision check
rejects previews after intervening writes. The UI keeps previews in session state;
rerenders never commit. A separate action can detach one wrongly linked import
row into a new source occurrence, leaving existing edits and facts untouched.

## Allocation and report invariants

`ledger.validate` checks integer cents, purpose/sign compatibility, exact sum,
original sign on all nonzero portions, valid budget month and notes for exclusions.
Zero sources permit one zero allocation. `db.save_allocations` validates the entire
replacement before deleting and inserting inside one SQLite transaction. Reset
removes saved rows, and suppression changes only a flag. The app closes each
connection at the end of a rerender.

`ledger.entries` combines source facts with effective allocations. `ledger.report`
selects unsuppressed allocations using actual month, or budget month for Typical
with one-offs excluded, then derives both category totals and metrics from that
same selection. Needs review is excluded but disclosed. `ledger.checking` selects
original unsuppressed checking sources by posting month, counting each once even
when split. Actual and Typical can have different rows. UI filters affect only
detail tables. Month selection includes budget-only months and starts July 2026.

## Tradeoffs and extension points

Source dictionaries keep this small MVP readable; public functions own validation.
Default classification is deliberately narrow and recalculated for unsaved rows.
Explicit saved decisions persist. A future classifier migration should freeze or
version defaults if policy changes must not affect historical unsaved rows.

The app is designed for one local user, not concurrent collaborative editing.
Import revision checks prevent stale deduplication decisions; allocation editing
currently uses last explicit save wins. SQLite integer range is enforced per
source/allocation, while Python report sums can exceed that range safely.

Future work may add bank adapters returning the parser row contract, migrations
beyond user_version 1, richer review policies, or automated linking. Auth, external
sync, and balance tracking are intentionally outside this MVP.
