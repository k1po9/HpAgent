# Observability preflight report

Date: 2026-08-12

Scope: the observability closure described in
`docs/web/fix/HpAgent_Observability_Preflight_and_Logging_Normalization_Guide.md`.

## Result

The in-process logging contract is normalized for the shared Agent execution
path, Web and QQ hosts, model calls, context assembly, and long-term memory
recall/retention. Lifecycle statuses are limited to `started`, `success`,
`failed`, `cancelled`, `degraded`, and `skipped` in the touched paths. Optional
correlation fields are omitted rather than emitted as empty or null values.

The unified account-backend startup validator now lives under `account` and no
longer creates a QQ bootstrap dependency on `orchestration.web_workers`.

## Correlation and failure classification

- Web execution events carry `run_id == execution_id`, plus the available
  conversation, session, account, and `surface=web` fields.
- QQ execution events carry `execution_id`, `workflow_id`, session, account,
  and `surface=qq`; no synthetic `run_id` is emitted.
- Model lifecycle events distinguish `agent_turn` from `forced_final` and use
  stable `model_timeout` / `model_unavailable` failure codes.
- Recall degradation uses `memory_backend_unavailable`, while a recall that
  actually fails execution uses `memory_recall_failed`. Disabled Web memory is
  recorded as `memory_recall_skipped` with `reason=memory_disabled`. Retention
  failure uses `memory_retain_failed`. QQ retention exceptions continue to
  propagate so the established failure semantics are unchanged.
- Stable execution failures retain their code at the Host boundary; unexpected
  exceptions are classified as `internal_error`.

## Verification performed

- `test/test_logging.py`, `test/test_observability_preflight.py`,
  `test/test_qq_execution_compat.py`, `test/test_qq_retention_document.py`, and
  `test/test_web_outbox_recovery.py`: **32 passed**.
- The preflight tests cover JSON schema promotion, omission of `None`, exception
  serialization, normal and forced-final model success/timeout/failure, Web and
  QQ Host lifecycle correlation, QQ recall degradation, and QQ retention
  failure propagation.
- `git diff --check`: passed.
- Ruff on all touched Python files: passed.
- `make lint typecheck`: passed after renaming the invalid colon-form Make
  target to `ci-web` and normalizing the Web API import ordering found by Ruff. MyPy
  checked 22 source files successfully.
- Docker service inspection: application PostgreSQL, Temporal, Redis, Hindsight,
  API, and gateway containers were running; health-checked dependencies were
  healthy.

## Incomplete external checks

- `test/test_web_temporal_contract.py` did not finish within 300 seconds. It
  reached 10 passing tests before the timeout. An isolated verbose run showed
  the next test waiting in
  `test_dispatcher_exhaustion_dead_letters_through_authoritative_outbox`.
- The real `test/test_web_temporal_integration.py` suite against the healthy
  local Temporal service also exceeded the 300-second limit without producing a
  terminal result.
- A real model-backed Web turn and live QQ delivery were not triggered: that
  would require active external model/channel credentials and would create
  external side effects. The deterministic Host/model/memory paths are covered
  by the tests above.
- GitHub Actions could not be queried because `gh` has no authenticated GitHub
  host in this environment. The public Actions page was also unavailable to the
  browser fetch, so no CI conclusion is claimed here.

These limitations are environment/external-state observations, not recorded as
passing checks.
