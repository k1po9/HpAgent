"""Explicit configuration maintenance; Chat reads never apply existing-bank changes."""
from __future__ import annotations

import json
import os
from pathlib import Path

from memory.hindsight_client import HindsightContractError, HindsightRejected
from memory.quality_config import BankSettings, MemoryConfigError, fingerprint

MISSIONS = {"retain_mission", "observations_mission", "retain_custom_instructions"}


def safe_value(value):
    if isinstance(value, str):
        return {"sha256": fingerprint(value), "characters": len(value)}
    if isinstance(value, (dict, list)):
        return {"sha256": fingerprint(value)}
    return value


def configuration_diff(state, updates):
    return {key: {"desired": safe_value(value),
                  "effective": safe_value(state["config"].get(key)),
                  "override": safe_value(state["overrides"].get(key)),
                  "matches": (state["overrides"].get(key) is None if value is None
                              else state["config"].get(key) == value)}
            for key, value in updates.items()}


async def require_remote_idle(remote, account):
    async with remote._sdk() as sdk:
        for status in ("pending", "processing", "failed"):
            page = await sdk.operations.list_operations(
                remote._bank_id_for(account), status=status, limit=1, offset=0,
                _request_timeout=remote.timeout)
            if page.total:
                raise HindsightContractError("memory_operations_not_drained")
        stats = await sdk.banks.get_agent_stats(remote._bank_id_for(account),
                                               _request_timeout=remote.timeout)
        if stats.pending_consolidation or stats.failed_consolidation:
            raise HindsightContractError("memory_consolidation_not_drained")


def local_accounts(database, requested=(), *, require_idle=False, expected_profile_hash=None):
    from persistence.migrate import verify_schema
    from persistence.uow import UnitOfWork

    verify_schema(database)
    with UnitOfWork(database) as uow:
        uow.execute("SET TRANSACTION READ ONLY")
        rows = uow.execute("SELECT account_id FROM accounts WHERE status='active'").fetchall()
        allowed = {str(row['account_id']) for row in rows}
        accounts = set(requested) if requested else allowed
        if not accounts <= allowed:
            raise MemoryConfigError("account_not_available")
        if require_idle and accounts:
            pending = uow.execute(
                "SELECT kind,status,send_state,request_payload FROM memory_actions WHERE account_id=ANY(%s::uuid[]) "
                "AND (status NOT IN ('verified','failed') OR send_state IN ('sending','unknown'))",
                (list(accounts),),
            ).fetchall()
            for row in pending:
                snapshot = (row['request_payload'] or {}).get('quality_snapshot') or {}
                # A failed provisioning attempt has not obtained source sending
                # qualification. Repairing exactly its frozen profile is safe;
                # switching its profile or retrying a legacy/unknown sender is not.
                matching_unsent = (
                    row['kind'] == 'retain' and row['send_state'] == 'idle'
                    and row['status'] in {'queued', 'uncertain'}
                    and expected_profile_hash is not None
                    and snapshot.get('config_hash') == expected_profile_hash)
                if not matching_unsent:
                    raise MemoryConfigError("memory_actions_not_drained")
    return sorted(accounts)


async def inspect_banks(remote, accounts, updates):
    states, report = {}, []
    await remote.check_version()
    for account in accounts:
        try:
            state = await remote.get_bank_config(account)
        except HindsightRejected as exc:
            if exc.status != 404:
                raise
            report.append({"account_id": account, "status": "missing_new_bank_lazy_init"})
            continue
        states[account] = state
        strategies = [key for key in ("retain_default_strategy", "retain_strategies",
                                     "consolidation_strategies") if state["config"].get(key)]
        report.append({"account_id": account, "status": "strategy_conflict" if strategies else "inspected",
                       "fields": configuration_diff(state, updates),
                       "strategy_conflicts": strategies})
    return states, report


def write_backup(path, states, updates):
    # Originals include only our managed behavior fields, never service credentials.
    originals = {account: {key: state['overrides'].get(key) for key in updates}
                 for account, state in states.items()}
    payload = json.dumps({"api_version": "1.1.0", "banks": originals}, ensure_ascii=False, indent=2)
    fd = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def read_backup(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('api_version') != '1.1.0' or not isinstance(data.get('banks'), dict):
        raise MemoryConfigError("backup")
    for updates in data['banks'].values():
        if not isinstance(updates, dict) or set(updates) - (set(BankSettings.model_fields) | MISSIONS):
            raise MemoryConfigError("backup.fields")
        BankSettings.model_validate({key: value for key, value in updates.items() if key not in MISSIONS})
        if any(value is not None and not isinstance(value, str)
               for key, value in updates.items() if key in MISSIONS):
            raise MemoryConfigError("backup.missions")
    return data['banks']
