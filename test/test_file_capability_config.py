from __future__ import annotations

from pathlib import Path

import pytest

from workspace.file_capability_config import FileCapabilityConfig
from workspace.file_scope import RunFileScopeUnavailable


def test_file_maximum_parse_error_does_not_expose_input_or_exception_chain(tmp_path):
    with pytest.raises(RunFileScopeUnavailable, match="FILE_MAX_BYTES") as error:
        FileCapabilityConfig.from_environment(tmp_path, None, {"FILE_MAX_BYTES": "secret-invalid"})
    assert "secret-invalid" not in str(error.value)
    assert error.value.__cause__ is None


def test_file_capability_defaults_closed(tmp_path: Path) -> None:
    config = FileCapabilityConfig.from_environment(tmp_path, None, {})
    assert config.upload_enabled is False
    assert config.transform_enabled is False
    assert config.shell_enabled is False
    assert config.store_root is None


def test_enabled_roots_are_pairwise_isolated(tmp_path: Path) -> None:
    workspace = tmp_path / "git"
    store = tmp_path / "objects"
    run = tmp_path / "runs"
    config = FileCapabilityConfig.from_environment(
        workspace,
        "postgresql://worker",
        {
            "WEB_FILE_UPLOAD_ENABLED": "true",
            "FILE_STORE_ROOT": str(store),
            "FILE_RUN_ROOT": str(run),
            "FILE_MAX_BYTES": "1024",
        },
    )
    assert config.store_root == store.resolve()
    assert config.run_root == run.resolve()
    assert config.max_bytes == 1024


@pytest.mark.parametrize("root_name", ["FILE_STORE_ROOT", "FILE_RUN_ROOT"])
def test_file_roots_cannot_overlap_git_workspace(
    tmp_path: Path, root_name: str,
) -> None:
    workspace = tmp_path / "git"
    environment = {
        "WEB_FILE_UPLOAD_ENABLED": "true",
        "FILE_STORE_ROOT": str(tmp_path / "objects"),
        "FILE_RUN_ROOT": str(tmp_path / "runs"),
    }
    environment[root_name] = str(workspace / "nested")
    with pytest.raises(RunFileScopeUnavailable, match="must not overlap"):
        FileCapabilityConfig.from_environment(
            workspace, "postgresql://worker", environment
        )


def test_file_store_and_run_root_cannot_overlap(tmp_path: Path) -> None:
    with pytest.raises(RunFileScopeUnavailable, match="must not overlap"):
        FileCapabilityConfig.from_environment(
            tmp_path / "git",
            "postgresql://worker",
            {
                "WEB_FILE_UPLOAD_ENABLED": "true",
                "FILE_STORE_ROOT": str(tmp_path / "files"),
                "FILE_RUN_ROOT": str(tmp_path / "files" / "runs"),
            },
        )


def test_production_requires_absolute_roots_and_enforced_budget(tmp_path: Path) -> None:
    base = {
        "HPAGENT_ENV": "production",
        "WEB_FILE_UPLOAD_ENABLED": "true",
        "FILE_STORE_ROOT": "relative-store",
        "FILE_RUN_ROOT": "relative-runs",
    }
    with pytest.raises(RunFileScopeUnavailable, match="RUN_BUDGET_MODE=enforce"):
        FileCapabilityConfig.from_environment(
            tmp_path, "postgresql://worker", base
        )
    base["RUN_BUDGET_MODE"] = "enforce"
    with pytest.raises(RunFileScopeUnavailable, match="absolute"):
        FileCapabilityConfig.from_environment(
            tmp_path, "postgresql://worker", base
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WEB_FILE_UPLOAD_ENABLED", "yes"),
        ("WEB_FILE_TRANSFORM_ENABLED", "1"),
        ("WEB_FILE_SHELL_ENABLED", "TRUE-ish"),
    ],
)
def test_feature_flags_reject_ambiguous_values(
    tmp_path: Path, name: str, value: str,
) -> None:
    with pytest.raises(RunFileScopeUnavailable, match="exactly true or false"):
        FileCapabilityConfig.from_environment(tmp_path, None, {name: value})


@pytest.mark.parametrize(('field', 'alias'), [
    ('file_capability_enabled', 'upload_enabled'),
    ('file_transform_enabled', 'transform_enabled'),
    ('file_shell_enabled', 'shell_enabled'),
])
@pytest.mark.parametrize(('new', 'old', 'expected'), [
    ({}, {}, False), ({}, {'value': False}, False), ({}, {'value': True}, True),
    ({'value': False}, {'value': True}, False),
    ({'value': True}, {'value': False}, True),
])
def test_constructor_aliases_share_only_canonical_state(field, alias, new, old, expected, caplog):
    from dataclasses import asdict, replace

    kwargs = {field: new['value']} if new else {}
    if old:
        kwargs[alias] = old['value']
    config = FileCapabilityConfig(store_root=None, run_root=None, max_bytes=1024, **kwargs)
    assert getattr(config, field) is expected
    assert getattr(config, alias) is expected
    assert alias not in asdict(config)
    assert getattr(replace(config, **{field: False}), alias) is False
    assert alias in caplog.text and 'deprecated' in caplog.text


@pytest.mark.parametrize(('canonical', 'legacy'), [
    ('FILE_CAPABILITY_ENABLED', 'WEB_FILE_UPLOAD_ENABLED'),
    ('FILE_TRANSFORM_ENABLED', 'WEB_FILE_TRANSFORM_ENABLED'),
    ('FILE_SHELL_ENABLED', 'WEB_FILE_SHELL_ENABLED'),
])
@pytest.mark.parametrize(('new', 'old', 'expected'), [
    (None, None, False), (None, 'false', False), (None, ' TrUe ', True),
    ('false', 'true', False), ('true', 'false', True), (' false ', 'secret-invalid', False),
    ('', 'true', 'error'), ('  ', 'false', 'error'), ('secret-invalid', 'true', 'error'),
    (None, '', 'error'), (None, ' ', 'error'), (None, 'secret-invalid', 'error'),
])
def test_environment_flag_matrix(tmp_path, canonical, legacy, new, old, expected, caplog):
    # Other capabilities are enabled only when needed to satisfy dependency checks.
    env = {'FILE_CAPABILITY_ENABLED': 'true', 'FILE_STORE_ROOT': str(tmp_path / 'objects'),
           'FILE_RUN_ROOT': str(tmp_path / 'runs')}
    env.pop(canonical, None)
    if new is not None:
        env[canonical] = new
    if old is not None:
        env[legacy] = old
    if expected == 'error':
        with pytest.raises(RunFileScopeUnavailable, match=canonical if new is not None else legacy) as error:
            FileCapabilityConfig.from_environment(tmp_path / 'git', 'postgresql://unused', env)
        assert 'secret-invalid' not in str(error.value)
    else:
        config = FileCapabilityConfig.from_environment(tmp_path / 'git', 'postgresql://unused', env)
        assert getattr(config, canonical.lower()) is expected
    if old is not None:
        assert legacy in caplog.text and 'deprecated' in caplog.text
    assert 'secret-invalid' not in caplog.text
