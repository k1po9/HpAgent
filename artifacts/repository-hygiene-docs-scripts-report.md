# Repository Hygiene — README, Docs, and Scripts

## 1. Audited HEAD

- Branch: `feat/hpagent-web`
- Audited HEAD: `b8ce3e8637d679908feb1b2fcb702cc0f0da93ad`
- Authority order: production source and entrypoints, Compose/Make/config wiring, current tests, then closure evidence.
- The pre-existing modification to `artifacts/architecture-audit/phase2_1/03_architecture_truth_table.md` was preserved and excluded from this work.

## 2. Inventory and actions

| Path | Current purpose | Still valid? | Action |
| --- | --- | --- | --- |
| `README.md` | Repository landing page | Content was not current | REWRITE |
| `docs/architecture/decisions/old/**` | Archived design/history | No | DELETE |
| `docs/web/**`, old acceptance/handoff reports | Delivery history | No | DELETE |
| Current architecture information in prior docs | Candidate facts | Partly | EXTRACT |
| `docs/operations/runbook.md` | Operator entry point | Partly | REWRITE |
| `docs/operations/troubleshooting.md` | Operator diagnostics | Partly | REWRITE |
| `docs/reference/configuration.md` | Configuration reference | Partly | REWRITE |
| `scripts/audit_w3*.py`, `scripts/verify_w*.py` | Completed consolidation gates | No | DELETE |
| Registry invariant in runtime audit | Current registry correctness | Yes | EXTRACT |
| Root operational/development scripts | Current tooling mixed at root | Yes, selected files | MERGE |
| Duplicate cleanup/model/debug scripts | Redundant or manual archaeology | No | DELETE |
| `scripts/benchmarks/**` | Reproducible current experiments | Yes | KEEP |
| `config/models.f4-e2e.yaml` | Browser test fixture | Yes, not production config | EXTRACT |

## 3. Deleted docs

Deleted 102 tracked documentation files: version archives, architecture progress notes, implementation plans, phase reports, acceptance/handoff documents, drift reports, and duplicated Web design material. The ignored duplicate `docs/web/build/` tree was also removed from the working tree. No replacement archive directory was created.

## 4. Rewritten and new docs

The current documentation tree contains 19 files:

- navigation root: `docs/README.md`;
- architecture: overview, runtime, data/state, capabilities, reliability, sequences;
- development: setup, testing, extending;
- operations: deployment, runbook, logging, troubleshooting, backup/restore;
- reference: configuration, repository layout, Temporal, API.

## 5. Deleted scripts

Deleted 19 historical or redundant scripts: four architecture audits, eight named gate runners, one workflow-history capture utility, four overlapping clear/reset helpers, one duplicate model tester, and one manual WebSocket event client.

## 6. Retained and extracted scripts

- Retained all 12 current benchmark files under `scripts/benchmarks/`.
- Moved selected current helpers into `scripts/dev/`, `scripts/operations/`, and `scripts/check/` with purpose-based names.
- Extracted current production registry validation to `scripts/check/temporal_registry.py`; it writes no audit artifact and uses inert collaborators.
- Added `scripts/operations/logs.sh` as the canonical Compose logging entry point.
- Updated tests, CI, and Compose references for moved scripts.

## 7. README changes

`README.md` now gives the project position, current capabilities, one architecture flow, verified Compose quick start, migration behavior, configuration entry points, canonical log commands, development commands, repository layout, and documentation navigation. It contains no consolidation chronology.

## 8. Configuration and documentation corrections

- Moved `models.f4-e2e.yaml` from production `config/` to `test/fixtures/`.
- Added a test-only Compose overlay and updated the browser test launcher so the worker mounts that fixture only for the test.
- Removed the Web E2E call to a nonexistent credentials migration script; its current database bootstrap remains self-contained.
- Corrected frozen workflow fixture documentation that referenced a deleted history-capture script.
- Documented environment, model, prompt, MCP, and research configuration from current source and Compose wiring.

## 9. Logging workflow

Canonical command: `./scripts/operations/logs.sh`.

It supports all services, API, workers, QQ, infrastructure, named services, follow/no-follow, and a configurable tail count. Documentation covers JSONL locations and correlation by `run_id`, `conversation_id`, `workflow_id`, and `operation_id`, plus execution-versus-delivery failure diagnosis.

## 10. External reference fixes

- Compose Hindsight volume: `scripts/operations/start-hindsight.sh`.
- CI gateway smoke: `scripts/check/gateway-smoke.sh`.
- MCP and observability tests: new check/operations paths.
- Runtime registry test: `scripts/check/temporal_registry.py`.
- Browser E2E: test model fixture overlay and removal of the missing script call.
- No README/docs link points to a deleted file.

## 11. Checks and tests executed

- All retained shell scripts: `bash -n` — PASS.
- All retained Python scripts: in-memory compile — PASS.
- Safe `--help` smoke checks for logs, reset, MCP, models, identity bootstrap, and observability — PASS.
- `docker compose config --quiet` for base, Web profile, and test overlay — PASS.
- Canonical logs script against API and worker selections — PASS.
- Live API `/health/ready` — PASS.
- Markdown relative-link scan — PASS.
- Current registry checker — PASS.
- Focused configuration/script/observability/document tests — 37 passed.
- Focused benchmark/state-authority/registry tests — 20 passed.
- Stale terminology and deleted-path scans — PASS after justified prohibited-symbol checks.
- `git diff --check` — PASS.

## 12. Remaining debt and judgment

No current README/docs/scripts debt was identified. Historical labels remain in evidence, CI job identifiers, and test fixture/test filenames outside this task's cleanup boundary; they do not describe the current repository documentation/tooling layer. The registry checker intentionally names prohibited runtime symbols so their reintroduction fails validation.

**PASS**

```text
README: CURRENT
DOCS: CURRENT
SCRIPTS: CURRENT
```
