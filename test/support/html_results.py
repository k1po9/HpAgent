"""Deterministic real HTML facts for isolated acceptance/scheduling tests."""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from agent_activities.capabilities import CapabilityActivities, CapabilityBinding
from agent_activities.store import AgentDataStore
from agent_workflows.capability_contracts import CapabilityClose, CapabilityExecutionInput
from agent_workflows.lifecycle_contracts import SegmentInput
from file_runtime.output import OutputPublisher
from persistence.uow import UnitOfWork
from run_domain.acceptance import ResultAdoptionService
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import AcceptancePredicate, CapabilityRegistry
from run_domain.execution_context import fence_scope
from storage.tenant_file_store import TenantFileStore
from web_artifacts.capability import html_definition, html_scope
from web_artifacts.generator import WebArtifactGenerator
from web_artifacts.local import HtmlActivities


async def evidence(database, account_id, run_id, root, criterion_id):
    account_id, run_id = str(account_id), str(run_id)
    store = AgentDataStore(database)
    caps = CapabilityActivities(
        store,
        CapabilityFacts(CapabilityRegistry((html_definition(),)), services=frozenset({"html"})),
        (CapabilityBinding("html", 1, "HtmlCapabilityWorkflow", html_scope),),
    )

    @contextmanager
    def execution(execution_id=None):
        execution_id = execution_id or store.run_identity(run_id)["execution_id"]
        segment = SegmentInput(2, run_id, account_id, str(uuid4()), execution_id=execution_id)
        token = store.acquire_segment(segment)
        try:
            with fence_scope(account_id, run_id, token, execution_id):
                yield token
        finally:
            store.release_segment(segment)

    operation = f"{run_id}:html:{uuid4()}"
    with execution():
        store.begin_operation(operation, run_id, "tool")
        with UnitOfWork(database) as uow:
            invocation = caps.facts.create(
                uow,
                operation_id=operation,
                capability_key="html",
                contract_version=1,
                inputs={"text": "# Verified output"},
                scope=html_scope({}),
                invocation_limits=html_definition().limits,
            )
    request = CapabilityExecutionInput(
        account_id,
        run_id,
        str(invocation["child_execution_id"]),
        operation,
        "HtmlCapabilityWorkflow",
        600,
        3,
        operation + ":produce",
    )

    class Model:
        async def generate(self, **kwargs):
            return SimpleNamespace(content="<html><body>Verified output</body></html>")

    producer = HtmlActivities(
        caps,
        WebArtifactGenerator(Model()),
        OutputPublisher(database, TenantFileStore(root, max_bytes=2 * 1024 * 1024)),
    )
    with execution(request.execution_id) as token:
        await producer.produce(replace(request, lease_token=token))
    await caps.close(CapabilityClose(request, "succeeded"))
    with execution():
        with UnitOfWork(database) as uow:
            result = caps.facts.read_result(uow, operation)
        return ResultAdoptionService(database, caps.facts).evidence(
            criterion_id, AcceptancePredicate("output_exists", result["outputs"][0]["ref_id"])
        )
