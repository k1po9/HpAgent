import pytest
import yaml

from application import prompt_catalog
from web_artifacts.generator import WebArtifactGenerator


def test_all_agent_roles_and_stages_are_configured():
    for name in (
        "main", "worker", "subagent", "research", "html", "planning",
        "evaluation", "step_execution", "handoff",
    ):
        assert prompt_catalog.agent_prompt(name)
    assert "workspace_save_requested=false" in prompt_catalog.agent_prompt("main")
    assert "最多 16 条" in prompt_catalog.agent_prompt("research")


def test_html_inputs_remain_separate_untrusted_json():
    import json

    source = '来源包含 {花括号} 和 "引号"'
    prompt = WebArtifactGenerator._prompt(source, "中文要求", "<html>旧版</html>")
    assert prompt.startswith(prompt_catalog.agent_prompt("html"))
    assert json.loads(prompt.splitlines()[-1]) == {
        "source_markdown": source,
        "design_instruction": "中文要求",
        "previous_html": "<html>旧版</html>",
    }


@pytest.mark.parametrize("contents,error", [
    ("[]", ValueError), ("main: 12", ValueError), ("main: ''", ValueError),
    ("main: [", yaml.YAMLError), (None, FileNotFoundError),
])
def test_bad_catalog_fails_explicitly(tmp_path, monkeypatch, contents, error):
    module = tmp_path / "src" / "application" / "prompt_catalog.py"
    directory = tmp_path / "config" / "prompts"
    directory.mkdir(parents=True)
    if contents is not None:
        (directory / "agents.yaml").write_text(contents, encoding="utf-8")
    monkeypatch.setattr(prompt_catalog, "__file__", str(module))
    prompt_catalog._catalog.cache_clear()
    try:
        with pytest.raises(error):
            prompt_catalog.agent_prompt("main")
    finally:
        prompt_catalog._catalog.cache_clear()


def test_unknown_prompt_is_not_silently_ignored():
    with pytest.raises(KeyError):
        prompt_catalog.agent_prompt("unknown_role")
