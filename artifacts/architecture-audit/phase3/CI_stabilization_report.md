# CI Stabilization and Cleanup Closure

- Audited HEAD: `8351ad1364ba026ee5471662db9e2f133aa9e537`
- Branch: `feat/hpagent-web`
- Report location: the requested Phase 3 evidence directory already exists, so this maintenance record remains there.
- Remote reference: reported GitHub Actions run `#59`. `gh run view 59 --repo k1po9/HpAgent` was attempted, but GitHub API access timed out in this environment; failures were reproduced from the workflow commands locally.

| Failed gate / symptom | Root cause | Classification | Canonical fix | Evidence |
|---|---|---|---|---|
| `lint-and-typecheck` / `session_key()` returns `Any` | The Chat binding accepted an untyped request even though it consumes a stable Chat context shape. | REAL_CODE_BUG | Added structural request/context/loaded-identity Protocols at the Chat adapter boundary; no cast, coercion, ignore, or runtime change. | `make PYTHON=.venv/bin/python lint typecheck` passes. |
| `existing-unit-tests` imports `audit_w3_reachability` | The test asserted a one-time W3 retirement report and depended on an intentionally deleted audit script. Permanent import isolation remains covered by the other tests in the same module. | OBSOLETE_HISTORICAL_CHECK | Retired only the historical report test; did not restore W3 code. | Non-PostgreSQL suite: 453 passed, 23 environment skips. |
| Phase D replay path absent | `test/test_web_temporal_replay.py` was deleted during runtime consolidation; frozen current histories moved to `test/test_agent_segment_replay.py`. | TEST_DRIFT | Pointed CI at the current frozen replay suite. | 5 replay tests passed. |
| Phase F references `test_qq_retention_document.py` | The deleted filename no longer represents the current retention authority; PostgreSQL QQ ingress and memory retention suites cover current behavior. | CI_CONFIG_DRIFT | Removed the nonexistent filename while retaining the current Hindsight, background, outbox, QQ ingress, and PostgreSQL retention coverage. | Phase F unit selection: 26 passed; PostgreSQL suites: 192 passed, 31 service-dependent skips in that run. |
| Frontend workers fail under Node 20 / `markAsUncloneable` | Current jsdom/Vite/Vitest dependency graph is Node-22-era while CI, package metadata, and the production build image still admitted Node 20. Unbounded jsdom worker spawning also caused memory-sensitive startup timeouts locally. | ENVIRONMENT_VERSION_DRIFT | Standardized CI, package engines, lockfile, and Docker build stage on Node 22; bounded Vitest to four workers. | Frontend lint/typecheck: pass; Vitest: 13 files / 78 tests pass; production build passes. |
| Predefined E2E login rejected on empty DB | E2E exported obsolete `WEB_CREDENTIALS_JSON` and inserted identity bindings directly, but current authentication requires rows in `web_credentials`. | TEST_DRIFT | Bootstrap `alice` and `bob` through canonical `RegistrationService`, idempotently. | Fresh PostgreSQL/Redis auth suite: 4 passed; fresh default browser suite subsequently reached all downstream scenarios. |
| Default browser suite included live F4 approval spec | `f4-approval.spec.ts` requires the real Agent/Temporal/file-tool composition provided by `test:e2e:f4`; the default Playwright config deliberately starts Fake Executor with uploads disabled. | CI_CONFIG_DRIFT | Excluded only that live-profile spec from the Fake Executor suite; the dedicated live F4 command remains unchanged. | Default browser gate runs 13 appropriate specs; live F4 remains a separate explicit suite. |
| Stop/retry E2E expected Retry after cancellation | Current API and UI contracts intentionally reject retrying cancelled Runs; only safely retryable failed Runs expose Retry. | TEST_DRIFT | Rewrote the browser assertion to protect cancellation and absence of unsafe Retry, matching API/unit coverage. | Updated stop test passes. |
| Long conversation intermittently leaves typed text unsent | Pressing Enter immediately after a terminal transition raced the controlled composer state. | TEST_DRIFT | Shared E2E helper now waits for the semantic Send button to enable and clicks it. Assertions remain unchanged. | Three-turn refresh test passes after the change. |
| Temporal service fails during container initialization | CI omitted `BIND_ON_IP=0.0.0.0`; auto-setup bound gRPC only to its container IP, so localhost readiness could never succeed. The old `tctl` command was also superseded. | INFRASTRUCTURE_READINESS_FAILURE | Added the bind setting and used `temporal operator cluster health --address localhost:7233`. | Fresh container reported `SERVING`; 5 replay and 14 real Temporal integration/fault/lifecycle tests passed. |

## Files changed

- `.github/workflows/ci.yml`
- `src/conversation_domain/execution_bindings.py`
- `test/test_w3_survivor_ownership.py`
- `web/package.json`, `web/package-lock.json`, `web/Dockerfile`
- `web/vite.config.ts`, `web/playwright.config.ts`
- `web/scripts/e2e-backend.sh`
- `web/e2e/helpers.ts`, `web/e2e/stop-retry.spec.ts`
- this report

The pre-existing user edit in `artifacts/architecture-audit/phase2_1/03_architecture_truth_table.md` was not modified by this task.

## Commands and results

- `make PYTHON=.venv/bin/python lint typecheck`: pass.
- `python -m pytest -m "not postgres" test`: 453 passed, 23 skipped (only explicitly unavailable service-backed tests).
- Fresh PostgreSQL contract/API run over `test/web_persistence test/web_api`: 192 passed, 31 skipped because Temporal/Redis were intentionally absent from that gate; current isolation unit selection: 15 passed.
- Phase F current unit selection: 26 passed.
- `npm ci`, lint, typecheck, Vitest, build under Node 22: pass; 78 unit tests pass. Build retains the existing large-chunk advisory.
- Fresh Temporal readiness: `SERVING`; frozen replay: 5 passed; workflow integration/fault/lifecycle selection: 14 passed.
- Fresh PostgreSQL + fresh Redis browser validation: predefined authentication fixed; default suite exercised auth, workbench, artifact rendering, refresh, SSE recovery, multi-tab isolation, and stop. One pre-fix full run had 12 passes plus one retry-classified flaky conversation case; its targeted final form passed after the deterministic Send-button fix.
- A final clean-database full browser rerun was attempted after all E2E edits, but the already saturated 3.3 GiB local host exhausted swap and Chromium stopped responding even on `page.goto`; it was aborted and the developer compose services were restored. This is recorded as a local resource blocker, not a product assertion failure.
- Gateway runtime smoke using the locally cached gateway image: G-T02, G-T03, and G-T04 pass. Building a new Node-22 gateway image locally was blocked only by Docker Hub token timeout; GitHub Actions must confirm that clean image build.

## Remaining warnings and deferred cleanup

- Python emits a third-party Starlette/httpx deprecation warning.
- Vite reports a chunk larger than 500 kB; this is a non-blocking optimization follow-up.
- `npm audit` reports two moderate and one high dependency advisory; dependency remediation is separate from this focused closure.
- Historical Phase job names were preserved to avoid required-check/branch-protection drift.
- The live F4 approval suite remains available as `npm run test:e2e:f4`; it is intentionally not run inside the Fake Executor browser gate.

## Readiness assessment

Repository/test/config drift found in run #59 has been corrected without restoring W3 code or changing product architecture. Local canonical Python, frontend, PostgreSQL, isolation, Temporal, and the corrected browser scenarios have executed successfully. Local CI-equivalent validation is not claimable as fully green because the final all-at-once browser rerun hit host resource exhaustion and the new gateway image could not be pulled/built. A new GitHub Actions run is required to confirm the complete hosted matrix and clean Node-22 gateway image build.
