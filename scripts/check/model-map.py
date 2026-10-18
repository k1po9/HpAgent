#!/usr/bin/env python3
"""Offline, secret-safe model routing report. Never starts services or calls providers."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

USES = {
    "fast": "召回查询改写、上下文压缩、长工具结果摘要",
    "chat": "Web/QQ对话、Agent决策、Research报告、HTML成果",
    "reasoning": "已注册的显式推理链；当前业务不会自动切换",
    "embedding": "工具语义检索向量",
    "rerank": "工具候选重排",
    "image": "图像生成（Beta关闭）",
}
PLACEHOLDERS = {"", "xxx", "change-me", "test-key"}


def filled(value: object) -> bool:
    text = str(value or "").strip()
    return text.lower() not in PLACEHOLDERS and "${" not in text


def secret_values(model: dict) -> list[str]:
    values = []
    for service in model.get("services", {}).values():
        for name, value in (service.get("environment") or {}).items():
            if re.search(r"(?:KEY|PASSWORD|SECRET|PEPPER|TOKEN)$", name) and value:
                values.append(str(value))
    return sorted(set(values), key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for value in secrets:
        text = text.replace(value, "[REDACTED]")
    return text


def redact_report(value, secrets: list[str]):
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [redact_report(item, secrets) for item in value]
    if isinstance(value, dict):
        return {redact(str(key), secrets): redact_report(item, secrets)
                for key, item in value.items()}
    return value


def model_path(worker: dict) -> Path:
    target = Path(worker.get("environment", {}).get("HPAGENT_MODELS_PATH")
                  or "/app/config/models.yaml")
    mounts = sorted(worker.get("volumes", []),
                    key=lambda volume: len(str(volume.get("target", ""))), reverse=True)
    for volume in mounts:
        if volume.get("type") != "bind":
            continue
        root = Path(volume["target"])
        if target == root or target.is_relative_to(root):
            return Path(volume["source"]) / target.relative_to(root)
    raise ValueError("无法将Worker模型配置映射到宿主文件")


@contextmanager
def worker_environment(values: dict):
    previous = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(values)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def summarize(model: dict) -> dict:
    # Imports the settings parser only, not Worker/clients; no network requests.
    import yaml
    from orchestration.config import ModelsConfig

    services = model.get("services", {})
    worker = services["hpagent"]
    environment = {name: str(value or "")
                   for name, value in worker.get("environment", {}).items()}
    path = model_path(worker)
    raw = yaml.safe_load(path.read_text()) or {}
    raw_models = raw.get("models", raw)
    with worker_environment(environment):
        config = ModelsConfig.from_yaml(path)
    rows, issues = [], []
    for category in USES:
        entries = [config.rerank] if category == "rerank" else config.get_chain(category)
        raw_entries = [raw.get("rerank", {})] if category == "rerank" else raw_models.get(category, [])
        for index, entry in enumerate(entries):
            provider = config.providers.get(entry.provider)
            if provider is None:
                issues.append(f"{category}[{index}]引用未定义的provider")
            key_ok = bool(provider and filled(provider.api_key))
            url_ok = bool(provider and filled(provider.base_url))
            model_ok = filled(entry.model)
            if not (key_ok and url_ok and model_ok):
                issues.append(f"{category}[{index}]模型名、地址或凭据缺失/未展开/占位")
            rows.append({"category": category, "provider": entry.provider,
                         "model": entry.model, "model_source": raw_entries[index].get("model", ""),
                         "key_configured": key_ok, "url_configured": url_ok,
                         "used_by": USES[category]})

    hindsight_env = services.get("hindsight", {}).get("environment", {})
    hindsight = []
    for role, prefix in (("llm", "HINDSIGHT_API_LLM"),
                         ("embedding", "HINDSIGHT_API_EMBEDDINGS_OPENAI"),
                         ("rerank", "HINDSIGHT_API_RERANKER_SILICONFLOW")):
        key_name = prefix + "_API_KEY"
        row = {"role": role, "model": hindsight_env.get(prefix + "_MODEL", ""),
               "key_configured": filled(hindsight_env.get(key_name)),
               "url_configured": filled(hindsight_env.get(prefix + "_BASE_URL"))}
        if role == "llm":
            row["adapter"] = hindsight_env.get("HINDSIGHT_API_LLM_PROVIDER", "")
        if not (filled(row["model"]) and row["key_configured"] and row["url_configured"]):
            issues.append(f"hindsight.{role}模型名、地址或凭据缺失/未展开/占位")
        hindsight.append(row)

    referenced = set(re.findall(r"\$\{(\w+)\}", path.read_text()))
    unused = [name for name in environment
              if name.endswith("_MODEL") and name not in referenced and filled(environment[name])]
    return {"config_file": str(path), "routes": rows, "hindsight": hindsight,
            "catalogue_not_selected_by_worker_yaml": sorted(unused), "issues": issues,
            "note": "静态配置检查；未验证模型存在、权限、费用或可调用性。"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--beta", action="store_true", help="合并Beta overlay，不启动服务")
    args = parser.parse_args(argv)
    env_path = args.env_file.resolve()
    if not env_path.is_file():
        print("检查失败：指定的私有配置文件不存在。", file=sys.stderr)
        return 2
    command = ["docker", "compose", "--env-file", str(env_path), "-p", "hpagent-beta",
               "-f", str(ROOT / "docker-compose.yaml")]
    if args.beta:
        command += ["-f", str(ROOT / "docker-compose.beta.yaml")]
    command += ["--profile", "web-prod", "--profile", "qq", "config", "--format", "json"]
    # Explicit file is authoritative; prevent stale shell secrets overriding it.
    environment = os.environ.copy()
    names = set(re.findall(r"^([A-Za-z_]\w*)=", env_path.read_text(), re.M))
    for name in names | {"COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"}:
        environment.pop(name, None)
    try:
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                text=True, timeout=30)
        if result.returncode:
            # Compose errors can quote interpolated values. Never print raw stderr.
            print("检查失败：Compose配置不能解析。错误正文已隐藏，以免泄漏凭据。", file=sys.stderr)
            return 2
        model = json.loads(result.stdout)
        report = summarize(model)
        print(json.dumps(redact_report(report, secret_values(model)), ensure_ascii=False, indent=2))
        return int(bool(report["issues"]))
    except Exception:
        # No exception repr/traceback: paths or parser errors can contain secrets.
        print("检查失败：无法生成脱敏模型图；未输出原始异常或配置。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
