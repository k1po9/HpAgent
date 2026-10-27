#!/usr/bin/env python3
"""Check the exact prepared amd64 image without QQ/network/login side effects."""
import argparse
import json
import re
import subprocess

PINNED_IMAGE = 'motricseven7/snowluma@sha256:34b4ec03738a698231cbad39c5650747330f5a96d953494fb6c12e1273974c0b'


def verify(image):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image):
        raise ValueError('SnowLuma requires an immutable image digest')
    metadata = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    if metadata['Architecture'] != 'amd64' or metadata['Os'] != 'linux':
        raise ValueError('only the verified linux/amd64 image is supported')
    script = '''
const fs = require('fs');
const cp = require('child_process');
const release = JSON.parse(fs.readFileSync('/app/runtime/release.json', 'utf8'));
const pkg = JSON.parse(fs.readFileSync('/app/runtime/package.json', 'utf8'));
const qq = cp.execFileSync('dpkg-query', ['-W', '-f=${Version}', 'linuxqq'], {encoding:'utf8'});
if (release.version !== '2.2.0' || pkg.version !== '2.2.0' ||
    release.target !== 'linux-x64' || process.version !== 'v24.22.1' || qq !== '3.2.32-52194') process.exit(1);
console.log(JSON.stringify({snowluma: release.version, node: process.version, qq, target: release.target}));
'''
    output = subprocess.check_output([
        'docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
        '--cap-add', 'SYS_PTRACE', '--security-opt', 'seccomp=unconfined',
        '--entrypoint', 'node', image, '-e', script], text=True)
    return {'image': image, 'image_id': metadata['Id'], **json.loads(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=PINNED_IMAGE)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.image), indent=2))
    except (ValueError, subprocess.CalledProcessError):
        parser.exit(1, 'SnowLuma version verification failed; no login started.\n')


if __name__ == '__main__':
    main()
