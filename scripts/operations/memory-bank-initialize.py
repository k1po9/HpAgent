#!/usr/bin/env python3
"""Initialize controlled Banks from trusted active Accounts on an empty target only."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


async def initialize(args):
    from application.memory_readiness import inspect_memory
    from memory.configuration import local_accounts
    from memory.hindsight_client import HindsightClient, HindsightContractError
    from memory.quality_config import MemoryQualityConfig, fingerprint, load_memory_prompts

    database, url = os.getenv('WORKER_DATABASE_URL'), os.getenv('HINDSIGHT_URL')
    if not args.senders_stopped or not database or not url:
        raise HindsightContractError('explicit_target_and_stopped_senders_required')
    quality = MemoryQualityConfig.from_yaml(args.config)
    prompts = load_memory_prompts(args.prompts)
    remote = HindsightClient(url, api_key=os.getenv('HINDSIGHT_API_KEY', ''),
                            quality_config=quality, memory_prompts=prompts)
    before = await inspect_memory(database, remote, require_empty=True)
    if not before['data_ready']:
        raise HindsightContractError('empty_target_not_ready')
    accounts = local_accounts(database, require_idle=True)
    for account in accounts:
        # Existing divergent Banks are rejected by the Adapter, never overwritten.
        await remote.ensure_bank_config(account)
    after = await inspect_memory(database, remote, require_empty=True)
    if not after['data_ready']:
        raise HindsightContractError('empty_target_changed_during_initialization')
    return dict(status='initialized', verified_banks=len(accounts),
                profile_hash=fingerprint(quality.bank_updates(prompts)),
                data_ready=True, provider_requests=0,
                senders_stopped='operator_attestation_not_process_proof')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/hindsight.yaml')
    parser.add_argument('--prompts', type=Path, default=ROOT / 'config/prompts/memory.yaml')
    parser.add_argument('--senders-stopped', action='store_true')
    args = parser.parse_args()
    try:
        result = asyncio.run(initialize(args))
    except Exception as exc:
        print(json.dumps(dict(status='failed', error_code=getattr(exc, 'code', 'memory_bank_initialization_failed'))))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
