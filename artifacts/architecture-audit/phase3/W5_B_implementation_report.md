# W5-B Account daily model quota implementation report

## Baseline and scope

- W5-A dependency and starting HEAD: `c0b050a0f7fac2e405b24ceb298d310c5aa2468a`.
- Branch: `main`; the worktree was clean before implementation.
- Scope is W5-B only. Model request preparation/sending, snapshots, fallback policy, prompt
  APIs, and frontend work remain intentionally unwired for W5-C.

## Schema and UTC reset rule

Migration `038_account_daily_model_budget.sql` adds an Account/date summary and an immutable
operation ledger. Both amounts are non-negative. Ledger shape constraints explicitly define
reserved, settled, and released rows; settled provenance remains exactly `provider`,
`measured`, or `estimated`. `snapshot_id` is nullable for W5-C.

`quota_date` is the UTC calendar date of the reservation instant. It is stored on both rows and
returned by reserve so settlement/release can bind the original day even after midnight. A new
UTC date creates an independent summary; previous daily rows are not rewritten. A NULL
entitlement limit disables exhaustion enforcement but not accounting.

The Worker receives SELECT/INSERT/UPDATE on both runtime tables. The API receives SELECT only.
No DELETE privilege is granted.

## Services and transaction boundaries

`AccountDailyBudgetService` resolves W5-A entitlement state inside the mutation transaction,
rejects disabled/missing/expired access, locks the daily summary, deduplicates by
`(account_id, quota_date, operation_id)`, and updates ledger and summary together. Current
entitlement limits are read on every new reservation, so increases and decreases take effect
immediately without modifying historical usage.

`RunBudgetService` now exposes `reserve_in_uow`, `settle_in_uow`, and `release_in_uow` while its
existing public methods still create their own retryable UnitOfWork. Existing dimensions,
protected final-response reserve, observe/enforce status behavior, stable errors, replay rules,
and usage sources are retained. In particular, the public enforce-mode exhaustion path still
commits the Run status before raising.

`ModelBudgetCoordinator` uses one retryable UnitOfWork for both Account/day and Run mutation.
Account and Run reservation, settlement, or release therefore commit or roll back together.
It also requires the Account total to equal the Run `model_total_tokens` dimension, preventing
the two ledgers from being called with divergent token totals.

## Failure and accounting matrix

| Provider outcome | Coordinator action | Account/day result | Run result |
|---|---|---|---|
| Definitely not dispatched | `release` | reservation removed, used unchanged | reservation removed, used unchanged |
| Response includes usage | `settle(..., provider)` | actual tokens consumed | actual dimensions consumed |
| Locally measured usage | `settle(..., measured)` | actual tokens consumed | actual dimensions consumed |
| May have dispatched, usage unavailable | settle reserved amount with `estimated` | conservative reservation consumed | conservative dimensions consumed |

Reservation replays require the same amount/dimensions. Settlement replays require the same
actual values and source. Released or settled operations cannot transition incompatibly.

## Validation evidence

- Focused Ruff over changed Python source and tests: passed.
- `pytest -q test/web_persistence/test_account_daily_budget.py test/web_persistence/test_run_budget.py test/test_run_budget.py test/test_account_daily_budget_contract.py`: **24 passed**.
- An earlier PostgreSQL-only focused run of the two persistence files: **14 passed**.
- `pytest -q test/web_persistence`: **178 passed, 19 skipped, 5 failed, 2 errors** in
  31:26. The non-passing cases were environmental interference from the already-running local
  dispatcher/QQ services sharing the test database: three Outbox assertions observed events
  consumed by the live dispatcher, two QQ cancellation assertions observed live active Runs,
  and two fixtures deadlocked while TRUNCATE competed with service row locks. None touched the
  new quota tables or budget coordinator; the focused suites passed before and after migration.
- Final Ruff, compile, `git diff --check`, and status checks are recorded immediately before
  the implementation commit.

## Deviations and W5-C integration points

- No Account quota read projection was added because W5-B runtime/tests do not require one and
  exposing it through QueryService would prematurely expand the API.
- Settlement/release accept the reserve result's `quota_date`; they can also resolve a unique
  operation when callers omit it. W5-C should retain and pass the date for explicitness.
- The coordinator is intentionally not wired into `ResourcePool` in W5-B. W5-C must provide
  Account identity, preserve provider-attempt operation identity, call atomic reserve before
  dispatch, and choose release versus actual/conservative settlement using the matrix above.
