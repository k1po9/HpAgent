"""模型工具分配配置；不代替资源权限、服务依赖与执行 fencing。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


def harness_tool_names() -> frozenset[str]:
    # Import only when loading policy, after Activity composition modules exist.
    # Capability identities and versions come from the trusted catalog once.
    from agent_activities.composition import capability_registrations

    return frozenset({
        "web_search", "web_fetch", "delegate_work",
        "result__adopt", "result__save", "result__evidence",
        *(entry.binding.tool_name for entry in capability_registrations()),
    })



class ToolPolicyError(ValueError):
    pass


class PolicyYamlLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
            raise ToolPolicyError("工具配置只允许不重复的文本键")
        return super().construct_mapping(node, deep=deep)


class ToolGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    description: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    category: Literal["mcp", "skill"] | None = None
    exposure: Literal["existing", "always", "semantic"]

    @model_validator(mode="after")
    def validate_members(self):
        if bool(self.tools) == bool(self.category) or len(set(self.tools)) != len(self.tools):
            raise ValueError("工具组必须指定不重复的工具名列表或一个动态类别")
        if any(not name.strip() for name in self.tools):
            raise ValueError("工具名不能为空")
        if set(self.tools) & harness_tool_names() and self.exposure != "always":
            raise ValueError("根执行追加的专用工具只支持 always；可通过角色工具组关闭")
        return self


class AgentTools(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    description: str = Field(min_length=1)
    groups: tuple[str, ...]
    grant: Literal["configured", "parent_read_only_subset", "none"]


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    groups: dict[str, ToolGroup]
    roles: dict[str, AgentTools]

    @model_validator(mode="after")
    def validate_references(self):
        expected = {"main", "worker", "subagent", "research", "html", "planning", "evaluation", "tool_summary", "memory_rewrite"}
        if set(self.roles) != expected:
            raise ValueError("必须配置全部已支持的 agent 角色")
        names = [name for group in self.groups.values() for name in group.tools]
        categories = [group.category for group in self.groups.values() if group.category]
        if len(names) != len(set(names)) or len(categories) != len(set(categories)):
            raise ValueError("工具名或动态类别不能分属多个组")
        for role, spec in self.roles.items():
            if len(set(spec.groups)) != len(spec.groups) or set(spec.groups) - self.groups.keys():
                raise ValueError(f"角色 {role} 引用了重复或未知的工具组")
            grant = "parent_read_only_subset" if role == "subagent" else (
                "configured" if role in {"main", "worker"} else "none"
            )
            if spec.grant != grant or (grant == "none" and spec.groups):
                raise ValueError(f"角色 {role} 的授予方式不受支持")
        return self

    def group_for(self, name: str, category: str | None = None) -> str | None:
        for key, group in self.groups.items():
            if name in group.tools:
                if category in {"mcp", "skill"}:
                    return None
                return key
        if category in {"mcp", "skill"}:
            return next((key for key, group in self.groups.items() if group.category == category), None)
        return None

    def allows(self, role: str, name: str, category: str | None = None) -> bool:
        return self.group_for(name, category) in self.roles[role].groups

    def require(self, role: str, name: str, category: str | None = None) -> None:
        if not self.allows(role, name, category):
            raise ToolPolicyError(f"角色 {role} 未获准使用工具 {name}")


def agent_role(source_kind: str, execution_role: str = "root") -> str:
    if execution_role == "subagent":
        return "subagent"
    if execution_role != "root" or source_kind not in {"chat", "work"}:
        raise ToolPolicyError("执行身份不支持交互工具分配")
    return "worker" if source_kind == "work" else "main"


def load_tool_policy(path: Path) -> ToolPolicy:
    from agent_activities.composition import capability_registrations
    from sandbox.tools.routing.contracts import NATIVE_ROUTING_SPECS

    with path.open(encoding="utf-8") as stream:
        data = yaml.load(stream, Loader=PolicyYamlLoader)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
        raise ToolPolicyError("工具配置必须包含整数 schema_version")
    policy = ToolPolicy.model_validate(data)
    declared = {name for group in policy.groups.values() for name in group.tools}
    unknown = declared - NATIVE_ROUTING_SPECS.keys() - harness_tool_names()
    if unknown:
        raise ToolPolicyError("配置包含未注册工具：" + ", ".join(sorted(unknown)))
    missing = NATIVE_ROUTING_SPECS.keys() - declared
    if missing:
        raise ToolPolicyError("原生工具缺少归属组：" + ", ".join(sorted(missing)))
    work_tools = {name for name, spec in NATIVE_ROUTING_SPECS.items() if spec.capability.startswith("work.")}
    forbidden = {
        "main": {"delegate_work"} | {
            entry.binding.tool_name for entry in capability_registrations()
            if "chat" not in entry.definition.allowed_surfaces
        },
        "worker": work_tools | {
            entry.binding.tool_name for entry in capability_registrations()
            if "work" not in entry.definition.allowed_surfaces
        },
        "subagent": work_tools | harness_tool_names(),
    }
    for role, names in forbidden.items():
        if any(policy.allows(role, name) for name in names):
            raise ToolPolicyError(f"角色 {role} 的工具分配超出服务端执行边界")
    return policy


@lru_cache(maxsize=1)
def tool_policy() -> ToolPolicy:
    source_root = Path(__file__).resolve().parents[1]
    project_root = source_root.parent if source_root.name == "src" else source_root
    path = project_root / "config" / "agent_tools.yaml"
    try:
        return load_tool_policy(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ToolPolicyError(f"工具分配配置无效或不可读取：{path}") from exc
