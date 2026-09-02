from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/check_mcp_health.py"
SPEC = importlib.util.spec_from_file_location("check_mcp_health", SCRIPT_PATH)
assert SPEC and SPEC.loader
health = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = health
SPEC.loader.exec_module(health)


def test_tool_security_annotations_are_conservative():
    read_only = health.annotate_tool("search", {"side_effect_class": "read_only"})
    unconfigured = health.annotate_tool("surprise", None)

    assert read_only.risk == "low"
    assert read_only.side_effect_class == "read_only"
    assert unconfigured.risk == "unreviewed"
    assert unconfigured.side_effect_class == "unknown"
    assert unconfigured.configured is False


def test_remote_cleartext_endpoint_is_flagged():
    label, warnings = health.endpoint_security({"url": "http://example.test/mcp"})

    assert label == "insecure_cleartext"
    assert warnings


def test_load_config_tracks_missing_environment_variables(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_HEALTH_TEST_TOKEN", raising=False)
    config = tmp_path / "servers.yaml"
    config.write_text(
        "servers:\n"
        "  demo:\n"
        "    url: https://example.test/mcp\n"
        "    headers:\n"
        "      Authorization: Bearer ${MCP_HEALTH_TEST_TOKEN}\n",
        encoding="utf-8",
    )

    loaded, missing = health.load_config(config)

    assert loaded["servers"]["demo"]["headers"]["Authorization"] == "Bearer "
    assert missing == {"demo": {"MCP_HEALTH_TEST_TOKEN"}}
