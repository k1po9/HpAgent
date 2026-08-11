#!/usr/bin/env bash
# 在 docker-compose.yaml 的 hpagent 容器环境中测试模型调用延迟。
#
# 默认使用项目 ModelClient，读取 compose 注入的环境变量和 config/models.yaml。
# 测试实现复用 scripts/test-models.py，避免容器内外两套基准逻辑产生偏差。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yaml}"
BUILD_IMAGE=false
USE_NATIVE=true
BENCHMARK_ARGS=()

usage() {
  cat <<'EOF'
用法:
  ./scripts/test-compose-model-latency.sh [包装器选项] [模型测试选项]

包装器选项:
  --build       测试前重新构建 hpagent 镜像
  --direct      绕过项目 ModelClient，直接请求模型 API
  -h, --help    显示帮助

模型测试选项（透传给 scripts/test-models.py）:
  -n N                          每个模型调用次数（默认 5）
  -c, --categories CATEGORIES  模型类别，如 fast,chat（默认全部文本模型）
  --stream                     使用流式请求（仅可与 --direct 一起使用）

示例:
  ./scripts/test-compose-model-latency.sh -n 10 -c fast,chat
  ./scripts/test-compose-model-latency.sh --build -n 5
  ./scripts/test-compose-model-latency.sh --direct --stream -c chat

可通过 COMPOSE_FILE 指定其他 Compose 文件。
EOF
}

while (($#)); do
  case "$1" in
    --build)
      BUILD_IMAGE=true
      shift
      ;;
    --direct)
      USE_NATIVE=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      BENCHMARK_ARGS+=("$1")
      shift
      ;;
  esac
done

fail() {
  echo "[model-latency] ERROR: $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "未找到 docker 命令"
docker compose version >/dev/null 2>&1 || fail "当前 Docker 未安装 Compose 插件"
[[ -f "$COMPOSE_FILE" ]] || fail "Compose 文件不存在: $COMPOSE_FILE"

cd "$ROOT"

if [[ "$BUILD_IMAGE" == true ]]; then
  echo "[model-latency] 正在构建 hpagent 镜像..."
  docker compose -f "$COMPOSE_FILE" --profile agent build hpagent
fi

MODE_ARGS=()
if [[ "$USE_NATIVE" == true ]]; then
  MODE_ARGS+=(--native)
fi

echo "[model-latency] 在 hpagent Compose 容器中执行模型延迟测试..."
echo "[model-latency] Compose: $COMPOSE_FILE"

# --no-deps：基准测试只访问外部模型 API，无需启动数据库、Redis 等基础设施。
# --entrypoint：hpagent 的默认 entrypoint 固定启动 Worker，因此这里显式改为 Python。
docker compose -f "$COMPOSE_FILE" --profile agent run --rm --no-deps \
  --interactive=false --no-tty \
  --entrypoint python \
  --env PYTHONPATH=/app \
  --volume "$ROOT/scripts/test-models.py:/app/scripts/test-models.py:ro" \
  hpagent \
  /app/scripts/test-models.py \
  "${MODE_ARGS[@]}" \
  "${BENCHMARK_ARGS[@]}"
