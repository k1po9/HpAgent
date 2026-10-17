"""Internal implementation of release-smoke.sh; stdlib only, no work at import."""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, HTTPCookieProcessor, build_opener

ROOT = Path(__file__).resolve().parents[2]
SERVICES = (
    "app-postgres", "redis", "temporal", "hindsight", "searxng", "gotenberg",
    "hpagent-api", "hpagent", "hpagent-document-worker", "web-gateway",
)


class SmokeError(Exception):
    """An operator-safe failure message, without request/response secrets."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def positive(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=(
        "生产组合人工 smoke：可能构建镜像、启动服务/迁移、写入会话和消息并调用真实模型。"
        "不会停止服务或清理业务记录；仅用于操作员选定的部署环境。"
    ))
    parser.add_argument("--execute", action="store_true", help="显式执行上述副作用")
    parser.add_argument("--profile", choices=["web-prod"], default=os.getenv("SMOKE_PROFILE", "web-prod"))
    parser.add_argument("--compose-file", default=str(ROOT / "docker-compose.yaml"))
    parser.add_argument("--gateway", default=os.getenv("WEB_GATEWAY_URL"), help="默认采用Compose API的WEB_PUBLIC_ORIGIN；显式地址必须与其一致")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--no-start", action="store_true", help="复用已部署组合，仍检查全部服务并写入受控Run")
    parser.add_argument("--timeout", type=positive, default=180, help="各健康/Run阶段最长秒数")
    parser.add_argument("--request-timeout", type=positive, default=15)
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("执行需要 --execute；先阅读 --help 中的副作用")
    if args.profile != "web-prod":
        parser.error("生产网关组合仅支持 web-prod")
    if not os.getenv("SMOKE_USER") or not os.getenv("SMOKE_PASSWORD"):
        parser.error("必须通过 SMOKE_USER / SMOKE_PASSWORD 指定已有专用账户")
    return args


def compose(args, *command):
    return ["docker", "compose", "-f", args.compose_file, "--profile", args.profile, *command]


def output(command):
    try:
        return subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, OSError) as exc:
        raise SmokeError("外部命令失败；检查 Docker/Compose 与部署日志（不回显可能含凭据的输出）") from exc


def validate_topology(model):
    services = model.get("services", {})
    missing = set(SERVICES + ("hpagent-migrate",)) - services.keys()
    if missing:
        raise SmokeError("Compose 缺少所需服务：" + ", ".join(sorted(missing)))
    for name in ("hpagent-api", "hpagent"):
        environment = services[name].get("environment", {})
        health = services[name].get("healthcheck", {})
        if not health or health.get("disable"):
            raise SmokeError(f"{name} 必须配置有效healthcheck，不能只凭进程running验收")
        if environment.get("HPAGENT_ENV") != "production":
            raise SmokeError(f"{name} 必须设置 HPAGENT_ENV=production")
        if str(environment.get("WEB_FAKE_EXECUTOR_ENABLED", "false")).lower() not in {"false", "0"}:
            raise SmokeError(f"{name} 不得启用 Fake Executor")
    if "80" not in {str(p.get("target")) for p in services["web-gateway"].get("ports", [])}:
        raise SmokeError("web-gateway 必须发布容器端口80")
    return services


def resolve_gateway(value, services):
    configured = services["hpagent-api"].get("environment", {}).get("WEB_PUBLIC_ORIGIN", "").rstrip("/")
    value = (value or configured).rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise SmokeError("网关必须是合法HTTP(S) origin，不含凭据、路径或查询")
    if value != configured:
        raise SmokeError("网关origin必须与Compose API的WEB_PUBLIC_ORIGIN一致，否则CSRF/Cookie验证无效")
    return value


def wait_services(args, services):
    deadline = time.monotonic() + args.timeout
    pending = list(SERVICES) + ["hpagent-migrate"]
    while pending:
        for service in pending[:]:
            cid = output(compose(args, "ps", "--all", "--quiet", service)).strip()
            if not cid:
                continue
            states = json.loads(output(["docker", "inspect", "--format", "{{json .State}}", cid]))
            if service == "hpagent-migrate":
                if states.get("Status") == "exited":
                    if states.get("ExitCode") != 0:
                        raise SmokeError("hpagent-migrate 非零退出")
                    pending.remove(service)
            elif states.get("Running"):
                configured = services[service].get("healthcheck", {})
                needs_health = bool(configured) and not configured.get("disable")
                if not needs_health or states.get("Health", {}).get("Status") == "healthy":
                    pending.remove(service)
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise SmokeError("服务未就绪：" + ", ".join(pending))
        time.sleep(2)


class Client:
    def __init__(self, origin, timeout):
        self.origin = origin
        self.timeout = timeout
        self.opener = build_opener(NoRedirect(), HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method, path, payload=None, csrf=None, expected=(200,)):
        headers = {"Accept": "application/json", "Origin": self.origin}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if csrf:
            headers.update({"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())})
        request = Request(self.origin + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            response = exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SmokeError(f"{method} {path}: 请求失败") from exc
        with response:
            if response.code not in expected:
                raise SmokeError(f"{method} {path}: HTTP {response.code}")
            body = response.read()
            if response.code == 303 or not body:
                return {}
            try:
                return json.loads(body)
            except ValueError as exc:
                raise SmokeError(f"{method} {path}: 非JSON响应") from exc


def exercise(client, username, password, timeout):
    client.request("POST", "/auth/login", {"username": username, "password": password, "return_to": "/"}, expected=(303,))
    me = client.request("GET", "/api/v1/me")
    csrf = me.get("csrf_token")
    if not csrf or not me.get("account", {}).get("account_id"):
        raise SmokeError("/me 缺少账户或CSRF")
    conversation = client.request("POST", "/api/v1/conversations", {"title": "release-smoke"}, csrf, (201,))["conversation"]["conversation_id"]
    sent = client.request("POST", f"/api/v1/conversations/{conversation}/messages", {"content": "ping — release smoke", "agent_strategy": "react"}, csrf, (202,))
    run_id = sent["run"]["run_id"]
    deadline = time.monotonic() + timeout
    while True:
        run = client.request("GET", f"/api/v1/runs/{run_id}")["run"]
        if run["status"] == "succeeded":
            break
        if run["status"] in {"failed", "cancelled"}:
            raise SmokeError("受控Run失败或取消")
        if time.monotonic() >= deadline:
            raise SmokeError("受控Run等待超时")
        time.sleep(2)
    history = client.request("GET", f"/api/v1/conversations/{conversation}/messages?limit=100")
    if not any(item.get("role") == "assistant" and item.get("status") == "completed" and item.get("produced_by_run_id") == run_id and item.get("content") for item in history.get("items", [])):
        raise SmokeError("历史中缺少本次Run产生的非空已完成助手消息")


def main(argv=None):
    args = parse_args(argv)
    try:
        services = validate_topology(json.loads(output(compose(args, "config", "--format", "json"))))
        args.gateway = resolve_gateway(args.gateway, services)
        if not args.no_start:
            if not args.skip_build:
                output(compose(args, "build"))
            output(compose(args, "up", "-d"))
        wait_services(args, services)
        client = Client(args.gateway, args.request_timeout)
        # SPA and proxied health are distinct: HTML is deliberately not parsed as JSON.
        with client.opener.open(args.gateway + "/", timeout=args.request_timeout) as response:
            if response.status != 200 or b"id=\"root\"" not in response.read():
                raise SmokeError("网关未提供预期SPA")
        exercise(client, os.environ["SMOKE_USER"], os.environ["SMOKE_PASSWORD"], args.timeout)
    except (SmokeError, KeyError, ValueError, URLError, OSError) as exc:
        message = str(exc) if isinstance(exc, SmokeError) else "响应/配置不符合当前合同，检查部署日志"
        print("[release-smoke] FAIL: " + message, file=sys.stderr)
        return 1
    print("[release-smoke] PASS: migration/services/Worker health/login/succeeded Run/attributed history")
    print("[release-smoke] 测试会话保留；不代表所有生产业务与恢复路径已验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
