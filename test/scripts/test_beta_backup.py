"""Cold-backup safety checks using fake Docker, never the daemon or user data."""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/operations/backup.sh"

FAKE_DOCKER = r'''#!/usr/bin/env python3
import io,json,os,sys,tarfile
args=sys.argv[1:]
with open(os.environ['CALL_LOG'],'a') as log: log.write(json.dumps(args)+'\n')
if args[0]=='compose':
    if os.environ.get('MISSING_SERVICE') and args[-1]=='snowluma': sys.exit(0)
    print('fixture-container')
elif args[0]=='inspect':
    fmt=args[2]
    if fmt=='{{.State.Status}}': print(os.environ.get('FIXTURE_STATE','exited'))
    elif fmt=='{{json .Mounts}}':
        print(json.dumps([{'Type':'volume','Name':'fixture-'+str(i),'Destination':d} for i,d in enumerate([
          '/var/lib/postgresql/data','/data','/var/lib/hpagent/file-store',
          '/var/lib/hpagent/file-runs','/var/lib/hpagent/document-runs'])]))
    else: print('/fixture sha256:fixture')
elif args[0]=='run':
    assert '--network' in args and args[args.index('--network')+1]=='none'
    assert '--pull' in args and args[args.index('--pull')+1]=='never'
    assert '--read-only' in args and any('readonly' in a for a in args)
    with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
        item=tarfile.TarInfo('fixture'); item.size=2; archive.addfile(item,io.BytesIO(b'ok'))
else: sys.exit(99)
'''


class ColdBackupTests(unittest.TestCase):
    def run_fixture(self, *, with_pgroonga=False, **overrides):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "repo"
        target = root / "scripts/operations/backup.sh"
        target.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, target)
        (root / ".data").mkdir()
        (root / ".data/sentinel").write_text("unchanged")
        (root / "config").mkdir()
        for name in ("docker-compose.yaml", "docker-compose.beta.yaml"):
            (root / name).write_text("services: {}\n")
        if with_pgroonga:
            for suffix in ("", ".enabled", ".recovery"):
                (root / f"docker-compose.pgroonga{suffix}.yaml").write_text("services: {}\n")
        fake = Path(temp.name) / "bin"
        fake.mkdir()
        (fake / "docker").write_text(FAKE_DOCKER)
        (fake / "docker").chmod(0o700)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c",
                        "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        out = Path(temp.name) / "backups"
        log = Path(temp.name) / "calls"
        env = {**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"],
               "CALL_LOG": str(log), **overrides}
        result = subprocess.run(["bash", str(target), "--output", str(out)],
                                env=env, capture_output=True, text=True)
        self.assertEqual((root / ".data/sentinel").read_text(), "unchanged")
        return result, next(out.iterdir()), log.read_text()

    def test_running_services_rejected_before_any_archive(self):
        result, backup, calls = self.run_fixture(FIXTURE_STATE="running")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((backup / ".incomplete").exists())
        self.assertNotIn('"run"', calls)

    def test_missing_service_rejected(self):
        result, backup, calls = self.run_fixture(MISSING_SERVICE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((backup / ".incomplete").exists())
        self.assertNotIn('"run"', calls)

    def test_complete_set_has_verified_checksums_and_read_only_sources(self):
        result, backup, calls = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((backup / ".incomplete").exists())
        self.assertEqual(len(list(backup.glob("volume-*.tar.gz"))), 5)
        checked = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=backup,
                                 capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertNotIn('"stop"', calls)
        self.assertNotIn('"start"', calls)

    def test_pgroonga_routing_and_recovery_overlays_are_restorable(self):
        result, backup, _ = self.run_fixture(with_pgroonga=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(backup / 'configuration.tar.gz') as archive:
            for suffix in ('', '.enabled', '.recovery'):
                member = archive.extractfile(f'docker-compose.pgroonga{suffix}.yaml')
                self.assertIsNotNone(member)
                self.assertEqual(member.read(), b'services: {}\n')


if __name__ == "__main__":
    unittest.main()
