"""
AppConfig —— 应用配置的强类型层次结构。

从 config.yaml 加载，每个模块一个子 dataclass。
默认值由 dataclass 定义；历史 YAML 工具数量缺省保留显式兼容分支。
Worker 通过属性访问配置。

用法:
    config = AppConfig.from_yaml("config/config.yaml")
    nsjail = NsjailConfig(
        nsjail_binary=config.sandbox.nsjail_binary,
        time_limit=config.sandbox.time_limit,
        ...
    )
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from common.agent_policy import AgentPolicy
from common.config_validation import environment_name, integer, typed_fields
from common.onebot import OneBot11Config
from common.runtime_config import (
    ArtifactRuntimeConfig,
    CapacityConfig,
    FileRuntimeConfig,
    binding_challenge_seconds,
    run_budget_mode,
)
from memory.quality_config import (
    MemoryConfigError,
    MemoryQualityConfig,
    load_memory_prompts,
    read_yaml,
)
from orchestration.run_lifecycle_contracts import (
    WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS,
    WEB_AGENT_TASK_QUEUE,
    WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_FINALIZE_START_TO_CLOSE_SECONDS,
    WEB_LIFECYCLE_TASK_QUEUE,
    WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_PREPARE_START_TO_CLOSE_SECONDS,
    WEB_WORKFLOW_EXECUTION_TIMEOUT_SECONDS,
)
from research_domain.policy import ResearchPolicy

if TYPE_CHECKING:
    from resources.credentials import ModelEndpoint

logger = logging.getLogger("HpAgent.Config")

_IGNORED_FIELDS = {
    "config": {
        "scheduler": "Work scheduling is owned by PostgreSQL",
        "session": "conversation state is owned by PostgreSQL",
        "models": "use the separate models.yaml file",
        "prompts": "use the separate prompts directory",
        "memory_quality": "use the separate hindsight.yaml file",
        "research": "only existing environment overrides are loaded; YAML policy wiring is deferred",
    },
    "agent": {
        "mode": "the Run chooses its durable strategy",
        "idle_timeout_minutes": "legacy sessions have been retired",
        "event_fetch_limit": "legacy sessions have been retired",
        "activity_timeout": "use the versioned Workflow contract",
        "archive_timeout": "legacy sessions have been retired",
        "multi_agent": "delegation uses the existing capability contract",
        "max_history_turns": "context assembly uses its existing token budget",
        "reflect_interval_hours": "bank Reflect scheduling has been retired",
    },
    "workspace": {"db_path": "Workspace metadata is owned by PostgreSQL"},
    "sandbox": {"result_ttl": "the current execution path does not consume this value"},
    "hindsight": {"reflect_timeout": "native Reflect requests are not sent"},
    "models": {"channel_overrides": "formal Agent calls use model-chain parameters"},
    "models.rerank": {"top_n": "tool retrieval supplies its request limit from tool_rag.top_k"},
}

# Keep the existing YAML omission behavior until a numeric migration is approved.
_LEGACY_YAML_TOOL_RAG_TOP_K = 8


def _check_fields(source: dict, allowed: set[str], path: str) -> None:
    ignored = _IGNORED_FIELDS.get(path, {})
    unknown = source.keys() - allowed - ignored.keys()
    if unknown:
        name = sorted(unknown)[0]
        raise ValueError(f"{path}.{name}: unknown configuration field")
    for name, replacement in ignored.items():
        if name in source:
            logger.warning("%s.%s is ignored; %s", path, name, replacement)


def _mapping(value: Any, path: str, *, optional: bool = True) -> dict:
    if value is None and optional:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a mapping")
    return value


def _validate_section(config_type: type, values: dict, path: str) -> None:
    typed_fields(config_type, values, path)
    nonnegative = {"port", "send_interval_seconds", "group_context_density_threshold"}
    for name in {f.name for f in dataclasses.fields(config_type)} & values.keys():
        value = values[name]
        if type(value) in (int, float) and (
            value < 0 if name in nonnegative else value <= 0
        ):
            raise ValueError(f"{path}.{name}: must be {'nonnegative' if name in nonnegative else 'positive'}")


# ═══════════════════════════════════════════════════════════════════════════════
# 模型配置 —— 从 config/models.yaml 加载
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProviderEntry:
    """API 提供商凭证。"""
    base_url: str = ""
    api_key: str = ""
    api_format: str = "anthropic"  # "anthropic" | "openai"
    extra_body: dict = field(default_factory=dict)  # 注入到 API 请求体的额外字段


@dataclass
class ModelEntry:
    """单个模型条目 —— 属于某个类别降级链中的一环。"""
    provider: str = ""       # 引用 providers 中的 key
    model: str = ""
    max_tokens: int = 2048
    timeout: float = 30.0
    extra_body: dict = field(default_factory=dict)  # 模型级请求参数，覆盖 provider 默认值
    access_tier: str = "standard"


# ═══════════════════════════════════════════════════════════════════════════════
# 工具体系扩展配置 —— 从 config/models.yaml 加载
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolRagConfig:
    """工具 RAG 检索配置。"""
    enabled: bool = True
    top_k: int = 12                         # 最终返回工具数量上限
    persist_path: str = "tools/vectors"


@dataclass
class McpConfig:
    """MCP Server 配置。"""
    config_path: str = "config/mcp/servers.yaml"
    auto_connect: bool = False


@dataclass
class SkillsConfig:
    """Skills 配置。"""
    config_path: str = "tools/skills/"
    enabled: bool = True


@dataclass
class ChannelsConfig:
    """渠道启停配置 —— 控制哪些渠道在 Worker 启动时加载。

    enabled 列表中的渠道名必须对应 ChannelType 枚举值。
    不在列表中的渠道不会被初始化或连接。
    """
    enabled: List[str] = field(default_factory=lambda: ["console"])
    onebot11: OneBot11Config = field(default_factory=OneBot11Config)


@dataclass
class ChannelOverrideConfig:
    """旧 YAML 兼容值；正式组合不消费，不向 BrainEngine 自动接线。"""
    max_tokens: int = 1024
    timeout: float = 30.0
    stream: bool = False


@dataclass
class RerankConfig:
    """Rerank 重排序配置。"""
    provider: str = ""
    model: str = "BAAI/bge-reranker-v2-m3"
    timeout: float = 10.0
    top_n: int = 10  # 旧 YAML 兼容值；检索器逐请求提供实际 top_n。


@dataclass
class ModelsConfig:
    """模型 API 统一配置 —— 从 config/models.yaml 加载。

    结构:
      providers:
        minimax: {base_url, api_key}
        openai:   {base_url, api_key}
      models:
        chat:       [{provider, model, max_tokens, timeout}, ...]
        embedding:  [...]
        image:      [...]
        reasoning:  [...]

    每个类别是一个降级链，从上到下依次尝试，失败后切换下一个。
    """
    providers: Dict[str, ProviderEntry] = field(default_factory=dict)
    fast: List[ModelEntry] = field(default_factory=list)
    chat: List[ModelEntry] = field(default_factory=list)
    embedding: List[ModelEntry] = field(default_factory=list)
    image: List[ModelEntry] = field(default_factory=list)
    reasoning: List[ModelEntry] = field(default_factory=list)
    tool_rag: ToolRagConfig = field(default_factory=ToolRagConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    rerank: RerankConfig = field(default_factory=RerankConfig)
    channel_overrides: Dict[str, ChannelOverrideConfig] = field(default_factory=dict)

    def has_category(self, name: str) -> bool:
        """检查指定类别是否配置了模型。"""
        return len(getattr(self, name, [])) > 0

    def get_chain(self, name: str) -> List[ModelEntry]:
        """获取指定类别的模型降级链。"""
        return getattr(self, name, [])

    def resolve_endpoint(
        self,
        entry: ModelEntry,
        *,
        endpoint_id: str = "",
    ) -> "ModelEndpoint":
        """将 ModelEntry 解析为完整的 ModelEndpoint（含 api_key）。"""
        from resources.credentials import ModelEndpoint
        provider = self.providers.get(entry.provider, ProviderEntry())

        def _deep_merge(base: dict, override: dict) -> dict:
            """递归合并请求体配置；模型条目优先于 provider 默认值。"""
            merged = dict(base)
            for key, value in override.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = _deep_merge(merged[key], value)
                else:
                    merged[key] = value
            return merged

        return ModelEndpoint(
            endpoint_id=endpoint_id,
            provider=entry.provider,
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=entry.model,
            access_tier=entry.access_tier,
            extra={
                "api_format": provider.api_format,
                "max_tokens": entry.max_tokens,
                "timeout": entry.timeout,
                "extra_body": _deep_merge(provider.extra_body, entry.extra_body),
            },
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ModelsConfig":
        """从 YAML 文件加载模型配置。

        Args:
            path: models.yaml 文件路径。

        Returns:
            ModelsConfig 实例。

        Raises:
            FileNotFoundError: 文件不存在。
        """
        import os as _os2

        config_file = Path(path)
        if not config_file.exists():
            raise FileNotFoundError(f"Models config not found: {path}")

        raw = _mapping(read_yaml(config_file, section="models"), "models")
        categories = {"fast", "chat", "embedding", "image", "reasoning"}
        _check_fields(raw, {f.name for f in dataclasses.fields(cls)} | {"models"}, "models")

        providers: Dict[str, ProviderEntry] = {}
        for key, val in _mapping(raw.get("providers"), "models.providers").items():
            path = f"models.providers.{key}"
            val = _mapping(val, path, optional=False)
            _check_fields(val, {f.name for f in dataclasses.fields(ProviderEntry)}, path)
            _validate_section(ProviderEntry, val, path)
            provider = ProviderEntry(**val)
            if provider.api_format not in {"anthropic", "openai"}:
                raise ValueError(f"{path}.api_format: unsupported format")
            provider.base_url = _os2.path.expandvars(provider.base_url)
            provider.api_key = _os2.path.expandvars(provider.api_key)
            providers[key] = provider

        # Preserve the legacy nested form, including its precedence over top-level chains.
        models_raw = _mapping(raw["models"] if "models" in raw else raw, "models.models")
        if "models" in raw:
            _check_fields(models_raw, categories, "models.models")
            if categories & raw.keys():
                logger.warning("models.models takes precedence over top-level model chains")

        def _parse_entries(category: str) -> List[ModelEntry]:
            entries = models_raw.get(category, [])
            if entries is not None and not isinstance(entries, list):
                raise ValueError(f"models.{category}: expected a list")
            result = []
            for index, item in enumerate(entries or []):
                path = f"models.{category}.{index}"
                item = _mapping(item, path, optional=False)
                allowed_fields = {"provider", "model", "max_tokens", "timeout", "extra_body", "access_tier"}
                unknown_fields = sorted(set(item) - allowed_fields)
                if unknown_fields:
                    raise ValueError(
                        f"{path} contains unsupported fields {unknown_fields}; "
                        "provider-specific request fields must be nested under extra_body"
                    )
                _validate_section(ModelEntry, item, path)
                entry = ModelEntry(**item)
                if not entry.access_tier.strip():
                    raise ValueError(f"{path}.access_tier: must not be empty")
                entry.model = _os2.path.expandvars(entry.model)
                result.append(entry)
            return result

        sections = {}
        for name, config_type in (
            ("tool_rag", ToolRagConfig), ("mcp", McpConfig),
            ("skills", SkillsConfig), ("rerank", RerankConfig),
        ):
            values = _mapping(raw.get(name), f"models.{name}")
            _check_fields(values, {f.name for f in dataclasses.fields(config_type)}, f"models.{name}")
            _validate_section(config_type, values, f"models.{name}")
            if name == "tool_rag" and "top_k" not in values:
                values = {**values, "top_k": _LEGACY_YAML_TOOL_RAG_TOP_K}
            sections[name] = config_type(**values)
        sections["rerank"].model = _os2.path.expandvars(sections["rerank"].model)

        channel_overrides: Dict[str, ChannelOverrideConfig] = {}
        for name, values in _mapping(raw.get("channel_overrides"), "models.channel_overrides").items():
            path = f"models.channel_overrides.{name}"
            values = _mapping(values, path, optional=False)
            _check_fields(values, {f.name for f in dataclasses.fields(ChannelOverrideConfig)}, path)
            _validate_section(ChannelOverrideConfig, values, path)
            channel_overrides[name] = ChannelOverrideConfig(**values)

        return cls(
            providers=providers,
            fast=_parse_entries("fast"), chat=_parse_entries("chat"),
            embedding=_parse_entries("embedding"), image=_parse_entries("image"),
            reasoning=_parse_entries("reasoning"), channel_overrides=channel_overrides,
            **sections,
        )


@dataclass
class TemporalConfig:
    """Temporal Server 连接配置。"""
    host: str = "localhost:7233"
    # Scheduled memory reflection/metrics queue; separate from canonical Agent queues.
    task_queue: str = "hpagent-task-queue"
    web_lifecycle_task_queue: str = WEB_LIFECYCLE_TASK_QUEUE
    web_agent_task_queue: str = WEB_AGENT_TASK_QUEUE
    web_workflow_execution_timeout_seconds: int = WEB_WORKFLOW_EXECUTION_TIMEOUT_SECONDS
    web_prepare_schedule_to_close_seconds: int = WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS
    web_prepare_start_to_close_seconds: int = WEB_PREPARE_START_TO_CLOSE_SECONDS
    web_agent_heartbeat_interval_seconds: int = WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS
    web_finalize_schedule_to_close_seconds: int = WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS
    web_finalize_start_to_close_seconds: int = WEB_FINALIZE_START_TO_CLOSE_SECONDS
    # Outbox lease auto-recovery.  A claimed (processing) Outbox row whose lease
    # is older than web_outbox_lease_timeout_seconds is returned to pending by a
    # dedicated recovery loop that runs every web_outbox_recovery_interval_seconds.
    # Both values must be positive and the interval must be smaller than the
    # timeout so the loop observes leases before they can starve the queue.
    web_outbox_lease_timeout_seconds: int = 60
    web_outbox_recovery_interval_seconds: int = 15
    # Must exceed any single model/tool Activity timeout plus safety margin.
    agent_execution_lease_ttl_seconds: int = 900


@dataclass
class RedisConfig:
    """Redis 连接配置。url 为空表示不启用 Redis。"""
    url: str = ""
    default_ttl: int = 300
    # 群聊短期上下文
    group_context_window: int = 80        # 滑动窗口保留消息数
    group_context_ttl: int = 86400        # 群上下文 TTL（秒，默认 24h）
    group_context_density_threshold: float = 2.0  # 密度阈值（msg/min），低于此值允许进度提示


@dataclass
class SandboxConfig:
    """沙箱（nsjail）隔离执行配置。

    字段名与 sandbox.nsjail.NsjailConfig 一致，可直接展开构造。
    """
    native_tools_enabled: bool = False   # 本地函数工具（fs_read/write/edit/Glob/Grep/Bash）
    nsjail_enabled: bool = False         # Nsjail OS 级隔离（仅对 Bash）
    nsjail_binary: str = "/usr/bin/nsjail"
    chroot_path: str = "/"
    work_dir: str = "/work"
    runner_script: str = "/opt/nsjail-runner.py"
    python_binary: str = "/usr/bin/python3"
    time_limit: int = 30
    memory_limit_mb: int = 256
    cpu_limit_seconds: int = 10
    max_processes: int = 32
    max_files: int = 64
    disable_proc: bool = True
    disable_network: bool = True
    readonly_root: bool = True
    max_idle_seconds: int = 300
    result_ttl: int = 3600  # 旧配置兼容值；当前执行路径不消费。


@dataclass
class WorkspaceConfig:
    """用户工作区配置。"""
    root: str = ".data/workspace"
    workspace_isolation_mode: str = ""
    agent_worker_replicas: int = 1
    prefork_enabled: bool = False
    agent_activity_processes: int = 1


@dataclass
class HindsightConfig:
    """Hindsight 长期记忆服务配置。"""
    enabled: bool = True
    recall_enabled: bool = False  # Explicitly opened after the cutover data check.
    base_url: str = "http://hindsight:8888"
    api_key: str = ""
    timeout: float = 30.0          # 默认超时（retain/recall 等轻量操作）
    retain_timeout: float = 30.0   # 记忆提取（异步提交，通常很快）
    recall_timeout: float = 3.0    # 语义检索（快速降级，锦上添花不阻塞）
    reflect_timeout: float = 120.0 # 兼容字段；当前不发送原生 Reflect 请求



@dataclass
class ResearchConfig:
    """Research source, bounded iteration and evidence thresholds."""
    searxng_url: str = "http://searxng:8080"
    search_provider: str = ResearchPolicy.search_provider
    fetch_provider: str = ResearchPolicy.fetch_provider
    deepseek_search_fetch_key: str = field(default="", repr=False)
    search_timeout_seconds: float = ResearchPolicy.search_timeout_seconds
    fetch_timeout_seconds: float = ResearchPolicy.fetch_timeout_seconds
    browser_timeout_seconds: float = 30.0
    min_content_chars: int = 240
    max_sources: int = ResearchPolicy.max_sources
    max_fetches: int = ResearchPolicy.max_fetches
    max_iterations: int = ResearchPolicy.max_iterations
    min_evidence: int = ResearchPolicy.min_evidence
    min_distinct_sources: int = ResearchPolicy.min_distinct_sources


@dataclass
class AgentConfig:
    """Agent 行为参数。"""
    default_timezone: str = AgentPolicy.default_timezone
    max_history_turns: int = 10  # 旧配置兼容值；当前上下文使用 token 预算。
    max_tool_turns: int = AgentPolicy.max_tool_turns
    recall_top_n: int = 5
    reflect_interval_hours: int = 6    # 兼容字段；全 Bank Reflect 调度已停用

    # 工具结果摘要（替代简单截断）
    tool_result_summary_enabled: bool = AgentPolicy.tool_result_summary_enabled # 启用 LLM 摘要替代截断
    tool_result_summary_threshold: int = AgentPolicy.tool_result_summary_threshold # 超过此字符数触发摘要
    tool_result_summary_max_chars: int = AgentPolicy.tool_result_summary_max_chars # 摘要最大字符数（注入 LLM 的）

    def __post_init__(self):
        AgentPolicy.from_config(self)

# ═══════════════════════════════════════════════════════════════════════════════
# Prompt 配置 —— 从 config/prompts/*.yaml 加载
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PromptsConfig:
    """所有 Agent prompt 模板的统一容器。

    从 config/prompts/ 目录加载具名 YAML 文件：
      - identities.yaml  → channel_map + 各渠道人设
      - guidance.yaml    → 风格引导 + 工具纪律
      - environment.yaml → 运行环境提示 (docker/wsl)
      - system.yaml      → 系统级提示 (跨渠道/查询改写)
      - memory.yaml      → 提取/归纳/记忆使用指令
    """
    identities: Dict[str, str] = field(default_factory=dict)
    guidance: Dict[str, str] = field(default_factory=dict)
    environment: Dict[str, str] = field(default_factory=dict)
    memory: Dict[str, str] = field(default_factory=dict)
    system: Dict[str, str] = field(default_factory=dict)
    tool_summary: Dict[str, Any] = field(default_factory=dict)  # tool_summary.yaml (含嵌套 hints dict)
    bot_name: str = "bot"  # 机器人名称（群聊上下文发送者名 + @检测回退）

    @classmethod
    def from_dir(cls, prompts_dir: Path) -> "PromptsConfig":
        """从目录加载所有 prompt YAML 文件。

        Args:
            prompts_dir: prompts/ 目录路径。

        Returns:
            PromptsConfig，缺失文件时对应字段为空 dict。
        """
        def _load(filename: str) -> Dict[str, str]:
            path = prompts_dir / filename
            if not path.exists():
                logger.warning("Prompt file not found: %s", path)
                return {}
            try:
                data = _mapping(read_yaml(path, section=f"prompts.{path.stem}"), f"prompts.{path.stem}")
                return {k: v.strip() for k, v in data.items() if isinstance(v, str)}
            except ValueError:
                raise
            except Exception as e:
                logger.warning("Failed to load %s: %s", path, e)
                return {}

        def _load_raw(filename: str) -> Dict[str, Any]:
            """加载 YAML 文件，保留嵌套结构（用于 tool_summary.yaml 等含 dict 的文件）。"""
            path = prompts_dir / filename
            if not path.exists():
                logger.warning("Prompt file not found: %s", path)
                return {}
            try:
                return _mapping(read_yaml(path, section=f"prompts.{path.stem}"), f"prompts.{path.stem}")
            except ValueError:
                raise
            except Exception as e:
                logger.warning("Failed to load %s: %s", path, e)
                return {}

        identities = _load_raw("identities.yaml")
        bot_name = identities.pop("bot_name", "bot") if isinstance(identities, dict) else "bot"
        return cls(
            identities=identities,
            guidance=_load("guidance.yaml"),
            environment=_load("environment.yaml"),
            system=_load("system.yaml"),
            memory=load_memory_prompts(prompts_dir / "memory.yaml") if (prompts_dir / "memory.yaml").exists() else {},
            tool_summary=_load_raw("tool_summary.yaml"),
            bot_name=str(bot_name),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 顶层配置
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AppConfig:
    """应用配置根结构。

    从 config.yaml + models.yaml + hindsight.yaml + prompts/*.yaml 一次性加载。
    Worker 通过属性访问。主 YAML 只加载已接线的 section；models、prompts、memory_quality
    使用独立文件，Research 主 YAML section 尚未接线，仅保留默认与既有环境覆盖。
    """
    models: ModelsConfig = field(default_factory=ModelsConfig)
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    hindsight: HindsightConfig = field(default_factory=HindsightConfig)
    memory_quality: MemoryQualityConfig = field(default_factory=MemoryQualityConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)

    # ══════════════════════════════════════════════════════════════════════
    # YAML 加载
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def from_yaml(cls, path: str | Path, models_path: str | Path = "") -> "AppConfig":
        """从 YAML 文件加载配置，缺失字段使用 dataclass 默认值。

        YAML 键名与 dataclass 字段名直接对应:
          temporal.host, sandbox.time_limit, agent.max_tool_turns ...

        模型配置从独立的 models.yaml 加载（models_path 参数），
        默认为 config.yaml 同目录下的 models.yaml。

        加载后自动应用环境变量覆盖（Docker 部署时使用）:
          TEMPORAL_HOST     → temporal.host
          HINDSIGHT_URL     → hindsight.base_url
          WORKSPACE_ROOT    → workspace.root

        Args:
            path: config.yaml 路径。
            models_path: models.yaml 路径，空则自动推断。

        Returns:
            AppConfig 实例。

        Raises:
            FileNotFoundError: 配置文件不存在。
        """
        import os as _os

        config_file = Path(path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        raw = _mapping(read_yaml(config_file, section="config"), "config")

        hindsight_raw = raw.get("hindsight")
        if isinstance(hindsight_raw, dict):
            moved = set(hindsight_raw) & {"retain_mission", "reflect_mission", "observations_mission"}
            if moved:
                paths = ", ".join(f"hindsight.{name}" for name in sorted(moved))
                raise MemoryConfigError(f"{paths}: moved to prompts/memory.yaml")

        config = cls._from_dict(raw)

        config_dir = config_file.parent
        quality_path = config_dir / "hindsight.yaml"
        if quality_path.exists():
            config.memory_quality = MemoryQualityConfig.from_yaml(
                quality_path, legacy_top_n=(raw.get("agent") or {}).get("recall_top_n"))
        else:
            config.memory_quality = MemoryQualityConfig.model_validate(
                {"recall": {"top_n": config.agent.recall_top_n}})

        # 加载模型配置
        if not models_path:
            models_path = _os.getenv(
                "HPAGENT_MODELS_PATH", str(config_dir / "models.yaml")
            )
        mp = Path(models_path)
        if mp.exists():
            config.models = ModelsConfig.from_yaml(str(mp))
        else:
            logger.warning("Models config not found: %s, using defaults", mp)

        # 加载 Prompt 配置
        prompts_dir = config_dir / "prompts"
        if prompts_dir.exists():
            config.prompts = PromptsConfig.from_dir(prompts_dir)
            logger.info("Prompts loaded from %s", prompts_dir)
        else:
            logger.warning("Prompts dir not found: %s, using defaults", prompts_dir)

        # 将相对路径解析为项目根目录下的绝对路径
        # config_dir = {project_root}/config/，因此 project_root = config_dir.parent
        config._resolve_data_paths(config_dir.parent)

        config._apply_env_overrides(_os.environ)
        return config

    def _resolve_data_paths(self, project_root: Path) -> None:
        """将数据目录的相对路径解析为绝对路径。

        默认值（如 .data/workspace）是相对于项目根目录的，
        不是相对于 cwd。容器模式下 WORKSPACE_ROOT 等环境变量会随后覆盖。
        """
        _root = project_root.resolve()

        ws = Path(self.workspace.root)
        if not ws.is_absolute():
            self.workspace.root = str(_root / ws)
        # models.yaml 中的工具相关路径（同样相对于项目根）
        mcp_path = Path(self.models.mcp.config_path)
        if not mcp_path.is_absolute():
            self.models.mcp.config_path = str(_root / mcp_path)

        skills_path = Path(self.models.skills.config_path)
        if not skills_path.is_absolute():
            self.models.skills.config_path = str(_root / skills_path)

        rag_path = Path(self.models.tool_rag.persist_path)
        if not rag_path.is_absolute():
            self.models.tool_rag.persist_path = str(_root / rag_path)

    def _apply_env_overrides(self, environ: dict) -> None:
        """用环境变量覆盖 YAML 中的值（Docker Compose 传入）。"""
        environment_name(environ.get("HPAGENT_ENV", "development"))
        if "HPAGENT_CHANNELS" in environ:
            self.channels.enabled = [v.strip() for v in environ["HPAGENT_CHANNELS"].split(",") if v.strip()]
        if environ.get("ONEBOT_EXPECTED_SELF_ID"):
            self.channels.onebot11.expected_self_id = environ["ONEBOT_EXPECTED_SELF_ID"]
        if environ.get("ONEBOT_ACCESS_TOKEN_FILE"):
            self.channels.onebot11.access_token_file = environ["ONEBOT_ACCESS_TOKEN_FILE"]
        if environ.get("TEMPORAL_HOST"):
            self.temporal.host = environ["TEMPORAL_HOST"]
        if environ.get("TEMPORAL_TASK_QUEUE"):
            self.temporal.task_queue = environ["TEMPORAL_TASK_QUEUE"]
        if environ.get("AGENT_EXECUTION_LEASE_TTL_SECONDS"):
            self.temporal.agent_execution_lease_ttl_seconds = integer(
                environ["AGENT_EXECUTION_LEASE_TTL_SECONDS"], "AGENT_EXECUTION_LEASE_TTL_SECONDS"
            )
        if environ.get("WEB_OUTBOX_LEASE_TIMEOUT_SECONDS"):
            self.temporal.web_outbox_lease_timeout_seconds = integer(
                environ["WEB_OUTBOX_LEASE_TIMEOUT_SECONDS"], "WEB_OUTBOX_LEASE_TIMEOUT_SECONDS"
            )
        if environ.get("WEB_OUTBOX_RECOVERY_INTERVAL_SECONDS"):
            self.temporal.web_outbox_recovery_interval_seconds = integer(
                environ["WEB_OUTBOX_RECOVERY_INTERVAL_SECONDS"], "WEB_OUTBOX_RECOVERY_INTERVAL_SECONDS"
            )
        if environ.get("HINDSIGHT_API_KEY"):
            self.hindsight.api_key = environ["HINDSIGHT_API_KEY"]
        if environ.get("HINDSIGHT_URL"):
            self.hindsight.base_url = environ["HINDSIGHT_URL"]
        if "HINDSIGHT_RECALL_ENABLED" in environ:
            value = environ["HINDSIGHT_RECALL_ENABLED"].strip().lower()
            if value not in {"true", "false"}:
                raise ValueError("HINDSIGHT_RECALL_ENABLED must be true or false")
            self.hindsight.recall_enabled = value == "true"
        if environ.get("SEARXNG_URL"):
            self.research.searxng_url = environ["SEARXNG_URL"]
        if environ.get("WEB_RETRIEVAL_SEARCH_PROVIDER"):
            self.research.search_provider = environ["WEB_RETRIEVAL_SEARCH_PROVIDER"]
        if environ.get("WEB_RETRIEVAL_FETCH_PROVIDER"):
            self.research.fetch_provider = environ["WEB_RETRIEVAL_FETCH_PROVIDER"]
        if environ.get("DEEPSEEK_API_SEARCH_FETCH"):
            self.research.deepseek_search_fetch_key = environ["DEEPSEEK_API_SEARCH_FETCH"]
        if environ.get("WORKSPACE_ROOT"):
            self.workspace.root = environ["WORKSPACE_ROOT"]
        if environ.get("WORKSPACE_ISOLATION_MODE"):
            self.workspace.workspace_isolation_mode = environ["WORKSPACE_ISOLATION_MODE"]
        if environ.get("REDIS_URL"):
            self.redis.url = environ["REDIS_URL"]
        for section in ("temporal", "redis", "sandbox", "workspace", "hindsight", "agent"):
            value = getattr(self, section)
            _validate_section(type(value), dataclasses.asdict(value), section)
        CapacityConfig.from_environment(environ)
        FileRuntimeConfig.from_environment(environ)
        ArtifactRuntimeConfig.from_environment(environ)
        binding_challenge_seconds(environ)
        run_budget_mode(environ, default="enforce")

    @classmethod
    def _from_dict(cls, raw: Dict[str, Any]) -> "AppConfig":
        """从已解析的 dict 构造 AppConfig。

        每个顶层 section 对应一个子 dataclass，缺失的 section
        使用该 dataclass 的默认构造。
        """
        raw = _mapping(raw, "config")
        _check_fields(raw, {f.name for f in dataclasses.fields(cls)}, "config")

        def _populate(dataclass_type, source: dict | None, prefix: str):
            """用 source dict 中的 key → dataclass 字段映射填充实例。

            支持嵌套 dataclass：当 source 中某个字段的值是 dict 且默认值
            是 dataclass 实例时，递归调用 _populate。
            """
            defaults = {}
            for f in dataclasses.fields(dataclass_type):
                if f.default is not dataclasses.MISSING:
                    defaults[f.name] = f.default
                elif f.default_factory is not dataclasses.MISSING:
                    defaults[f.name] = f.default_factory()
                else:
                    defaults[f.name] = None

            source = _mapping(source, prefix)
            _check_fields(source, set(defaults), prefix)
            kwargs = {}
            for field_name, default_val in defaults.items():
                if field_name in source:
                    raw_val = source[field_name]
                    # Recursively populate nested dataclass fields
                    if isinstance(raw_val, dict) and dataclasses.is_dataclass(default_val):
                        kwargs[field_name] = _populate(
                            type(default_val), raw_val, f"{prefix}.{field_name}"
                        )
                    else:
                        kwargs[field_name] = raw_val
                else:
                    kwargs[field_name] = default_val
            _validate_section(dataclass_type, kwargs, prefix)
            from zoneinfo import ZoneInfoNotFoundError

            try:
                return dataclass_type(**{k: v for k, v in kwargs.items() if k in defaults})
            except (ZoneInfoNotFoundError, ValueError):
                if dataclass_type is AgentConfig:
                    raise ValueError("agent.default_timezone: expected an IANA timezone") from None
                raise

        return cls(
            temporal=_populate(TemporalConfig, raw.get("temporal"), "temporal"),
            redis=_populate(RedisConfig, raw.get("redis"), "redis"),
            sandbox=_populate(SandboxConfig, raw.get("sandbox"), "sandbox"),
            workspace=_populate(WorkspaceConfig, raw.get("workspace"), "workspace"),
            hindsight=_populate(HindsightConfig, raw.get("hindsight"), "hindsight"),
            channels=_populate(ChannelsConfig, raw.get("channels"), "channels"),
            agent=_populate(AgentConfig, raw.get("agent"), "agent"),
        )
