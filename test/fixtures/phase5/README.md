# Durable Work V1 Phase 5 replay histories

Captured from the current production Workflow definitions on 2026-10-02 with
real Temporal and PostgreSQL API/worker roles. Deterministic model responses
investigate three independent directions: A/B succeed, C fails twice. A barrier
in the test proves that the first three contexts execute concurrently.

The histories cover the Agent router, ReAct coordinator, serial plan-and-execute
coordinator and step, delegate tool, bounded fan-out, and isolated child ReAct.
They use the current Execution identity contract. Phase 3's pre-Execution,
pre-strategy lifecycle histories are superseded; this design does not provide
historical production replay compatibility.

Run `pytest -q test/test_agent_segment_replay.py` for offline sandboxed replay.
To recapture after an intentional contract change, set the three test database
role URLs, TEMPORAL_HOST, HPAGENT_MIGRATIONS_DIR and RECORD_PHASE5_HISTORY=1, then
run `pytest -q test/work_domain/test_delegation_temporal.py`. Use an isolated test
database. Only the isolated test queue name is normalized to the deployment
queue; execution identities, inputs, results and commands are captured as-is.
