"""Synthetic-only P3 collector/metrics. Run client in the isolated checks image.

The real SDK, Adapter and ContextAssembly execute. Local readable-document state
and context binding are fixtures: this is NOT a persistence/fencing integration
or real-model quality benchmark. Full server traces stay in process memory.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

DATASET = Path('test/fixtures/hindsight-pgroonga/evaluation/dataset.json')
STAMP = '2026-10-25T00:00:00+00:00'


def identity(session, key):
    return uuid5(NAMESPACE_URL, 'hpagent-pgroonga-p3/' + session + '/' + key)


def rank_metrics(ranked, relevance, k):
    """Document-level, unique results; failed queries remain zero in denominator."""
    ranked = list(dict.fromkeys(ranked))[:k]
    relevant = {d for d, grade in relevance.items() if grade > 0}
    if not relevant:
        return {'recall': None, 'mrr': None, 'ndcg': None,
                'false_injection': float(bool(ranked))}
    recall = len(set(ranked) & relevant) / len(relevant)
    mrr = next((1 / (i + 1) for i, d in enumerate(ranked) if d in relevant), 0.0)
    dcg = sum((2 ** relevance.get(d, 0) - 1) / math.log2(i + 2)
              for i, d in enumerate(ranked))
    ideal = sum((2 ** g - 1) / math.log2(i + 2)
                for i, g in enumerate(sorted(relevance.values(), reverse=True)[:k]))
    return {'recall': recall, 'mrr': mrr, 'ndcg': dcg / ideal, 'false_injection': None}


def stage_metrics(items, relevance, k):
    """Cut off at k memory results before expanding Observation source documents."""
    items = items[:k]
    relevant = {d for d, grade in relevance.items() if grade > 0}
    if not relevant:
        return {'recall': None, 'mrr': None, 'ndcg': None,
                'false_injection': float(bool(items))}
    found = set()
    first = 0.0
    dcg = 0.0
    for rank, item in enumerate(items, 1):
        docs = set(item.get('source_documents', [item['document_id']]))
        novel = (docs & relevant) - found
        if novel:
            first = first or 1 / rank
            dcg += (2 ** max(relevance[d] for d in novel) - 1) / math.log2(rank + 1)
        found.update(docs & relevant)
    ideal = sum((2 ** g - 1) / math.log2(i + 2)
                for i, g in enumerate(sorted(relevance.values(), reverse=True)[:k]))
    return {'recall': len(found) / len(relevant), 'mrr': first,
            'ndcg': dcg / ideal, 'false_injection': None}


def safe_trace(trace, mapping):
    """Whitelist rank/score/identity only, rejecting unknown fact identities."""
    stages = {'keyword': [], 'rrf': [], 'rerank': []}
    for arm in trace.get('retrieval_results', []):
        if arm['method_name'] == 'bm25':
            for r in arm['results']:
                stages['keyword'].append(dict(fact_id=r['node_id'],
                    document_id=mapping[r['node_id']], rank=r['rank'], score=r['score'],
                    arm='bm25', fact_type=arm.get('fact_type')))
    for key, target, rank, score in [('rrf_merged', 'rrf', 'final_rrf_rank', 'rrf_score'),
                                     ('reranked', 'rerank', 'rerank_rank', 'rerank_score')]:
        stages[target] = [dict(fact_id=r['node_id'], document_id=mapping[r['node_id']],
                              rank=r[rank], score=r[score], arm=target)
                          for r in trace.get(key, [])]
    return stages


async def collect(args):
    from hindsight_client import Hindsight

    from application.context_assembly import ContextAssemblyService, WebContextBase
    from application.context_builder import HarnessContextBuilder
    from memory.hindsight_client import HindsightClient

    assert args.url == 'http://hindsight:8888', 'only isolated network alias supported'
    data = json.loads(DATASET.read_text())
    protocol = json.loads(Path(args.protocol).read_text())
    assert protocol['dataset_sha256'] == hashlib.sha256(DATASET.read_bytes()).hexdigest()
    assert protocol['session_id'] == args.session
    account = identity(args.session, 'main')
    mapping = {str(identity(args.session, d['document_id'])): d['document_id']
               for d in data['documents']}
    for o in data['observations']:
        mapping[str(identity(args.session, o['fact_id']))] = o['fact_id']
    readable = {d['document_id'] for d in data['documents']
                if d['readable'] and d['account'] == 'main' and d['tags'] == ['scope:private']}
    adapter = HindsightClient(args.url, recall_timeout=10)
    captured = {}

    @asynccontextmanager
    async def sdk_context(**kwargs):
        sdk = Hindsight(args.url, max_attempts=1)

        async def recall(bank, body, **kw):
            body.trace = True
            response = await sdk.memory.recall_memories(bank, body, **kw)
            captured['response'] = response.to_dict()
            return response
        try:
            yield SimpleNamespace(memory=SimpleNamespace(recall_memories=recall))
        finally:
            await sdk.aclose()

    adapter._sdk = sdk_context
    service = ContextAssemblyService('fixture-only', HarnessContextBuilder(), adapter)
    base = WebContextBase(run_id=identity(args.session, 'run'), account_id=account,
        conversation_id=identity(args.session, 'conversation'), session_id=None,
        execution_id=identity(args.session, 'execution'), context_message_seq=1,
        trigger_message_id=identity(args.session, 'message'), trigger_content='synthetic',
        short_term_events=(), trigger_created_at=STAMP)
    # Only local DB/fence state is substituted; actual Adapter source checks and
    # ContextAssembly authorization-before-top_n remain the production code.
    uow = MagicMock()
    uow.execute.return_value.fetchone.return_value = {
        'memory_context': {'epoch': 0, 'scopes': [], 'documents': []}}
    counts = {}
    original = adapter.recall_candidates

    async def candidates(*a, **kw):
        result = await original(*a, **kw)
        counts.update(remote=result.remote_count, incomplete=result.incomplete_sources,
                      candidates=len(result))
        return result
    adapter.recall_candidates = candidates
    patches = [patch('application.context_assembly.MemoryStore.readable_documents', return_value=readable),
               patch.object(service, 'bind_base'),
               patch('application.context_assembly.UnitOfWork', return_value=MagicMock(
                   __enter__=MagicMock(return_value=uow))),
               patch('persistence.memory_context.check_manifest'),
               patch('persistence.memory_context.bind_context'),
               patch('run_domain.execution.assert_execution_fence')]
    for p in patches:
        p.start()
    try:
        for q in data['queries']:
            captured.clear()
            counts.clear()
            start = time.perf_counter()
            row = dict(case_id=q['case_id'], group=q['group'], batch=args.batch,
                       backend=args.arm, cache='first_pass' if args.batch == 0 else 'warm',
                       error_code=None, stages={})
            try:
                selected = await service.recall_long_term(base, q['frozen_rewrite'])
                raw = captured.get('response')
                assert raw is not None and counts, 'backend_or_adapter_failure'
                assert raw.get('trace'), 'trace_missing'
                row['stages'] = safe_trace(raw['trace'], mapping)
                row['stages']['remote'] = [dict(fact_id=r['id'],
                    document_id=mapping[r['id']], rank=i + 1, arm='remote')
                    for i, r in enumerate(raw['results'])]
                row['stages']['final'] = [dict(fact_id=r.memory_id,
                    document_id=mapping[r.memory_id], rank=i + 1, arm='final')
                    for i, r in enumerate(selected)]
                # Observation source set contributes docs only when complete/authorized.
                for r in row['stages']['final']:
                    obs = next((o for o in data['observations']
                                if o['fact_id'] == r['document_id']), None)
                    r['source_documents'] = obs['sources'] if obs else [r['document_id']]
                row['counts'] = dict(hindsight=counts['remote'],
                    incomplete_sources=counts['incomplete'],
                    permission_rejected=counts['candidates'] - len([
                        r for r in raw['results'] if r.get('document_id') in readable]),
                    selected=len(selected))
                # Recompute rejection with the same document-set requirement, including observations.
                accepted = 0
                sources = raw.get('source_facts') or {}
                for r in raw['results']:
                    if r.get('type') == 'observation':
                        ids = r.get('source_fact_ids') or []
                        if ids and all(x in sources for x in ids):
                            accepted += all(sources[x].get('document_id') in readable for x in ids)
                    else:
                        accepted += r.get('document_id') in readable
                row['counts']['permission_rejected'] = counts['candidates'] - accepted
                for r in selected:
                    assert r.bank_id == 'hpagent-u-' + str(account)
                    assert set(r.tags) == {'scope:private'}
                    docs = {s['document_id'] for s in r.source_facts} if r.memory_type == 'observation' else {r.source_session_id}
                    assert docs and docs <= readable, 'authorization_leak'
            except Exception as exc:
                # Never serialize exception text: SDK exceptions can contain response bodies.
                row['error_code'] = type(exc).__name__
            row['duration_ms'] = (time.perf_counter() - start) * 1000
            print(json.dumps(row), flush=True)
    finally:
        for p in reversed(patches):
            p.stop()


def summarize(args):
    data = json.loads(DATASET.read_text())
    gold = {q['case_id']: q for q in data['queries']}
    rows = [json.loads(line) for line in Path(args.results).read_text().splitlines()]
    output = {}
    for arm in ['native', 'candidate']:
        group_output = {}
        for group in ['all', 'english', 'mixed_chinese'] + sorted({q['group'] for q in data['queries']}):
            subset = [r for r in rows if r['backend'] == arm and (
                group == 'all' or r['group'] == group or
                (group == 'english' and r['group'] == 'mixed' and int(r['case_id'][-2:]) < 5) or
                (group == 'mixed_chinese' and r['group'] == 'mixed' and int(r['case_id'][-2:]) >= 5))]
            metrics = {}
            for stage in ['keyword', 'rrf', 'rerank', 'remote', 'final']:
                for k in [5, 10]:
                    vals = []
                    for r in subset:
                        items = r['stages'].get(stage, [])
                        if stage == 'keyword':
                            items = [i for i in items if i['fact_type'] == 'world']
                        vals.append(stage_metrics(items, gold[r['case_id']]['relevance'], k))
                    metrics[stage + '@' + str(k)] = {
                        key: sum(v[key] for v in vals if v[key] is not None) / len([v for v in vals if v[key] is not None])
                        if any(v[key] is not None for v in vals) else None
                        for key in ['recall', 'mrr', 'ndcg', 'false_injection']}
            durations = sorted(r['duration_ms'] for r in subset)
            group_output[group] = dict(requests=len(subset), errors=sum(bool(r['error_code']) for r in subset),
                p50_ms=durations[math.ceil(len(durations)*.5)-1],
                p95_ms=durations[math.ceil(len(durations)*.95)-1], metrics=metrics)
        output[arm] = group_output
    expected = {(arm, b, q['case_id']) for arm in ['native','candidate'] for b in range(3) for q in data['queries']}
    actual = [(r['backend'],r['batch'],r['case_id']) for r in rows]
    output['coverage'] = dict(expected=len(expected), actual=len(actual),
                              complete=len(actual)==len(set(actual)) and set(actual)==expected)
    output['limitations'] = ['constant embedding / mock LLM / rrf; no semantic quality claim',
        'local readable state and context binding are fixtures; no real PG authorization/fence proof',
        'first_pass is not OS/database cold cache; no cache eviction or service restart',
        'single synthetic template per group; repeats are not independent quality samples',
        'no downstream answering model; false injection is context selection, not refusal']
    Path(args.output).write_text(json.dumps(output, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['collect','summarize'])
    parser.add_argument('--session')
    parser.add_argument('--arm', choices=['native','candidate'])
    parser.add_argument('--batch', type=int, choices=range(3))
    parser.add_argument('--url')
    parser.add_argument('--protocol')
    parser.add_argument('--results')
    parser.add_argument('--output')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.action == 'collect':
        asyncio.run(collect(args))
    else:
        summarize(args)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
    main()
