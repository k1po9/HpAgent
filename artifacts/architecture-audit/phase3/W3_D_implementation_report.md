# Phase 3 W3-D — Compatibility / Experimental Surface Retirement

Baseline: `35104a0b02b445314533e3c40d69c86cb0c7822d`

## Deleted surfaces

- Removed `WEB_REAL_AGENT_ENABLED`, `WEB_REAL_AGENT_GATE_VERSION`, and their
  Temporal/Web API configuration fields. The main worker now always composes
  the canonical lifecycle and Agent workers.
- Removed the duplicate standalone `orchestration.web_worker` entrypoint and
  its topology gate.
- Removed `WEB_CREDENTIALS_JSON`, the configured-password and fallback
  adapters, and the credential migration helper. Web authentication now reads
  PostgreSQL `web_credentials` only.
- Removed JSON account models/service, `merge-account.py`, and stale reset and
  release instructions for `accounts.json`.
- Removed `MultiAgentConfig`, `AgentEntry`, `agent.mode`, `agents.yaml` loading,
  and `config/agents.yaml`.
- Removed the obsolete `RunRepository.context_messages` forwarding method and
  Hindsight's session-shaped `retain()` forwarding method; canonical callers
  use `MessageRepository` and `retain_document()`.
- Removed top-level progress-field normalization from SSE; canonical events use
  the versioned `payload` object.

## Registry and clean configuration

`W3_D_compatibility_scan.json` captures four registries. The canonical Web
lifecycle queue registers `AgentLifecycleWorkflow` plus Research, Document and
Artifact workflows. The Agent queue registers `AgentRunWorkflow`, ReAct,
Plan-and-Execute, AgentStep and ToolExecution. The independent Document
activity worker and scheduled Memory workflows remain registered. No retired
runtime type, dual registry, missing required workflow, retired import, or
removed configuration key was found.

A minimal YAML configuration and `WebApiSettings.from_env()` construct with all
retired variables absent. `docker compose config --quiet` also succeeds.

## Deliberately retained surfaces

- Schema migration runner and all PostgreSQL migrations.
- Research, Document, Artifact, File, Workspace, Memory, Trace and Budget.
- Model-provider fallback chains, browser fetch fallback, and public event/DTO
  normalization that serve current product contracts rather than a retired
  Agent runtime.
- Interaction-profile to channel prompt mapping, which is used by current
  context assembly and is outside W3 package/composition cleanup.

## Validation

- Focused/runtime contracts: `129 passed`.
- Isolated PostgreSQL identity and persistence contracts: `31 passed`.
- Isolated SSE gateway contract after transport compatibility removal: `13 passed`.
- Full collection: `686 tests collected`.
- W3-D config/import/definition/path scan: pass.
- Canonical registry capture: pass.
- Minimal clean configuration: pass.
- Docker Compose configuration: pass.
- Ruff on every changed Python file: pass.
- `git diff --check`: pass.
- Phase 2.2 frozen evidence: unchanged.
