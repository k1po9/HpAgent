"""QQ readiness is reported separately from the Web Worker liveness probe."""
import asyncio
import json
import os
import time
from pathlib import Path


async def qq_diagnostics_loop(channels):
    path = Path(os.getenv('HPAGENT_QQ_READY_FILE', '/tmp/hpagent-qq-ready.json'))
    try:
        while True:
            state = {'pid': os.getpid(), 'updated_at': time.time(),
                     'channels': {ch.channel_type.value: ch.diagnostics() for ch in channels
                                  if hasattr(ch, 'diagnostics')}}
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state))
            temporary.replace(path)
            await asyncio.sleep(5)
    finally:
        path.unlink(missing_ok=True)
