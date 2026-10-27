#!/usr/bin/env python3
"""Read the Worker's sanitized local QQ readiness file; no QQ action is sent."""
import argparse
import json
import os
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--path', type=Path, default=Path('/tmp/hpagent-qq-ready.json'))
args = parser.parse_args()
try:
    state = json.loads(args.path.read_text())
    os.kill(state['pid'], 0)
    fresh = time.time() - state['updated_at'] < 15
    channel = state['channels'].get('onebot11', {})
    # Whitelist diagnostics: never print arbitrary file fields or identities.
    print(json.dumps({key: channel.get(key) for key in (
        'listening', 'ready', 'connected', 'online', 'good', 'version', 'epoch',
        'queue_depth', 'last_event', 'last_ack', 'counters')}, ensure_ascii=False))
    raise SystemExit(0 if fresh and channel.get('ready') else 1)
except (ValueError, KeyError, OSError, TypeError):
    print('OneBot readiness unavailable')
    raise SystemExit(1)
