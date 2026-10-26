"""G1 against an explicitly isolated 1.1.0 service, native mock LLM + embedding fixture.

Never point this test at a business service. It writes fresh account Banks and
exercises destructive maintenance only on those test-owned sources.
"""

import asyncio
import os
import time
from uuid import uuid4

import pytest
from hindsight_client import Hindsight
from hindsight_client_api.models.recall_request import RecallRequest

from memory.hindsight_client import HindsightClient, HindsightRejected
from memory.scope_policy import memory_scope

pytestmark = pytest.mark.skipif(
    os.environ.get("HINDSIGHT_CONTRACT_ISOLATED") != "1",
    reason="requires dedicated Hindsight fixture service and explicit opt-in",
)


def as_dict(value):
    return value.to_dict() if hasattr(value, "to_dict") else value


async def eventually(check, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        value = await check()
        if value:
            return value
        assert time.monotonic() < deadline, "Hindsight contract condition did not converge"
        await asyncio.sleep(0.25)


async def test_g1_real_service_contract():
    url = os.environ["HINDSIGHT_CONTRACT_URL"]
    adapter = HindsightClient(
        url, retain_timeout=60, recall_timeout=30, retain_mission="Preserve explicit user facts."
    )
    accounts = [f"g1-{uuid4()}", f"g1-{uuid4()}"]
    cases = [
        (accounts[0], "private", ""),
        (accounts[0], "group", "A"),
        (accounts[0], "group", "B"),
        (accounts[1], "private", ""),
    ]
    docs, operations = {}, {}
    sdk = Hindsight(url, max_attempts=1)
    try:
        assert (await sdk.aget_version()).api_version == "1.1.0"
        for index, case in enumerate(cases):
            account, scope, group = case
            doc, op = f"g1-source-{index}", str(uuid4())
            docs[case], operations[case] = doc, op
            receipt = await adapter.retain_document(
                [
                    {
                        "role": "user",
                        "content": f"Member{index} prefers violet notebooks and morning tea.",
                    }
                ],
                account,
                doc,
                scope=scope,
                group_id=group,
                iso_timestamp="2026-10-25T00:00:00+00:00",
                async_retain=True,
                operation_id=op,
                metadata={"case": index, "absent": None},
            )
            assert receipt.accepted and receipt.async_processing and receipt.operation_id == op

            async def complete():
                status = await adapter.get_operation(account, op)
                assert status["status"] not in {"failed", "cancelled"}
                return status["status"] == "completed"

            await eventually(complete)
            # Same operation/payload replay is native deduplication, not a new action.
            again = await adapter.retain_document(
                [
                    {
                        "role": "user",
                        "content": f"Member{index} prefers violet notebooks and morning tea.",
                    }
                ],
                account,
                doc,
                scope=scope,
                group_id=group,
                iso_timestamp="2026-10-25T00:00:00+00:00",
                async_retain=True,
                operation_id=op,
                metadata={"case": index, "absent": None},
            )
            assert again.operation_id == op
            document = as_dict(await adapter.get_document(account, doc))
            policy = memory_scope(*case)
            assert document["bank_id"] == policy.bank_id
            assert policy.matches_document(document)

        async def recall(case, types):
            policy = memory_scope(*case)
            response = await sdk.memory.recall_memories(
                policy.bank_id,
                RecallRequest(
                    query="notebooks morning tea",
                    types=types,
                    tags=list(policy.tags),
                    tags_match="all_strict",
                    budget="high",
                    max_tokens=16384,
                    include={"source_facts": {"max_tokens": -1}},
                ),
            )
            return response.to_dict()

        fact_ids = {}
        for case in cases:
            policy = memory_scope(*case)
            facts = await recall(case, ["world", "experience"])
            assert facts["results"], "positive fact control required in every domain"
            fact_ids[case] = {f["id"] for f in facts["results"]}
            assert all(f["document_id"] == docs[case] for f in facts["results"])

            async def observations_ready():
                response = await recall(case, ["observation"])
                return response if response["results"] else None

            observations = await eventually(observations_ready)
            assert observations["source_facts"]
            for result in observations["results"]:
                assert policy.contains(result["tags"])
                assert result["source_fact_ids"]
                for fact_id in result["source_fact_ids"]:
                    source = observations["source_facts"][fact_id]
                    assert source["document_id"] == docs[case]
                    assert policy.contains(source["tags"])
            # Also exercise the production DTO and evidence filter on this real response.
            items = await adapter.recall(
                "notebooks morning tea", case[0], scope=case[1], group_id=case[2], top_n=100
            )
            assert items
            for item in items:
                assert item.bank_id == policy.bank_id
                assert policy.contains(item.tags)
                if item.memory_type == "observation":
                    assert item.source_facts
                    assert all(f["document_id"] == docs[case] for f in item.source_facts)
                else:
                    assert item.source_session_id == docs[case]

        with pytest.raises(HindsightRejected) as retry_completed:
            await adapter.retry_operation(cases[0][0], operations[cases[0]])
        assert retry_completed.value.status == 409

        target = cases[0]
        fact = (await recall(target, ["world"]))["results"][0]
        fact_id = fact["id"]
        await adapter.patch_fact(target[0], fact_id, text="Member0 now prefers green notebooks.")
        current = as_dict(await adapter.get_fact(target[0], fact_id))
        assert "green notebooks" in str(current)
        await adapter.patch_fact(target[0], fact_id, state="invalidated", reason="g1 contract")
        assert fact_id not in {f["id"] for f in (await recall(target, ["world"]))["results"]}
        await adapter.patch_fact(target[0], fact_id, state="valid")
        assert fact_id in {f["id"] for f in (await recall(target, ["world"]))["results"]}
        with pytest.raises(HindsightRejected) as rejected:
            await adapter.get_fact(accounts[1], fact_id)
        assert rejected.value.status == 404

        # Move A's document to B using only the public PATCH, including derived scopes.
        moved, destination = cases[1], cases[2]
        await adapter.patch_document_scope(moved[0], docs[moved], scope="group", group_id="B")
        document = as_dict(await adapter.get_document(moved[0], docs[moved]))
        assert memory_scope(moved[0], 'group', 'B').matches_document(document)
        assert not (await recall(moved, ["world", "experience", "observation"]))["results"]
        destination_facts = (await recall(destination, ["world"]))["results"]
        assert {f["document_id"] for f in destination_facts} == {docs[moved], docs[destination]}

        async def moved_observations_ready():
            response = await recall(destination, ["observation"])
            sources = response.get("source_facts") or {}
            return any(f.get("document_id") == docs[moved] for f in sources.values())

        await eventually(moved_observations_ready)
        await adapter.delete_document(moved[0], docs[moved])
        with pytest.raises(HindsightRejected) as missing:
            await adapter.get_document(moved[0], docs[moved])
        assert missing.value.status == 404
        after = await recall(destination, ["world", "experience", "observation"])
        assert all(f.get("document_id") != docs[moved] for f in after["results"])
        assert all(
            f.get("document_id") != docs[moved] for f in (after.get("source_facts") or {}).values()
        )
        assert all(
            not (set(f.get("source_fact_ids") or ()) & fact_ids[moved])
            for f in after["results"]
        )
        # Terminal operation payload deletion is adapter-only here; no business tombstone flow.
        await adapter.delete_operation(moved[0], operations[moved])
        assert (await adapter.get_operation(moved[0], operations[moved]))["status"] == "not_found"
    finally:
        await sdk.aclose()
        # These fresh banks live only in the throwaway fixture database.


async def test_managed_bank_profile_update_reset_and_candidates():
    from pathlib import Path

    from memory.hindsight_client import HindsightUnavailable
    from memory.quality_config import MemoryQualityConfig, load_memory_prompts

    root = Path(__file__).resolve().parents[1]
    quality = MemoryQualityConfig.from_yaml(root / 'config/hindsight.yaml')
    prompts = load_memory_prompts(root / 'config/prompts/memory.yaml')
    account = f"quality-{uuid4()}"
    adapter = HindsightClient(os.environ['HINDSIGHT_CONTRACT_URL'], quality_config=quality,
                              memory_prompts=prompts, timeout=30.0)
    await adapter.ensure_bank_config(account)
    state = await adapter.get_bank_config(account)
    assert adapter.config_matches(state, quality.bank_updates(prompts))
    assert state['config']['observations_mission'] == prompts['observations_mission']
    modified = quality.model_dump()
    modified['bank']['retain_chunk_size'] = 4000
    changed = MemoryQualityConfig.model_validate(modified)
    adapter.quality_config = changed
    with pytest.raises(HindsightUnavailable, match='not_applied'):
        await adapter.ensure_bank_config(account)
    await adapter.update_bank_config(account, changed.bank_updates(prompts))
    await adapter.ensure_bank_config(account)
    await adapter.update_bank_config(account, {'enable_graph_retrieval': False})
    assert (await adapter.get_bank_config(account))['config']['enable_graph_retrieval'] is False
    await adapter.update_bank_config(account, {'enable_graph_retrieval': None})
    reset = await adapter.get_bank_config(account)
    assert reset['overrides'].get('enable_graph_retrieval') is None
    assert reset['config']['enable_graph_retrieval'] is True
    assert await adapter.recall_candidates('memory', account, scope='private') == []

    operation = str(uuid4())
    document_id = f'quality-document-{uuid4()}'
    receipt = await adapter.retain_document(
        [{'role': 'user', 'content': 'Member0 prefers violet notebooks and morning tea.'}],
        account, document_id, scope='private', iso_timestamp='2026-10-26T00:00:00Z',
        async_retain=True, operation_id=operation, quality_snapshot=adapter.quality_snapshot())
    assert receipt.accepted and receipt.operation_id == operation
    async def completed():
        return (await adapter.get_operation(account, operation))['status'] == 'completed'
    await eventually(completed)
    document = as_dict(await adapter.get_document(account, document_id))
    assert document['id'] == document_id and document['memory_unit_count'] > 0
    assert memory_scope(account, 'private').matches_document(document)
