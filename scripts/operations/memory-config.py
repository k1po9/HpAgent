#!/usr/bin/env python3
"""Validate, inspect, plan, apply, or restore managed Hindsight 1.1.0 settings."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


async def run(args):
    from memory.quality_config import MemoryQualityConfig, fingerprint, load_memory_prompts

    if args.action == 'restore':
        updates = {}
    else:
        quality = MemoryQualityConfig.from_yaml(args.config)
        prompts = load_memory_prompts(args.prompts)
        updates = quality.bank_updates(prompts)
    if args.action == 'validate':
        snapshot = quality.snapshot(prompts)
        return {"status": "valid", "profile_hash": fingerprint(updates),
                "config_hash": snapshot['config_hash'], "service_hash": snapshot['service_hash'],
                "text_search": snapshot['text_search'],
                "reflect": "inactive", "service": "local_only_not_remote_verified"}

    from memory.configuration import (
        inspect_banks,
        local_accounts,
        read_backup,
        require_remote_idle,
        write_backup,
    )
    from memory.hindsight_client import HindsightClient, HindsightContractError

    if not os.getenv('WORKER_DATABASE_URL') or not os.getenv('HINDSIGHT_URL'):
        raise HindsightContractError('explicit_memory_target_required')
    mutating = args.action in {'apply', 'restore'}
    if mutating and (not args.senders_stopped or args.backup is None):
        raise HindsightContractError('senders_stopped_and_backup_required')
    restore = read_backup(args.backup) if args.action == 'restore' else None
    requested = list(restore) if restore is not None else [str(a) for a in args.account]
    accounts = local_accounts(
        os.environ['WORKER_DATABASE_URL'], requested, require_idle=mutating,
        expected_profile_hash=quality.snapshot(prompts)['config_hash'] if args.action == 'apply' else None)
    remote = HindsightClient(os.environ['HINDSIGHT_URL'], api_key=os.getenv('HINDSIGHT_API_KEY', ''))
    states, report = await inspect_banks(remote, accounts, updates)
    if mutating:
        if any(row.get('strategy_conflicts') for row in report):
            raise HindsightContractError('memory_bank_strategy_conflict')
        if restore is not None and set(restore) != set(states):
            raise HindsightContractError('memory_restore_bank_missing')
        # Preflight the entire selected set before the first write.
        for account in states:
            await require_remote_idle(remote, account)
        if args.action == 'apply':
            write_backup(args.backup, states, updates)
        for account in states:
            desired = restore[account] if restore is not None else updates
            await remote.update_bank_config(account, desired)
        return {"status": "restored" if restore is not None else "applied",
                "verified_banks": len(states), "profile_hash": fingerprint(restore if restore is not None else updates)}
    return {"status": args.action, "api_version": "1.1.0", "banks": report,
            "reflect": "inactive", "service": "not_remote_verified"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'inspect', 'plan', 'apply', 'restore'))
    parser.add_argument('--config', type=Path, default=ROOT / 'config/hindsight.yaml')
    parser.add_argument('--prompts', type=Path, default=ROOT / 'config/prompts/memory.yaml')
    parser.add_argument('--account', type=UUID, action='append', default=[])
    parser.add_argument('--senders-stopped', action='store_true',
                        help='Operator attests all old/new memory senders are stopped; not auto-detected.')
    parser.add_argument('--backup', type=Path, help='New mode-0600 backup for apply; existing file for restore.')
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_code": getattr(exc, 'code', 'memory_configuration_error')},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
