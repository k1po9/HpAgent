#!/usr/bin/env python3
"""
模型 API 延迟与成功率测试 —— 覆盖 models.yaml 中所有文本模型类别。

用法:
    python scripts/test-models.py                     # 测试所有类别，每模型 5 次
    python scripts/test-models.py -n 10               # 每模型 10 次
    python scripts/test-models.py -c fast             # 只测 fast 类
    python scripts/test-models.py -c fast,chat        # 只测 fast + chat
    python scripts/test-models.py --native            # 使用项目 ModelClient（默认直连 API）
    python scripts/test-models.py --stream            # 流式模式

两种调用模式:
  - 直连模式 (默认):  直接 httpx 调 API，无项目依赖，轻量
  - 项目模式 (--native): 使用 src/resources/model_client.py 的 ModelClient，与生产一致

两者底层都是 httpx POST，延迟由 API Server 决定，客户端开销可忽略。
项目模式额外测试了 error wrapping 和格式转换路径。
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import httpx
import yaml

# ── 项目根目录 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# 文本类模型类别（embedding / image 走不同 API，跳过）
TEXT_CATEGORIES = ("fast", "chat", "reasoning")

# ── 测试用例：简短摘要，模拟 fast/chat 模型常见负载 ──
TEST_CASE = {
    "system": "你是一个文本摘要助手，只做简洁的摘要，不要添加额外评论。",
    "user": (
        "请用不超过 3 句话总结以下文本的要点。\n\n"
        "文本：今天天气很好，我和朋友去公园散步。公园里的樱花开了，"
        "很多人都在拍照。我们还看到了一只松鼠在树上跳来跳去。"
        "中午在附近的餐厅吃了午饭，味道很不错。下午回到家后，"
        "我开始整理昨天的工作文件，发现有几个地方需要修改。"
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════════════════════

def load_env() -> Dict[str, str]:
    """加载 .env，写入 os.environ 供 ${VAR} 解析。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        print(f"[WARN] .env not found at {env_path}")
        return {}
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


def _sub_env(s: str) -> str:
    return re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), s)


def load_all_models() -> Dict[str, List[Dict[str, Any]]]:
    """读取 models.yaml，按类别返回模型列表（仅文本类）。"""
    config_path = ROOT / "config" / "models.yaml"
    raw = config_path.read_text(encoding="utf-8")
    raw = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), raw)
    cfg = yaml.safe_load(raw)
    providers = cfg.get("providers", {})

    result: Dict[str, List[Dict[str, Any]]] = {}
    for cat in TEXT_CATEGORIES:
        models = cfg.get(cat, [])
        if not models:
            continue
        resolved = []
        for m in models:
            p_name = m.get("provider", "")
            p = providers.get(p_name, {})
            resolved.append({
                "category": cat,
                "model": m["model"],
                "provider": p_name,
                "base_url": _sub_env(p.get("base_url", "")).rstrip("/"),
                "api_key": _sub_env(p.get("api_key", "")),
                "api_format": p.get("api_format", "openai"),
                "max_tokens": m.get("max_tokens", 1024),
                "timeout": m.get("timeout", 30.0),
                "extra_body": m.get("extra_body") or {},
            })
        if resolved:
            result[cat] = resolved
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 模式 A：直连 API（httpx 裸调）
# ═══════════════════════════════════════════════════════════════════════════════

async def call_direct(
    client: httpx.AsyncClient,
    cfg: Dict[str, Any],
    stream: bool = False,
) -> Dict[str, Any]:
    """直接用 httpx 调 OpenAI/Anthropic 兼容 API。"""
    fmt = cfg["api_format"]
    t0 = time.monotonic()

    try:
        if fmt == "openai":
            url = f"{cfg['base_url']}/chat/completions"
            headers = {
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            }
            payload: Dict[str, Any] = {
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": TEST_CASE["system"]},
                    {"role": "user", "content": TEST_CASE["user"]},
                ],
                "max_tokens": cfg["max_tokens"],
                "stream": stream,
            }
        else:
            url = f"{cfg['base_url']}/messages"
            headers = {
                "x-api-key": cfg["api_key"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            payload = {
                "model": cfg["model"],
                "system": TEST_CASE["system"],
                "messages": [{"role": "user", "content": TEST_CASE["user"]}],
                "max_tokens": cfg["max_tokens"],
                "stream": stream,
            }

        if cfg["extra_body"]:
            payload.update(cfg["extra_body"])

        resp = await client.post(url, json=payload, headers=headers, timeout=cfg["timeout"])

        if stream:
            content = ""
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta", {})
                content += delta.get("content", "")
            body = None
        else:
            body = resp.json()
            if fmt == "openai":
                content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
            else:
                content = "".join(
                    b.get("text", "") for b in (body.get("content") or [])
                    if b.get("type") == "text"
                )

        latency_ms = (time.monotonic() - t0) * 1000
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "tokens": _extract_usage(body, fmt),
            "content_len": len(content),
        }

    except httpx.HTTPStatusError as e:
        return {
            "ok": False,
            "latency_ms": (time.monotonic() - t0) * 1000,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": (time.monotonic() - t0) * 1000,
            "error": f"{type(e).__name__}: {e}",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 模式 B：项目 ModelClient
# ═══════════════════════════════════════════════════════════════════════════════

async def call_native(
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """使用项目的 ModelClient 调用（与生产一致）。"""
    from resources.model_client import ModelClient
    from common.errors import ModelAPIError

    client_cfg = {
        "api_key": cfg["api_key"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_format": cfg["api_format"],
        "max_tokens": cfg["max_tokens"],
        "timeout": cfg["timeout"],
        "extra_body": cfg["extra_body"],
    }

    mc = ModelClient(client_cfg)
    t0 = time.monotonic()
    try:
        resp = await mc.generate(
            messages=[
                {"role": "system", "content": TEST_CASE["system"]},
                {"role": "user", "content": TEST_CASE["user"]},
            ],
            stream=False,
        )
        latency_ms = (time.monotonic() - t0) * 1000
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "content_len": len(resp.content or ""),
        }
    except ModelAPIError as e:
        return {
            "ok": False,
            "latency_ms": (time.monotonic() - t0) * 1000,
            "error": str(e),
        }
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": (time.monotonic() - t0) * 1000,
            "error": f"{type(e).__name__}: {e}",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_usage(body: Optional[dict], fmt: str) -> Optional[Dict[str, int]]:
    if not body:
        return None
    usage = body.get("usage")
    if not usage:
        return None
    if "input_tokens" in usage:
        return {"in": usage["input_tokens"], "out": usage["output_tokens"]}
    if "prompt_tokens" in usage:
        return {"in": usage["prompt_tokens"], "out": usage["completion_tokens"]}
    return None


def _stats(results: List[Dict]) -> Dict[str, Any]:
    oks = [r for r in results if r["ok"]]
    fails = [r for r in results if not r["ok"]]
    lats = [r["latency_ms"] for r in oks]
    total = len(results)
    rate = len(oks) / total * 100 if total else 0
    if not lats:
        return {"oks": len(oks), "total": total, "rate": rate,
                "fails": len(fails), "fail_details": fails}
    s = sorted(lats)
    return {
        "oks": len(oks), "total": total, "rate": rate,
        "avg": sum(lats) / len(lats),
        "p50": s[len(s) // 2],
        "p95": s[int(len(s) * 0.95)] if len(s) > 1 else s[0],
        "min": s[0], "max": s[-1],
        "fails": len(fails), "fail_details": fails,
        "latencies": lats,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════

async def run(
    models_by_cat: Dict[str, List[Dict]],
    n: int,
    *,
    native: bool = False,
    stream: bool = False,
):
    mode = "项目 ModelClient" if native else "直连 API"
    stream_note = " (stream)" if stream else ""
    print(f"\n{'='*75}")
    print(f"模型延迟测试 — 每模型 {n} 次 — {mode}{stream_note}")
    print(f"{'='*75}")

    # 收集所有结果用于汇总
    all_stats: List[Tuple[str, str, str, Dict]] = []  # (cat, provider, model, stats)

    for cat, models in models_by_cat.items():
        print(f"\n{'─'*75}")
        print(f"【{cat}】")
        print(f"{'─'*75}")

        for idx, m in enumerate(models):
            label = f"{m['provider']}:{m['model']}"
            print(f"  [{cat} {idx+1}/{len(models)}] {label}")

            results = []
            async with httpx.AsyncClient() as client:
                for i in range(n):
                    bar = "▌" * (i + 1) + " " * (n - i - 1)
                    sys.stdout.write(f"    [{bar}] {i+1}/{n}\r")
                    sys.stdout.flush()

                    if native:
                        r = await call_native(m)
                    else:
                        r = await call_direct(client, m, stream=stream)
                    results.append(r)

            sys.stdout.write(" " * 40 + "\r")
            st = _stats(results)
            all_stats.append((cat, m["provider"], m["model"], st))

            ok_mark = "✓" if st["rate"] == 100 else ("⚠" if st["rate"] >= 50 else "✗")
            print(f"    {ok_mark} 成功率: {st['oks']}/{st['total']} ({st['rate']:.0f}%)")

            if st.get("avg") is not None:
                print(f"      延迟: avg={st['avg']:.0f}ms  p50={st['p50']:.0f}ms  "
                      f"p95={st['p95']:.0f}ms  min={st['min']:.0f}ms  max={st['max']:.0f}ms")

            for r in st.get("fail_details", []):
                print(f"      ✗ {r['latency_ms']:.0f}ms: {r['error'][:100]}")

            lats = st.get("latencies", [])
            if lats:
                vals = " | ".join(f"{v:.0f}ms" for v in lats)
                print(f"      各次: {vals}")
            print()

    # ── 汇总表 ──
    print(f"{'='*75}")
    print(f"{'类别':<10} {'模型':<48} {'成功率':>6} {'avg':>7} {'p50':>7} {'p95':>7}")
    print(f"{'-'*75}")
    for cat, prov, model, st in all_stats:
        name = f"{prov}:{model}"[:48]
        if st.get("avg") is not None:
            print(f"{cat:<10} {name:<48} {st['rate']:>5.0f}% {st['avg']:>6.0f}ms "
                  f"{st['p50']:>6.0f}ms {st['p95']:>6.0f}ms")
        else:
            print(f"{cat:<10} {name:<48} {st['rate']:>5.0f}% {'N/A':>7} {'N/A':>7} {'N/A':>7}")
    print(f"{'='*75}")


def main():
    parser = argparse.ArgumentParser(description="模型 API 延迟与成功率测试")
    parser.add_argument("-n", type=int, default=5, help="每模型测试次数 (default: 5)")
    parser.add_argument("-c", "--categories", default="fast,chat,reasoning",
                        help="测试的模型类别，逗号分隔 (default: fast,chat,reasoning)")
    parser.add_argument("--native", action="store_true",
                        help="使用项目 ModelClient 调用（默认直连 API）")
    parser.add_argument("--stream", action="store_true", help="流式模式（仅直连 API 支持）")
    args = parser.parse_args()

    load_env()
    all_models = load_all_models()

    # 过滤类别
    wanted = [c.strip() for c in args.categories.split(",")]
    selected = {c: all_models[c] for c in wanted if c in all_models}
    if not selected:
        print(f"[FATAL] 未找到匹配类别: {args.categories}")
        print(f"  可用: {', '.join(all_models)}")
        sys.exit(1)

    total_models = sum(len(v) for v in selected.values())
    print(f"已加载 {total_models} 个模型 ({', '.join(selected)}):")
    for cat, models in selected.items():
        for m in models:
            print(f"  [{cat}] {m['provider']}:{m['model']}  (timeout={m['timeout']}s  "
                  f"max_tokens={m['max_tokens']}  fmt={m['api_format']})")

    if args.native:
        print("\n⚠  项目模式：每个调用创建新的 ModelClient 实例（与生产一致）")

    asyncio.run(run(selected, args.n, native=args.native, stream=args.stream))


if __name__ == "__main__":
    main()
