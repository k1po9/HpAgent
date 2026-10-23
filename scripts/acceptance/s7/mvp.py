"""Finite Research/HTML MVP over normal HTTP and production source discovery.

No fixed candidates, model answers, capability calls or business-state writes.
Each stage requires an independent quality review before the next stage.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from s7.core import MVP_SCENARIOS


def configure_manifest(m, image):
    m.update(profile='research-html-mvp', scenarios=MVP_SCENARIOS, faults=[], discovery='searxng')
    m['effective_governance']['source_discovery'] = 'searxng'
    m['resources']['search_image'] = image(os.environ.get('HPAGENT_MVP_SEARCH_IMAGE', 'searxng/searxng:latest'))
    m['search_settings'] = '''use_default_settings:
  engines:
    keep_only: [google, bing, duckduckgo]
server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "mvp-isolated-search-only"
  limiter: false
  image_proxy: false
search:
  formats: [html, json]
outgoing:
  request_timeout: 15.0
  max_request_timeout: 25.0
engines:
  - name: google
    disabled: false
  - name: bing
    disabled: false
  - name: duckduckgo
    disabled: false
'''
    upstream = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
    if upstream:
        parsed = urlsplit(upstream)
        if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('acceptance upstream proxy must be an explicit credential-free HTTP proxy')
        m['resources']['outbound_proxy'] = upstream
        m['search_settings'] = m['search_settings'].replace('  request_timeout: 15.0',
            '  proxies:\n    all://: [' + json.dumps(upstream) + ']\n  request_timeout: 15.0')
    m['sources'] = [
        dict(url='https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Flexible_box_layout/Basic_concepts',
             locator='one-dimensional model and axes', facts=['Flexbox controls one dimension at a time; row or column.', 'The main axis depends on flex-direction.']),
        dict(url='https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Grid_layout/Relationship_with_other_layout_methods',
             locator='grid and flexbox', facts=['Grid controls rows and columns together.', 'Flexbox wrapping does not align independent lines into a two-dimensional grid.']),
        dict(url='https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control',
             locator='response directives no-cache/no-store/max-age', facts=['no-cache allows storage but requires validation before reuse.', 'no-store directs caches not to store the response.', 'max-age freshness is measured from generation, not arrival.']),
        dict(url='https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Caching',
             locator='validation and revalidation', facts=['A successful conditional validation can return 304 with no response body.', 'private restricts storage to private caches.']),
    ]
    m['sources'].append(dict(url='https://docs.python.org/3/library/asyncio-task.html', locator='TaskGroup and gather', facts=['TaskGroup waits for its tasks when the context exits.']))
    m['egress_hosts'] = ['api.siliconflow.cn', 'developer.mozilla.org', 'docs.python.org']
    m['limits'].update(batch_seconds=14400, scenario_seconds=1800, scenario_tokens=400000)
    # Fixed, bounded topics and interactions; production Agent chooses all tools.
    m['tasks'] = [
        dict(id='research_1', title='CSS layout comparison', category_a='Flexbox', category_b='Grid',
             objective='根据 MDN 官方公开资料研究 CSS Flexbox 与 CSS Grid 的布局区别：维度、主轴和换行限制。只总结本题的关键规则和限制，不扩展 API 清单。',
             query='site:developer.mozilla.org Flexbox Grid one dimensional two dimensional wrapping'),
        dict(id='research_2', title='HTTP cache directives', category_a='no-cache', category_b='no-store',
             objective='根据 MDN 官方公开资料研究 HTTP Cache-Control 的 no-cache、no-store 与 max-age，明确存储和复用验证的区别，以及 private 与 304 的适用限制。',
             query='site:developer.mozilla.org HTTP caching Cache-Control no-cache no-store max-age'),
    ]
    m['precheck_tasks'] = [*m['tasks'], dict(id='python_precheck', objective='研究 Python asyncio TaskGroup 与 gather 的异常和取消规则，并核对 Lock 公平性。仅用 docs.python.org 公开文档。', query='asyncio TaskGroup gather Lock', category_a='TaskGroup', category_b='Lock', domains=['docs.python.org'])]
    m['scheduled_at'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    m['prompt'] = ('这是一个需要后台持续完成的任务：{objective}。请通过正常公开网页搜索发现资料（检索方向：{query}），'
        '仅使用 developer.mozilla.org 官方资料，不使用上传资料。将真实研究内容做成有实际内容与引用链接的自由设计交互 HTML 单页，'
        '用独立 article 卡片展示要点，提供可访问名称恰为“{category_a}”“{category_b}”“全部”的筛选按钮，切换后确实显示/隐藏对应卡片。'
        '请先登记为 '+m['scheduled_at']+'（UTC）执行；我会在选择并授权目录后通过任务要求改为立即执行。每张卡片标题包含对应类别名称，分类筛选互斥。将成果保存到已指定的验收目录（目录标识 {directory}，我会通过任务界面显式授权）。保存是必需条件。资料不足时说明缺口，不编造。离页后继续。')
    m['revision_prompt'] = '只调整这份已成功 HTML 的展示：新增名称恰为“搜索条目”的关键词搜索框，按 article 卡片标题实时筛选；保留全部原研究事实、限制、引用和分类筛选。复用原内容，不重新研究，保留旧版。'
    m['browser_assertion'] = 'Verify declared category filters hide/show cards; revision keyword search filters cards and reset restores them; save, refresh, reopen exact versions.'
    m['clarification'] = '按原目标继续后台任务，保留必需保存和准确引用；只使用 MDN 官方资料，不扩大主题。'
    m['bindings'] = {}



async def source_precheck(manifest, search_url):
    """Unpaid gate: real objectives through product query, discovery and fetch adapters."""
    from research_adapters import (
        CompositeSourceDiscoveryProvider,
        GitHubDiscoveryProvider,
        RSSDiscoveryProvider,
        SearXNGDiscoveryProvider,
        StaticWebContentProvider,
        W3libSourceCanonicalizer,
    )
    from research_adapters.web import discovery_query, freshness_status
    from research_domain.models import SourceStrategy
    from research_domain.excerpts import evidence_spans, missing_concepts

    discovery = CompositeSourceDiscoveryProvider([
        SearXNGDiscoveryProvider(search_url), GitHubDiscoveryProvider(), RSSDiscoveryProvider()])
    content = StaticWebContentProvider(W3libSourceCanonicalizer())
    results = []
    for task in manifest.get('precheck_tasks', manifest['tasks']):
        # Freeze the full natural-language objective; do not use task.query as a
        # special short probe or inject manifest.sources as discovered candidates.
        objective = manifest['prompt'].replace('developer.mozilla.org', task.get('domains', ['developer.mozilla.org'])[0]).format(**task, directory='00000000-0000-0000-0000-000000000001')
        strategy = SourceStrategy(github=False, rss=False, preferred_domains=tuple(task.get('domains', ['developer.mozilla.org'])))
        query = discovery_query(objective, strategy)
        candidates = await discovery.discover(query, strategy=strategy, limit=3)
        fetched = []
        excerpts = []
        for candidate in candidates:
            if (urlsplit(candidate.uri).hostname not in strategy.preferred_domains
                    or freshness_status(candidate, strategy) not in {'confirmed', 'unrestricted'}):
                continue
            try:
                body = await content.fetch(candidate)
                spans = evidence_spans(body.text, task['objective'])
                excerpts.extend(body.text[a:b] for a, b, _ in spans)
                fetched.append(dict(uri=body.canonical_uri, chars=len(body.text), sha256=body.content_hash,
                                    evidence_spans=len(spans), usable=len(body.text) >= content.min_content_chars and bool(spans)))
            except Exception as exc:
                fetched.append(dict(uri=candidate.uri, error=type(exc).__name__, usable=False))
            if (sum(row['usable'] for row in fetched) >= 2 and not missing_concepts(task['objective'], excerpts)) or len(fetched) >= 3:
                break
        results.append(dict(task=task['id'], query=query, source_strategy=strategy.to_dict(),
                            discovery=getattr(candidates, 'diagnostics', {}),
                            candidates=[dict(uri=c.uri, provider=c.provider) for c in candidates], fetched=fetched,
                            missing_concepts=missing_concepts(task['objective'], excerpts),
                            passed=sum(row['usable'] for row in fetched) >= 2 and not missing_concepts(task['objective'], excerpts)))
    return dict(gate_version=2, code_tree_hash=manifest.get('code', {}).get('tree_hash'),
                passed=all(row['passed'] for row in results), tasks=results, model_requests=0)


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'source-precheck':
        import asyncio
        from pathlib import Path
        batch = Path('/batch')
        result = asyncio.run(source_precheck(json.loads((batch / 'manifest.json').read_text()), 'http://searxng:8080'))
        (batch / 'source-precheck.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result['passed'] else 2)
    from s7.mvp_product import main
    main()
