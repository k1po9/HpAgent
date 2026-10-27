#!/usr/bin/env python3
"""Initialize/recheck SnowLuma's effective OneBot snapshot; never print secrets.

Run with the selected bot stopped. Existing mismatched config is refused rather
than overwritten; later WebUI edits must be reviewed before enabling the Worker.
"""
import argparse
import json
import os
import re
import secrets
import tempfile
from pathlib import Path


def atomic_json(path, value, uid, gid):
    fd, tmp = tempfile.mkstemp(prefix='.onebot-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), uid, gid)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def desired(token):
    return {
        'mode': 'snapshot',
        'networks': {'httpServers': [], 'httpClients': [], 'wsServers': [],
                     'wsClients': [{'name': 'hpagent', 'enabled': True,
                                    'url': 'ws://hpagent:8082/onebot/v11', 'role': 'Universal',
                                    'accessToken': token, 'messageFormat': 'array',
                                    'reportSelfMessage': False, 'reconnectIntervalMs': 5000}]},
        'statusCommand': {'enabled': False, 'swallow': False, 'cooldownSeconds': 5, 'trigger': '#sl'},
        'historySync': {'enabled': False}, 'notifications': {'channelIds': []}}


def initialize(root, bot_id, uid, gid, check=False):
    if not re.fullmatch(r'[1-9][0-9]{4,19}', bot_id):
        raise ValueError('invalid bot QQ ID')
    # Refuse symlinks before resolve/mkdir/chown, including ancestors.
    root = root.absolute()
    paths = [root, root / 'onebot-secrets', root / 'snowluma', root / 'snowluma/data',
             root / 'snowluma/data/config', root / 'snowluma/qq-config', root / 'snowluma/qq-share']
    config = root / 'snowluma/data/config'
    token_file = root / 'onebot-secrets/onebot_access_token'
    outputs = [config / 'onebot.json', config / f'onebot_{bot_id}.json']
    for path in [*paths, token_file, *outputs]:
        if any(part.is_symlink() for part in [path, *path.parents]):
            raise ValueError('symlink in SnowLuma configuration path')
    if check and (not token_file.is_file() or any(not p.is_file() for p in outputs)):
        raise ValueError('SnowLuma initialization missing')
    if not check:
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        # Do not change ownership or permissions of the shared .data root.
        for path in paths[1:]:
            os.chown(path, uid, gid)
            os.chmod(path, 0o700)
    if token_file.exists():
        token = token_file.read_text().strip()
        if not token:
            raise ValueError('empty OneBot secret')
    else:
        token = secrets.token_urlsafe(48)
    value = desired(token)
    # Validate all existing files before writing anything. Ignore normalization
    # additions, but require the complete selected network topology and guards.
    if config.exists():
        unexpected = [p for p in config.glob('onebot_*.json') if p not in outputs]
        if unexpected:
            raise ValueError('unexpected per-account OneBot config; single bot required')
    for path in outputs:
        if path.exists():
            actual = json.loads(path.read_text())
            normalized = json.loads(json.dumps(actual))
            for client in normalized.get('networks', {}).get('wsClients', []):
                if isinstance(client, dict):
                    client.setdefault('enabled', True)
            for key in ['mode', 'networks']:
                if normalized.get(key) != value[key]:
                    raise ValueError('OneBot effective config differs; review with bot stopped')
            if actual.get('statusCommand', {}).get('enabled') is not False or actual.get('historySync', {}).get('enabled') is not False:
                raise ValueError('OneBot autonomous response/history must be disabled')
            if actual.get('notifications', {}).get('channelIds') != []:
                raise ValueError('OneBot unsolicited notifications must be disabled')
    if check:
        for path in [token_file, *outputs]:
            info = path.stat()
            if info.st_mode & 0o077 or info.st_uid != uid or info.st_gid != gid:
                raise ValueError('OneBot config/secret permissions differ')
        return
    if not token_file.exists():
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(token + '\n')
            os.fchown(stream.fileno(), uid, gid)
    for path in outputs:
        if not path.exists():
            atomic_json(path, value, uid, gid)
    for path in [token_file, *outputs]:
        os.chmod(path, 0o600)
        os.chown(path, uid, gid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=Path('.data'))
    parser.add_argument('--bot-id', required=True)
    parser.add_argument('--uid', type=int, default=1000)
    parser.add_argument('--gid', type=int, default=1000)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        initialize(args.data_root, args.bot_id, args.uid, args.gid, args.check)
    except (ValueError, OSError):
        parser.exit(1, 'SnowLuma configuration rejected; check paths, ownership and effective snapshot locally.\n')
    print('SnowLuma effective OneBot configuration verified; secret values omitted.')


if __name__ == '__main__':
    main()
