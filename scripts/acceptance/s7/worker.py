"""Dedicated S7 composition; production entrypoints never import these hooks."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from functools import wraps
from pathlib import Path

from s7.core import Ledger, Rejected, request_bound

BATCH = Path('/batch')


def sample():
    return (BATCH / 'active-sample').read_text().strip()


def has_committed_html(capabilities, child):
    from persistence.uow import UnitOfWork
    with UnitOfWork(capabilities.store.database_url) as uow:
        row = uow.execute('SELECT payload FROM capability_results WHERE account_id=%s AND invocation_id=%s',
                          (child.account_id, child.invocation_id)).fetchone()
    return bool(row and row['payload']['outcome'] == 'succeeded' and
                any(r['type'] == 'artifact_version' for r in row['payload']['outputs']))


def target_work(context, *, require_research=False):
    marker = BATCH / f'{sample()}.work.json'
    if not marker.exists():
        return False
    selected = json.loads(marker.read_text())['work_id']
    from persistence.uow import UnitOfWork
    with UnitOfWork(os.environ['WORKER_DATABASE_URL']) as uow:
        run = uow.execute('SELECT source_kind,work_id,account_id FROM runs WHERE run_id=%s', (context.run_id,)).fetchone()
        if not run or run['source_kind'] != 'work' or str(run['work_id']) != selected:
            return False
        if require_research:
            return bool(uow.execute("SELECT 1 FROM capability_results cr JOIN capability_invocations i USING(account_id,invocation_id) "
                "JOIN runs r ON r.account_id=cr.account_id AND r.run_id=cr.run_id WHERE r.work_id=%s "
                "AND r.account_id=%s AND i.capability_key='research' AND cr.payload->>'outcome'='succeeded' LIMIT 1",
                (run['work_id'], run['account_id'])).fetchone())
    return True


def install_hooks(manifest, ledger):
    from resources.model_budget_context import current_model_call
    from resources.model_client import ModelClient, canonical_request_json
    original_send = ModelClient.send_prepared
    import httpx
    original_post = httpx.AsyncClient.post
    receipts = {}

    @wraps(original_post)
    async def observed_post(self, url, *args, **kwargs):
        response = await original_post(self, url, *args, **kwargs)
        if str(url) == manifest['model']['base_url'] + '/chat/completions':
            context = current_model_call()
            if context and context.snapshot_id:
                receipt = dict(http_status=response.status_code,
                    provider_request_id=response.headers.get('x-request-id') or response.headers.get('request-id'),
                    raw_response_sha256=hashlib.sha256(response.content).hexdigest())
                try:
                    data = response.json()
                    receipt['provider_response_id'] = str(data.get('id', ''))[:200]
                    receipt['provider_usage'] = {k: v for k, v in (data.get('usage') or {}).items()
                        if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and type(v) is int and v >= 0}
                except (ValueError, AttributeError):
                    receipt['provider_usage'] = {}
                folder = BATCH / 'model-responses'
                folder.mkdir(exist_ok=True, mode=0o700)
                raw = response.content
                authorization = (kwargs.get('headers') or {}).get('Authorization', '')
                if authorization.startswith('Bearer '):
                    raw = raw.replace(authorization[7:].encode(), b'[REDACTED]')
                (folder / (str(context.snapshot_id) + '.json')).write_bytes(raw)
                receipt['stored_response_sha256'] = hashlib.sha256(raw).hexdigest()
                receipts[str(context.snapshot_id)] = receipt
                ledger.event('provider_http_response', dict(snapshot_id=str(context.snapshot_id), **receipt))
        return response

    httpx.AsyncClient.post = observed_post

    async def before_dispatch(self, prepared):
        from resources.model_failures import ModelRequestInvalid
        prepared.verify_immutable()
        body = prepared.body()
        context = current_model_call()
        if not context or not context.snapshot_id:
            raise ModelRequestInvalid('acceptance requires a frozen governed model call')
        if (prepared.provider, prepared.model, prepared.url) != (
                manifest['model']['provider'], manifest['model']['id'], manifest['model']['base_url'] + '/chat/completions'):
            raise ModelRequestInvalid('acceptance model identity mismatch')
        if body.get('stream') or body.get('enable_thinking') is not False:
            raise ModelRequestInvalid('acceptance requires bounded non-thinking, non-stream text')
        maximum = body.get('max_tokens')
        if type(maximum) is not int or not 0 < maximum <= manifest['model']['max_output_tokens']:
            raise ModelRequestInvalid('output upper bound missing')
        # UTF-8 byte count plus per-message/tool framing allowance is deliberately
        # pessimistic for this authorized tokenizer. Unknown usage retains all of it.
        bound = request_bound(body)
        identity = str(context.snapshot_id)
        corr = dict(account_id=str(context.account_id), run_id=str(context.run_id),
                    operation_id=context.operation_id, snapshot_id=identity,
                    phase=context.phase, request_sha256=hashlib.sha256(canonical_request_json(body)).hexdigest())
        from run_domain.execution_context import execution_fence
        fence = execution_fence.get()
        if fence is not None:
            corr.update(execution_id=str(fence[2]), fencing_token=fence[3])
            if context.phase == 'artifact_generation':
                from persistence.uow import UnitOfWork
                with UnitOfWork(os.environ['WORKER_DATABASE_URL']) as uow:
                    invocation = uow.execute('SELECT invocation_id FROM capability_invocations WHERE account_id=%s AND run_id=%s AND child_execution_id=%s',
                        (context.account_id, context.run_id, fence[2])).fetchone()
                if invocation:
                    corr['invocation_id'] = invocation['invocation_id']
        current = sample()
        try:
            ledger.reserve(identity, current, bound, corr)
        except Rejected as exc:
            raise ModelRequestInvalid(str(exc)) from exc
        pending[identity] = (current, corr)

    pending = {}

    @wraps(original_send)
    async def send(self, prepared, on_text_delta=None):
        from resources.model_failures import ModelRequestInvalid
        context = current_model_call()
        identity = str(context.snapshot_id) if context else ''
        guarded = pending.pop(identity, None)
        if guarded is None:
            raise ModelRequestInvalid('acceptance requires pre-dispatch allocation')
        current, corr = guarded
        ledger.event('provider_adapter_enter', corr)
        try:
            result = await asyncio.wait_for(original_send(self, prepared, on_text_delta), timeout=manifest['limits']['request_seconds'])
            receipt = receipts.pop(identity, {})
            usage = dict(result.usage)
            if type(receipt.get('provider_usage', {}).get('total_tokens')) is int:
                usage.update(total_tokens=receipt['provider_usage']['total_tokens'], usage_source='provider')
            ledger.settle(identity, usage)
            ledger.event('provider_adapter_completed', {**corr, 'usage': usage, 'provider_receipt': receipt,
                'response_sha256': hashlib.sha256((result.content or '').encode()).hexdigest(),
                'tool_names': [t.name for t in (result.tool_calls or [])]})
        except BaseException:
            ledger.uncertain(identity)
            raise
        # Raw candidate changes before WebArtifactGenerator.harden, after real usage.
        if current.startswith('M3-') and context.phase == 'artifact_generation' and await asyncio.to_thread(target_work, context, require_research=True):
            if ledger.consume(current, corr, 'invalid_html'):
                import re
                complete = bool(re.search(r'<html(?:\s|>)', result.content or '', re.I) and re.search(r'</html\s*>', result.content or '', re.I))
                ledger.event('candidate_corrupted', {**corr, 'sample': current, 'original_complete_html': complete})
                result.content = '<html><head></head><body>incomplete candidate'
        return result

    # No optional recall rewrite when this isolated composition disables memory.
    from dataclasses import replace

    from application.chat_execution import PostgresWebRequestLoader
    original_load = PostgresWebRequestLoader.load

    @wraps(original_load)
    async def without_memory(self, run_id):
        loaded = await original_load(self, run_id)
        return replace(loaded, context_provider=None)

    PostgresWebRequestLoader.load = without_memory

    ModelClient.before_dispatch = before_dispatch
    ModelClient.send_prepared = send

    # Supply the frozen, real public documentation candidates through the normal
    # discovery interface. Content, citations, synthesis, budgets and publication
    # remain the production implementation; source_strategy is still honored.
    from research_adapters import CompositeSourceDiscoveryProvider, StaticWebContentProvider
    from research_domain.models import SourceCandidate
    discovery = CompositeSourceDiscoveryProvider.discover

    @wraps(discovery)
    async def approved_discovery(self, query, *, strategy, limit, original_question=None):
        if not (strategy.public_web or strategy.official_sources):
            return []
        return [SourceCandidate(uri=s['url'], title=s['locator'], provider='approved_public_documents',
                metadata={'preferred_domain': True}) for s in manifest['sources']][:limit]

    if manifest.get('profile') != 'research-html-mvp':
        CompositeSourceDiscoveryProvider.discover = approved_discovery
    else:
        @wraps(discovery)
        async def observed_discovery(self, query, *, strategy, limit, original_question=None):
            results = await discovery(self, query, strategy=strategy, limit=limit,
                **({'original_question': original_question} if original_question is not None else {}))
            from research_adapters.web import discovery_query
            ledger.event('source_discovered', dict(sample=sample(), query=query, effective_query=discovery_query(query, strategy),
                candidates=[dict(uri=c.uri, provider=c.provider) for c in results]))
            return results
        CompositeSourceDiscoveryProvider.discover = observed_discovery
    fetch = StaticWebContentProvider.fetch

    @wraps(fetch)
    async def record_fetch(self, candidate, *args, **kwargs):
        result = await fetch(self, candidate, *args, **kwargs)
        content = result.text.encode('utf-8')
        sha = hashlib.sha256(content).hexdigest()
        folder = BATCH / 'source-bytes'
        folder.mkdir(exist_ok=True)
        (folder / sha).write_bytes(content)
        ledger.event('source_fetched', dict(sample=sample(), uri=result.canonical_uri,
                    at=time.time(), sha256=sha, content_hash=result.content_hash))
        return result

    StaticWebContentProvider.fetch = record_fetch
    from agent_activities.capabilities import CapabilityActivities
    close = CapabilityActivities.close

    @wraps(close)
    async def after_close(self, request):
        result = await close(self, request)
        current = sample()
        child = request.child
        # close() has returned: transaction and capability lease already released.
        # Parent has not acquired its observation segment yet.
        if current.split('-')[0] in {'M4', 'M5', 'M6'} and child.workflow_name == 'HtmlCapabilityWorkflow' and await asyncio.to_thread(target_work, child) and await asyncio.to_thread(has_committed_html, self, child):
            target = dict(run_id=child.run_id, invocation_id=child.invocation_id,
                          execution_id=child.execution_id, operation_id=child.operation_id)
            if ledger.consume(current, target, 'after_html_close'):
                marker = BATCH / f'{current}.barrier.json'
                marker.write_text(json.dumps({**target, 'at': time.time()}))
                ledger.event('barrier_reached', {**target, 'sample': current})
                deadline = time.monotonic() + 20  # below close Activity's unchanged 30s timeout
                while not (BATCH / f'{current}.release').exists():
                    if time.monotonic() >= deadline:
                        raise Rejected('controller missed declared barrier; sample not covered')
                    await asyncio.sleep(0.1)
        return result

    CapabilityActivities.close = after_close

    from agent_activities.segments import SegmentActivities
    release = SegmentActivities.release

    @wraps(release)
    async def after_release(self, request):
        result = await release(self, request)
        current = sample()
        flag = BATCH / f'{current}.resume-requested'
        if current.startswith('M5-') and flag.exists():
            original = json.loads((BATCH / f'{current}.barrier.json').read_text())
            if request.run_id != original['run_id'] and await asyncio.to_thread(target_work, request):
                target = dict(run_id=request.run_id, execution_id=request.execution_id,
                              segment_id=request.segment_id)
                if ledger.consume(current, target, 'resume_segment_release'):
                    (BATCH / f'{current}.resumed-barrier.json').write_text(json.dumps(target))
                    deadline = time.monotonic() + 20
                    while not (BATCH / f'{current}.resumed-release').exists():
                        if time.monotonic() >= deadline:
                            raise Rejected('resumed segment controller missed barrier')
                        await asyncio.sleep(0.1)
        return result

    SegmentActivities.release = after_release


async def main():
    from temporalio.client import Client

    from bootstrap.durable_runtime import compose_durable_runtime
    from bootstrap.worker_dependencies import init_dependencies
    from orchestration.config import AppConfig
    from orchestration.worker_tasks import BackgroundTasks, start_durable_background_tasks
    manifest = json.loads((BATCH / 'manifest.json').read_text())
    ledger = Ledger(Path('/campaign/ledger.sqlite'), manifest)
    # A prior process may have died after dispatch. Never infer absence of cost.
    with ledger.connect() as db:
        db.execute('UPDATE sends SET state="uncertain" WHERE state="sending"')
    install_hooks(manifest, ledger)
    from s7.composition import configure_governance
    configure_governance(manifest)
    config = AppConfig.from_yaml('/batch/app.json', '/batch/models.json')
    deps = await init_dependencies(config)
    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                client = await Client.connect('temporal:7233', namespace='default')
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(1)
        runtime = compose_durable_runtime(client, config, deps)
        tasks = BackgroundTasks()
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(runtime.workers.lifecycle)
            await stack.enter_async_context(runtime.workers.agent)
            start_durable_background_tasks(tasks=tasks, web_dispatcher=runtime.dispatcher,
                web_reconciler=runtime.reconciler, web_memory_retention=None,
                lease_timeout_seconds=config.temporal.web_outbox_lease_timeout_seconds,
                recovery_interval_seconds=config.temporal.web_outbox_recovery_interval_seconds)
            stack.push_async_callback(tasks.close)
            (BATCH / 'worker-ready').write_text(str(os.getpid()))
            await asyncio.Event().wait()
    finally:
        await deps.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
