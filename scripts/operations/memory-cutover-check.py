#!/usr/bin/env python3
"""Read-only memory data gate. Never stops services, migrates or deletes data."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-empty', action='store_true', help='Require a fresh memory target')
    parser.add_argument('--service-only', action='store_true', help='Inspect process/DB without local source inventory')
    parser.add_argument('--service-container', help='Explicit Hindsight Docker container; operations authority only')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[2] / 'config/hindsight.yaml')
    parser.add_argument('--memory-db-host')
    parser.add_argument('--memory-db-port', type=int, default=5432)
    parser.add_argument('--memory-db-name')
    parser.add_argument('--memory-db-schema', default='public')
    args = parser.parse_args()
    if args.service_only and args.require_empty:
        parser.error('--require-empty requires the local/remote data inventory')
    if args.service_only and not args.service_container:
        parser.error('--service-only requires --service-container')
    if args.service_container and (not args.memory_db_host or not args.memory_db_name):
        parser.error('Explicit --memory-db-host and --memory-db-name are required')
    if not args.service_only and (not os.getenv('WORKER_DATABASE_URL') or not os.getenv('HINDSIGHT_URL')):
        parser.error('Explicit WORKER_DATABASE_URL and HINDSIGHT_URL are required')

    try:
        service = None
        if args.service_container:
            from memory.quality_config import MemoryQualityConfig
            from memory.service_readiness import inspect_service
            quality = MemoryQualityConfig.from_yaml(args.config)
            service = inspect_service(args.service_container, {
                **quality.target.model_dump(),
                'text_search': quality.text_search_settings().resolved(os.environ),
                'target': {'host': args.memory_db_host, 'port': args.memory_db_port,
                           'database': args.memory_db_name, 'schema': args.memory_db_schema}},
                api_url=None if args.service_only else os.environ['HINDSIGHT_URL'])
        if args.service_only:
            result = service
            ready = service['service_ready']
        elif service is not None and not service['service_ready']:
            result = {'data_ready': False, 'data_check_executed': False, 'service_state': service}
            ready = False
        else:
            from application.memory_readiness import inspect_memory
            from memory.hindsight_client import HindsightClient
            result = asyncio.run(inspect_memory(
                os.environ['WORKER_DATABASE_URL'],
                HindsightClient(os.environ['HINDSIGHT_URL'], api_key=os.getenv('HINDSIGHT_API_KEY', '')),
                require_empty=args.require_empty))
            result['service_state'] = service
            ready = result['data_ready'] and (service is None or service['service_ready'])
    except Exception:
        # Provider/PG exceptions can include connection secrets and source text.
        print(json.dumps({'data_ready': False, 'error': 'memory_readiness_check_failed'}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if ready else 2


if __name__ == '__main__':
    sys.exit(main())
