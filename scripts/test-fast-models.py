#!/usr/bin/env python3
"""
Fast 模型链路测试 —— 测量每次调用延迟、成功率、token 用量。

用法:
    python scripts/test-fast-models.py          # 每个模型测 5 次（默认）
    python scripts/test-fast-models.py -n 10    # 每个模型测 10 次
    python scripts/test-fast-models.py -n 10 --stream  # 流式模式

从 .env 加载 API 密钥，从 config/models.yaml 读取 fast 模型链。
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

import httpx
import yaml

# ── 项目根目录 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def load_env(path: str = ".env") -> Dict[str, str]:
    """加载 .env 文件，返回替换了 ${VAR} 的环境变量 dict。"""
    env_path = ROOT / path
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
    # Merge into os.environ so ${VAR} in models.yaml resolves
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


def resolve_env_vars(value: str) -> str:
    """替换 ${VAR} 占位符。"""
    return re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), value)


def load_fast_models() -> List[Dict[str, Any]]:
    """从 config/models.yaml 读取 fast 模型链。"""
    config_path = ROOT / "config" / "models.yaml"
    raw = config_path.read_text(encoding="utf-8")
    raw = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), raw)
    cfg = yaml.safe_load(raw)
    models = cfg.get("fast", [])
    providers = cfg.get("providers", {})

    resolved = []
    for m in models:
        provider_name = m.get("provider", "")
        p = providers.get(provider_name, {})
        resolved.append({
            "model": m["model"],
            "provider": provider_name,
            "base_url": resolve_env_vars(p.get("base_url", "")).rstrip("/"),
            "api_key": resolve_env_vars(p.get("api_key", "")),
            "api_format": p.get("api_format", "openai"),
            "max_tokens": m.get("max_tokens", 1024),
            "timeout": m.get("timeout", 30.0),
        })
    return resolved


# ── 测试用 prompt —— 模拟 fast 模型的实际场景：工具结果摘要 ──
TEST_PROMPT = (
    "请用不超过 3 句话总结以下文本的要点。\n\n"
    "文本：今天天气很好，我和朋友去公园散步。公园里的樱花开了，"
    "很多人都在拍照。我们还看到了一只松鼠在树上跳来跳去。"
    "中午在附近的餐厅吃了午饭，味道很不错。下午回到家后，"
    "我开始整理昨天的工作文件，发现有几个地方需要修改。"
)
TEST_SYSTEM = "你是一个文本摘要助手，只做简洁的摘要，不要添加额外评论。"


async def call_model(
    client: httpx.AsyncClient,
    model_cfg: Dict[str, Any],
    stream: bool = False,
) -> Dict[str, Any]:
    """调用一次模型，返回 (latency_ms, tokens, error)。"""
    fmt = model_cfg["api_format"]

    if fmt == "openai":
        url = f"{model_cfg['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {model_cfg['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_cfg["model"],
            "messages": [
                {"role": "system", "content": TEST_SYSTEM},
                {"role": "user", "content": TEST_PROMPT},
            ],
            "max_tokens": model_cfg["max_tokens"],
            "stream": stream,
        }
    else:
        url = f"{model_cfg['base_url']}/messages"
        headers = {
            "x-api-key": model_cfg["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_cfg["model"],
            "system": TEST_SYSTEM,
            "messages": [{"role": "user", "content": TEST_PROMPT}],
            "max_tokens": model_cfg["max_tokens"],
            "stream": stream,
        }

    t0 = time.monotonic()
    try:
        response = await client.post(
            url, json=payload, headers=headers,
            timeout=model_cfg["timeout"],
        )

        if stream:
            content = ""
            async for line in response.aiter_lines():
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
        else:
            body = response.json()
            if fmt == "openai":
                choice = (body.get("choices") or [{}])[0]
                content = choice.get("message", {}).get("content", "")
            else:
                parts = []
                for block in body.get("content", []):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                content = "".join(parts)

        latency_ms = (time.monotonic() - t0) * 1000

        # 提取 token 用量
        if stream:
            tokens = None
        else:
            usage = body.get("usage", {})
            if "input_tokens" in usage:
                tokens = {"in": usage["input_tokens"], "out": usage["output_tokens"]}
            elif "prompt_tokens" in usage:
                tokens = {"in": usage["prompt_tokens"], "out": usage["completion_tokens"]}
            else:
                tokens = None

        return {
            "ok": True,
            "latency_ms": latency_ms,
            "tokens": tokens,
            "content_len": len(content),
            "content_preview": content[:200],
        }

    except httpx.HTTPStatusError as e:
        latency_ms = (time.monotonic() - t0) * 1000
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except Exception as e:
        latency_ms = (time.monotonic() - t0) * 1000
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "error": f"{type(e).__name__}: {e}",
        }


async def run_tests(models: List[Dict[str, Any]], n: int, stream: bool = False):
    """对每个模型运行 n 次测试，汇总结果。"""
    print(f"\n{'='*70}")
    print(f"Fast 模型链路测试 — 每模型 {n} 次调用 {'(stream)' if stream else '(非流式)'}")
    print(f"{'='*70}")

    for idx, model_cfg in enumerate(models):
        label = f"{model_cfg['provider']}:{model_cfg['model']}"
        print(f"\n── [{idx+1}/{len(models)}] {label} ──")

        results = []
        async with httpx.AsyncClient() as client:
            for i in range(n):
                dot = "." * (i + 1)
                print(f"  {dot:<{n+2}}", end="\r", flush=True)
                r = await call_model(client, model_cfg, stream=stream)
                results.append(r)

        # ── 统计 ──
        oks = [r for r in results if r["ok"]]
        fails = [r for r in results if not r["ok"]]
        latencies = [r["latency_ms"] for r in oks]
        total_in = sum(r["tokens"]["in"] for r in oks if r["tokens"])
        total_out = sum(r["tokens"]["out"] for r in oks if r["tokens"])

        print(f"  {'':<{n+2}}", end="\r")
        print(f"  成功率: {len(oks)}/{n} ({100*len(oks)/n:.0f}%)")

        if latencies:
            avg = sum(latencies) / len(latencies)
            sorted_lat = sorted(latencies)
            p50 = sorted_lat[len(sorted_lat) // 2]
            p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if len(sorted_lat) > 1 else sorted_lat[0]
            print(f"  延迟: avg={avg:.0f}ms  p50={p50:.0f}ms  p95={p95:.0f}ms  min={sorted_lat[0]:.0f}ms  max={sorted_lat[-1]:.0f}ms")
            if oks:
                print(f"  Tokens: avg_in={total_in//len(oks)} avg_out={total_out//len(oks)} (total_in={total_in} total_out={total_out})")

        if fails:
            print(f"  失败详情:")
            for r in fails:
                err = r["error"][:120]
                print(f"    - {r['latency_ms']:.0f}ms: {err}")

        # 每次延迟明细
        lat_strs = [f"{r['latency_ms']:.0f}ms" for r in results]
        print(f"  各次延迟: {' | '.join(lat_strs)}")

    print(f"\n{'='*70}")
    print("测试完成")


def main():
    parser = argparse.ArgumentParser(description="测试 Fast 模型链")
    parser.add_argument("-n", type=int, default=5, help="每个模型测试次数 (default: 5)")
    parser.add_argument("--stream", action="store_true", help="使用流式模式")
    args = parser.parse_args()

    # 加载 .env
    env = load_env()
    if not env:
        print("[FATAL] 无法加载 .env 文件")
        sys.exit(1)

    # 读取模型链
    models = load_fast_models()
    if not models:
        print("[FATAL] config/models.yaml 中未找到 fast 模型")
        sys.exit(1)

    print(f"已加载 {len(models)} 个 fast 模型:")
    for m in models:
        print(f"  - {m['provider']}:{m['model']} (timeout={m['timeout']}s max_tokens={m['max_tokens']})")

    asyncio.run(run_tests(models, args.n, args.stream))


if __name__ == "__main__":
    main()
