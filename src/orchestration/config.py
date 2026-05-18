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


@dataclass
class ModelEntry:
    """单个模型条目 —— 属于某个类别降级链中的一环。"""
    provider: str = ""       # 引用 providers 中的 key
    model: str = ""
    max_tokens: int = 2048
    timeout: float = 30.0


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
    chat: List[ModelEntry] = field(default_factory=list)
    embedding: List[ModelEntry] = field(default_factory=list)
    image: List[ModelEntry] = field(default_factory=list)
    reasoning: List[ModelEntry] = field(default_factory=list)

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
                "max_tokens": entry.max_tokens,
                "timeout": entry.timeout,
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
                        model=item.get("model", ""),
                        max_tokens=item.get("max_tokens", 2048),
                        timeout=item.get("timeout", 30.0),
                    ))
            return result

        return cls(
            providers=providers,
            chat=_parse_entries("chat"),
            embedding=_parse_entries("embedding"),
            image=_parse_entries("image"),
            reasoning=_parse_entries("reasoning"),
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
    root: str = ".hpagent/data/workspace"
    db_path: str = ""
    cleanup_max_age_days: int = 30


@dataclass
class HindsightConfig:
    """Hindsight 长期记忆服务配置。"""
    enabled: bool = True
    base_url: str = "http://hindsight:8000"
    api_key: str = ""
    timeout: float = 30.0


@dataclass
class SessionConfig:
    """会话存储配置。"""
    backup_dir: str = ".hpagent/data/sessions"
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
    activity_timeout: int = 120
    archive_timeout: int = 10
    mode: str = "single"               # "single" | "multi"
    multi_agent: MultiAgentConfig = field(default_factory=MultiAgentConfig)


# ═══════════════════════════════════════════════════════════════════════════════
# 顶层配置
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AppConfig:
    """应用配置根结构。

    模型配置独立于 config/models.yaml，由 ModelsConfig.from_yaml() 加载。
    """
    models: ModelsConfig = field(default_factory=ModelsConfig)
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    hindsight: HindsightConfig = field(default_factory=HindsightConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

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

        # 加载模型配置
        if not models_path:
            models_path = str(config_file.parent / "models.yaml")
        mp = Path(models_path)
        if mp.exists():
            config.models = ModelsConfig.from_yaml(str(mp))
        else:
            logger.warning("Models config not found: %s, using defaults", mp)

        config._apply_env_overrides(_os.environ)
        return config

    def _apply_env_overrides(self, environ: dict) -> None:
        """用环境变量覆盖 YAML 中的值（Docker Compose 传入）。"""
        if environ.get("TEMPORAL_HOST"):
            self.temporal.host = environ["TEMPORAL_HOST"]
        if environ.get("HINDSIGHT_URL"):
            self.hindsight.base_url = environ["HINDSIGHT_URL"]
        if environ.get("WORKSPACE_ROOT"):
            self.workspace.root = environ["WORKSPACE_ROOT"]

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
            agent=_populate(AgentConfig, raw.get("agent"), "agent"),
        )
