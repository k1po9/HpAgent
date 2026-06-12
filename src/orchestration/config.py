"""
AppConfig —— 应用配置的强类型层次结构。

从 config.yaml 加载，每个模块一个子 dataclass。
所有默认值在一处定义，Worker 通过属性访问而非 dict.get()。

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
from typing import Dict, Any

logger = logging.getLogger("HpAgent.Config")


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


# ═══════════════════════════════════════════════════════════════════════════════
# 工具体系扩展配置 —— 从 config/models.yaml 加载
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolRagConfig:
    """工具 RAG 检索配置。"""
    enabled: bool = True
    top_k: int = 8                          # 最终返回工具数量上限
    max_merged_multiplier: float = 1.5      # 多路检索合并缓冲系数（max_merged = top_k * multiplier）
    per_query_min: int = 3                  # 多路检索每查询最少召回数
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


@dataclass
class ChannelOverrideConfig:
    """单个渠道的模型参数覆盖。"""
    max_tokens: int = 1024
    timeout: float = 30.0
    stream: bool = False


@dataclass
class RerankConfig:
    """Rerank 重排序配置。"""
    provider: str = ""
    model: str = "BAAI/bge-reranker-v2-m3"
    timeout: float = 10.0
    top_n: int = 10


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

    def resolve_endpoint(self, entry: ModelEntry) -> "ModelEndpoint":
        """将 ModelEntry 解析为完整的 ModelEndpoint（含 api_key）。"""
        from resources.credentials import ModelEndpoint
        provider = self.providers.get(entry.provider, ProviderEntry())
        return ModelEndpoint(
            provider=entry.provider,
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=entry.model,
            extra={
                "api_format": provider.api_format,
                "max_tokens": entry.max_tokens,
                "timeout": entry.timeout,
                "extra_body": provider.extra_body,
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
        import yaml

        config_file = Path(path)
        if not config_file.exists():
            raise FileNotFoundError(f"Models config not found: {path}")

        with open(config_file, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = yaml.safe_load(f) or {}

        # 解析 providers（支持 ${ENV_VAR} 语法展开）
        providers: Dict[str, ProviderEntry] = {}
        for key, val in (raw.get("providers") or {}).items():
            if isinstance(val, dict):
                providers[key] = ProviderEntry(
                    base_url=_os2.path.expandvars(val.get("base_url", "")),
                    api_key=_os2.path.expandvars(val.get("api_key", "")),
                    api_format=val.get("api_format", "anthropic"),
                    extra_body=val.get("extra_body") or {},
                )

        # 解析 models 各分类
        models_raw = raw.get("models") or raw  # 兼容 models: 键或顶层直接写
        if "models" in raw:
            models_raw = raw["models"]

        def _parse_entries(category: str) -> List[ModelEntry]:
            entries = models_raw.get(category, []) if isinstance(models_raw, dict) else []
            result = []
            for item in (entries or []):
                if isinstance(item, dict):
                    result.append(ModelEntry(
                        provider=item.get("provider", ""),
                        model=_os2.path.expandvars(item.get("model", "")),
                        max_tokens=item.get("max_tokens", 2048),
                        timeout=item.get("timeout", 30.0),
                    ))
            return result

        tool_rag_raw = raw.get("tool_rag") or {}
        mcp_raw = raw.get("mcp") or {}
        skills_raw = raw.get("skills") or {}
        rerank_raw = raw.get("rerank") or {}
        channel_overrides_raw = raw.get("channel_overrides") or {}

        channel_overrides: Dict[str, ChannelOverrideConfig] = {}
        for ch_name, ch_cfg in channel_overrides_raw.items():
            if isinstance(ch_cfg, dict):
                channel_overrides[ch_name] = ChannelOverrideConfig(
                    max_tokens=ch_cfg.get("max_tokens", 1024),
                    timeout=ch_cfg.get("timeout", 30.0),
                    stream=ch_cfg.get("stream", False),
                )

        return cls(
            providers=providers,
            fast=_parse_entries("fast"),
            chat=_parse_entries("chat"),
            embedding=_parse_entries("embedding"),
            image=_parse_entries("image"),
            reasoning=_parse_entries("reasoning"),
            tool_rag=ToolRagConfig(
                enabled=tool_rag_raw.get("enabled", True),
                top_k=tool_rag_raw.get("top_k", 8),
                persist_path=tool_rag_raw.get("persist_path", "tools/vectors"),
            ),
            mcp=McpConfig(
                config_path=mcp_raw.get("config_path", "config/mcp/servers.yaml"),
                auto_connect=mcp_raw.get("auto_connect", False),
            ),
            skills=SkillsConfig(
                config_path=skills_raw.get("config_path", "tools/skills/"),
                enabled=skills_raw.get("enabled", True),
            ),
            rerank=RerankConfig(
                provider=rerank_raw.get("provider", ""),
                model=_os2.path.expandvars(rerank_raw.get("model", "BAAI/bge-reranker-v2-m3")),
                timeout=rerank_raw.get("timeout", 10.0),
                top_n=rerank_raw.get("top_n", 10),
            ),
            channel_overrides=channel_overrides,
        )


@dataclass
class TemporalConfig:
    """Temporal Server 连接配置。"""
    host: str = "localhost:7233"
    task_queue: str = "hpagent-task-queue"


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
    result_ttl: int = 3600


@dataclass
class WorkspaceConfig:
    """用户工作区配置。"""
    root: str = ".data/workspace"
    db_path: str = ""
    cleanup_max_age_days: int = 30


@dataclass
class HindsightConfig:
    """Hindsight 长期记忆服务配置。"""
    enabled: bool = True
    base_url: str = "http://hindsight:8888"
    api_key: str = ""
    timeout: float = 30.0          # 默认超时（retain/recall 等轻量操作）
    retain_timeout: float = 30.0   # 记忆提取（异步提交，通常很快）
    recall_timeout: float = 3.0    # 语义检索（快速降级，锦上添花不阻塞）
    reflect_timeout: float = 120.0 # 深度推理（LLM 多轮分析，耗时较长）
    retain_mission: str = (
        "Extract and preserve structured, reusable knowledge from multi-turn "
        "conversations across QQ/NapCat, Console, and Web channels.\n\n"
        "Extraction categories:\n"
        "- world: Stable facts the user states (personal info, technical facts, domain knowledge)\n"
        "- experience: The user's stated preferences, past events, decisions made\n"
        "- observation: Behavioral patterns inferred from how the user interacts\n\n"
        "Guidelines:\n"
        "1. Preserve original context: use the provided context field (channel type, group name, sender) "
        "to enrich extracted facts with provenance\n"
        "2. Prefer specificity over generality: 'uses Python 3.12 with FastAPI for backend APIs' > 'uses Python'\n"
        "3. Detect contradictions: when new information conflicts with existing memories, mark both and let reflect resolve\n"
        "4. Respect tags: extracted memories inherit the document's tags (user, session, group, scope, channel) "
        "for correct isolation boundaries\n"
        "5. Timestamp-aware: use the provided timestamp for temporal reasoning (recency, trend detection)\n"
        "6. Skip ephemera: ignore greetings, one-word replies, tool-call artifacts, and purely functional exchanges"
    )
    reflect_mission: str = (
        "Periodically analyze accumulated memories for a given user to produce higher-level insights.\n\n"
        "Analysis tasks:\n"
        "1. Pattern discovery: identify recurring themes, preferences, and behavioral patterns across sessions\n"
        "2. Contradiction resolution: detect conflicting memories and resolve based on recency, specificity, "
        "and source reliability\n"
        "3. Knowledge abstraction: distill multiple related observations into compact, reusable knowledge entries\n"
        "4. Staleness detection: flag memories that are likely outdated (user stopped using a tool, changed role, etc.)\n"
        "5. Cross-session linking: connect related facts from different sessions/channels that belong to the same user\n\n"
        "Output: synthesized insights that improve future recall relevance and reduce noise."
    )


@dataclass
class SessionConfig:
    """会话存储配置。"""
    backup_dir: str = ".data/active-sessions"
    redis_ttl: int = 86400


@dataclass
class MultiAgentConfig:
    """多Agent模式配置。"""
    strategy: str = "supervisor"       # supervisor | council | workflow
    max_review_rounds: int = 10
    agents_config: str = "config/agents.yaml"


@dataclass
class AgentConfig:
    """Agent 行为参数。"""
    max_history_turns: int = 10
    max_tool_turns: int = 20
    recall_top_n: int = 5
    event_fetch_limit: int = 100
    activity_timeout: int = 300    # process_turn Activity 超时（秒）
    archive_timeout: int = 10
    mode: str = "single"               # "single" | "multi"
    reflect_interval_hours: int = 6    # 记忆反思间隔（小时）
    idle_timeout_minutes: int = 5      # 会话空闲自动关闭时间（分钟）
    multi_agent: MultiAgentConfig = field(default_factory=MultiAgentConfig)

    # —— 上下文工程参数 ——
    context_budget: int = 256000            # 总上下文 token 预算
    generation_headroom: int = 16000        # 留给模型输出的 token 空间
    summary_budget: int = 2000              # 运行摘要最大 token
    memories_budget: int = 2000             # 召回记忆最大 token
    compress_interval: int = 8              # 每 N 轮触发历史压缩（0=禁用）
    checkpoint_interval: int = 10           # 每 N 轮写入中间检查点（0=禁用）
    # 工具结果摘要（替代简单截断）
    tool_result_summary_enabled: bool = True            # 启用 LLM 摘要替代截断
    tool_result_summary_threshold: int = 4000           # 超过此字符数触发摘要
    tool_result_summary_max_chars: int = 1000           # 摘要最大字符数（注入 LLM 的）
    wal_enabled: bool = True             # 启用 WAL 预写日志
    inherit_context: bool = True         # 跨会话上下文继承

# ═══════════════════════════════════════════════════════════════════════════════
# Prompt 配置 —— 从 config/prompts/*.yaml 加载
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PromptsConfig:
    """所有 Agent prompt 模板的统一容器。

    从 config/prompts/ 目录加载四个 YAML 文件：
      - identities.yaml  → channel_map + 各渠道人设
      - guidance.yaml    → 风格引导 + 工具纪律
      - environment.yaml → 运行环境提示 (docker/wsl)
      - system.yaml      → 系统级提示 (跨渠道/上下文文件/记忆/截断)
    """
    identities: Dict[str, str] = field(default_factory=dict)
    guidance: Dict[str, str] = field(default_factory=dict)
    environment: Dict[str, str] = field(default_factory=dict)
    system: Dict[str, str] = field(default_factory=dict)
    tool_summary: Dict[str, Any] = field(default_factory=dict)  # tool_summary.yaml (含嵌套 hints dict)

    @classmethod
    def from_dir(cls, prompts_dir: Path) -> "PromptsConfig":
        """从目录加载所有 prompt YAML 文件。

        Args:
            prompts_dir: prompts/ 目录路径。

        Returns:
            PromptsConfig，缺失文件时对应字段为空 dict。
        """
        import yaml

        def _load(filename: str) -> Dict[str, str]:
            path = prompts_dir / filename
            if not path.exists():
                logger.warning("Prompt file not found: %s", path)
                return {}
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data: dict = yaml.safe_load(f) or {}
                return {k: v.strip() for k, v in data.items() if isinstance(v, str)}
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
                with open(path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning("Failed to load %s: %s", path, e)
                return {}

        return cls(
            identities=_load("identities.yaml"),
            guidance=_load("guidance.yaml"),
            environment=_load("environment.yaml"),
            system=_load("system.yaml"),
            tool_summary=_load_raw("tool_summary.yaml"),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 定义 —— 从 config/agents.yaml 加载（多Agent模式）
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentEntry:
    """单个 Agent 的能力定义。"""
    tag: str = ""
    model_selector: str = "chat"
    system_prompt: str = ""
    tools: list = field(default_factory=list)
    tool_executor: Any = None
    max_tool_turns: int = 5
    cost_tier: str = "default"
    priority: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentEntry":
        return cls(
            tag=data.get("tag", ""),
            model_selector=data.get("model_selector", "chat"),
            system_prompt=data.get("system_prompt", ""),
            tools=data.get("tools", []),
            tool_executor=data.get("tool_executor"),
            max_tool_turns=data.get("max_tool_turns", 5),
            cost_tier=data.get("cost_tier", "default"),
            priority=data.get("priority", 0),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 顶层配置
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AppConfig:
    """应用配置根结构。

    从 config.yaml + models.yaml + prompts/*.yaml + agents.yaml 一次性加载。
    所有配置统一存放在此 dataclass 中，Worker 通过属性访问。
    """
    models: ModelsConfig = field(default_factory=ModelsConfig)
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    hindsight: HindsightConfig = field(default_factory=HindsightConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    agents: List[AgentEntry] = field(default_factory=list)

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
        import yaml

        config_file = Path(path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(config_file, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = yaml.safe_load(f) or {}

        config = cls._from_dict(raw)

        config_dir = config_file.parent

        # 加载模型配置
        if not models_path:
            models_path = str(config_dir / "models.yaml")
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

        # 加载 Agent 定义（多Agent模式）
        agents_path = config_dir / "agents.yaml"
        if agents_path.exists():
            with open(agents_path, "r", encoding="utf-8") as _f:
                agents_raw = yaml.safe_load(_f) or {}
            for item in agents_raw.get("agents", []):
                config.agents.append(AgentEntry.from_dict(item))
            logger.info("Agents loaded: %d from %s", len(config.agents), agents_path)

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
        sess = Path(self.session.backup_dir)
        if not sess.is_absolute():
            self.session.backup_dir = str(_root / sess)

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
        if environ.get("TEMPORAL_HOST"):
            self.temporal.host = environ["TEMPORAL_HOST"]
        if environ.get("TEMPORAL_TASK_QUEUE"):
            self.temporal.task_queue = environ["TEMPORAL_TASK_QUEUE"]
        if environ.get("HINDSIGHT_URL"):
            self.hindsight.base_url = environ["HINDSIGHT_URL"]
        if environ.get("WORKSPACE_ROOT"):
            self.workspace.root = environ["WORKSPACE_ROOT"]
        if environ.get("REDIS_URL"):
            self.redis.url = environ["REDIS_URL"]

    @classmethod
    def _from_dict(cls, raw: Dict[str, Any]) -> "AppConfig":
        """从已解析的 dict 构造 AppConfig。

        每个顶层 section 对应一个子 dataclass，缺失的 section
        使用该 dataclass 的默认构造。
        """
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

            if source is None:
                source = {}
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
            # 对未知 key 发出警告
            unknown = set(source.keys()) - set(defaults.keys())
            if unknown:
                logger.warning(
                    "Unknown keys in config section [%s]: %s",
                    prefix, ", ".join(sorted(unknown)),
                )
            return dataclass_type(**{k: v for k, v in kwargs.items() if k in defaults})

        return cls(
            temporal=_populate(TemporalConfig, raw.get("temporal"), "temporal"),
            redis=_populate(RedisConfig, raw.get("redis"), "redis"),
            sandbox=_populate(SandboxConfig, raw.get("sandbox"), "sandbox"),
            workspace=_populate(WorkspaceConfig, raw.get("workspace"), "workspace"),
            hindsight=_populate(HindsightConfig, raw.get("hindsight"), "hindsight"),
            session=_populate(SessionConfig, raw.get("session"), "session"),
            channels=_populate(ChannelsConfig, raw.get("channels"), "channels"),
            agent=_populate(AgentConfig, raw.get("agent"), "agent"),
        )
