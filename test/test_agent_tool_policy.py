from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from langchain_core.tools import StructuredTool

from application.tool_policy import (
    HARNESS_TOOLS,
    ToolPolicyError,
    agent_role,
    load_tool_policy,
    tool_policy,
)
from sandbox.sandbox_manager import SandboxManager


@pytest.fixture
def configuration():
    path = Path(__file__).resolve().parents[1] / "config" / "agent_tools.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load(configuration, tmp_path):
    path = tmp_path / "agent_tools.yaml"
    path.write_text(yaml.safe_dump(configuration, allow_unicode=True), encoding="utf-8")
    return load_tool_policy(path)


def test_default_roles_preserve_coordinator_boundaries():
    policy = tool_policy()
    assert policy.allows("main", "accept_work")
    assert not policy.allows("worker", "accept_work")
    assert policy.allows("worker", "delegate_work")
    assert not policy.allows("main", "delegate_work")
    assert policy.allows("worker", "capability__text__v1")
    assert not policy.allows("main", "capability__text__v1")
    assert policy.allows("subagent", "read_file")
    assert not policy.allows("subagent", "web_search")
    assert not policy.allows("subagent", "result__save")
    assert policy.allows("subagent", "remote_read", "mcp")
    assert not policy.allows("main", "fs_read", "mcp")
    for role in ("research", "html", "planning", "evaluation", "tool_summary", "memory_rewrite"):
        assert not any(policy.allows(role, name) for name in HARNESS_TOOLS)


@pytest.mark.parametrize("change", [
    lambda cfg: cfg.update(schema_version=2),
    lambda cfg: cfg.update(typo=True),
    lambda cfg: cfg["roles"]["worker"]["groups"].append("typo"),
    lambda cfg: cfg["groups"]["web"]["tools"].append("typo"),
    lambda cfg: cfg["groups"]["web"].update(exposure="semantic"),
    lambda cfg: cfg["groups"]["web"]["tools"].append("read_file"),
    lambda cfg: cfg["groups"].pop("code_read"),
    lambda cfg: cfg["groups"]["shell"].update(tools=["" ]),
    lambda cfg: cfg["roles"]["subagent"].update(grant="configured"),
    lambda cfg: cfg["roles"]["research"].update(groups=["web"]),
    lambda cfg: cfg["roles"].pop("main"),
    lambda cfg: cfg["roles"]["worker"]["groups"].append("work_management"),
    lambda cfg: cfg["roles"]["main"]["groups"].append("delegation"),
    lambda cfg: cfg["roles"]["subagent"]["groups"].append("web"),
    lambda cfg: cfg.update(schema_version=True),
])
def test_configuration_errors_do_not_silently_grant_tools(configuration, tmp_path, change):
    change(configuration)
    with pytest.raises(ValueError):
        load(configuration, tmp_path)


@pytest.mark.parametrize("source,execution,expected", [
    ("chat", "root", "main"), ("work", "root", "worker"),
    ("work", "subagent", "subagent"),
])
def test_role_comes_from_execution_identity(source, execution, expected):
    assert agent_role(source, execution) == expected


def test_unknown_execution_role_is_denied():
    with pytest.raises(ToolPolicyError):
        agent_role("work", "capability")


async def test_removing_group_prevents_selection_and_execution(configuration, tmp_path, monkeypatch):
    from sandbox import sandbox_manager

    configuration["roles"]["main"]["groups"].remove("code_write")
    policy = load(configuration, tmp_path)
    monkeypatch.setattr(sandbox_manager, "tool_policy", lambda: policy)
    manager = SandboxManager(nsjail_enabled=False)
    sandbox_id = manager.create_execution_sandbox("resource", str(tmp_path), session_context={"source_kind": "chat"})
    try:
        sandbox = manager.get_sandbox_for_execution("resource")
        manifests, audit = await sandbox.select_tools("write a file", top_k=12)
        assert "fs_write" not in audit["tools"]
        assert all(tool["function"]["name"] != "fs_write" for tool in manifests)
        result, _ = await sandbox.execute("fs_write", {"path": "should-not-exist", "content": "data"})
        assert not result.success
        assert not (tmp_path / "should-not-exist").exists()
        assert sandbox.get_tool_category("fs_write") is None
        assert sandbox.get_tool_category("fs_read") == "native"
    finally:
        manager.destroy_sandbox(sandbox_id)
        manager.close()


async def test_child_registration_intersects_parent_manifest_and_read_only_metadata(tmp_path):
    def remote_read():
        return "read"

    def remote_write():
        return "write"

    read = StructuredTool.from_function(remote_read, description="读取资料", metadata={"side_effect_class": "read_only"})
    write = StructuredTool.from_function(remote_write, description="写入资料", metadata={"side_effect_class": "idempotent_write"})
    manager = SandboxManager(nsjail_enabled=False, mcp_manager=SimpleNamespace(get_cached_tools=lambda: [read, write]))
    sandbox_id = manager.create_execution_sandbox("child", str(tmp_path), session_context={
        "source_kind": "work", "execution_role": "subagent",
        "parent_tool_names": ["fs_read", "remote_read", "remote_write", "Bash"],
    })
    try:
        sandbox = manager.get_sandbox_for_execution("child")
        _, audit = await sandbox.select_tools("read", top_k=12)
        assert set(audit["tools"]) == {"fs_read", "remote_read"}
        for name in ("Glob", "remote_write", "Bash"):
            assert sandbox.get_tool_category(name) is None
    finally:
        manager.destroy_sandbox(sandbox_id)
        manager.close()


async def test_configured_exposure_changes_actual_selection(configuration, tmp_path, monkeypatch):
    from sandbox import sandbox_manager

    configuration["groups"]["code_read"]["exposure"] = "always"
    policy = load(configuration, tmp_path)
    monkeypatch.setattr(sandbox_manager, "tool_policy", lambda: policy)
    manager = SandboxManager(nsjail_enabled=False)
    sandbox_id = manager.create_execution_sandbox("root", str(tmp_path))
    try:
        sandbox = manager.get_sandbox_for_execution("root")
        _, audit = await sandbox.select_tools("", top_k=12)
        assert set(audit["always_candidates"]) == {"Glob", "Grep", "fs_read"}
    finally:
        manager.destroy_sandbox(sandbox_id)
        manager.close()


def test_missing_file_and_invalid_yaml_fail_explicitly(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_tool_policy(tmp_path / "missing.yaml")
    path = tmp_path / "invalid.yaml"
    path.write_text("groups: [", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_tool_policy(path)


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.yaml"
    path.write_text("schema_version: 1\nroles: {}\nroles: {}\n", encoding="utf-8")
    with pytest.raises(ToolPolicyError, match="不重复"):
        load_tool_policy(path)


def test_special_tool_catalog_tracks_production_registration():
    from agent_activities.composition import compose_capabilities
    from agent_activities.web_tools import NAMES
    from agent_workflows.delegation_contracts import DELEGATE_TOOL

    capabilities = compose_capabilities(None, services=frozenset({"research", "html", "text"}))
    names = {tool["function"]["name"] for tool in capabilities.discover("work")}
    assert names | NAMES | {DELEGATE_TOOL} == HARNESS_TOOLS


def test_capability_discovery_applies_role_configuration_and_service_availability(configuration, tmp_path):
    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.composition import compose_capabilities

    for group in ("research", "html", "results"):
        configuration["roles"]["main"]["groups"].remove(group)
    policy = load(configuration, tmp_path)
    registered = compose_capabilities(None, services=frozenset({"research", "html", "text"}))
    capabilities = CapabilityActivities(None, registered.facts, tuple(registered.bindings.values()), policy=policy)
    assert capabilities.discover("chat") == []
    assert "capability__research__v1" in {tool["function"]["name"] for tool in capabilities.discover("work")}
    unavailable = compose_capabilities(None, services=frozenset())
    assert unavailable.discover("work") == []


@pytest.mark.parametrize("directory", ["src", ""])
def test_policy_path_supports_checkout_and_flat_worker_layout(configuration, tmp_path, monkeypatch, directory):
    from application import tool_policy as module

    path = tmp_path / "config"
    path.mkdir()
    (path / "agent_tools.yaml").write_text(yaml.safe_dump(deepcopy(configuration)), encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(tmp_path / directory / "application" / "tool_policy.py"))
    module.tool_policy.cache_clear()
    try:
        assert module.tool_policy().allows("main", "web_search")
    finally:
        module.tool_policy.cache_clear()
