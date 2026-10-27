"""Config creation is isolated and must not overwrite existing login/config."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / 'scripts/operations/snowluma-config.py'
spec = importlib.util.spec_from_file_location('snowluma_config', MODULE)
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class SnowLumaConfigTests(unittest.TestCase):
    def test_repeat_check_permissions_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            config.initialize(root, '123456', os.getuid(), os.getgid())
            secret = root / 'onebot-secrets/onebot_access_token'
            snapshot = root / 'snowluma/data/config/onebot_123456.json'
            before = (secret.read_bytes(), snapshot.read_bytes(), snapshot.stat().st_mtime_ns)
            config.initialize(root, '123456', os.getuid(), os.getgid())
            config.initialize(root, '123456', os.getuid(), os.getgid(), check=True)
            self.assertEqual(before, (secret.read_bytes(), snapshot.read_bytes(), snapshot.stat().st_mtime_ns))
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            value = json.loads(snapshot.read_text())
            self.assertEqual(value['mode'], 'snapshot')
            self.assertEqual(value['networks']['wsServers'], [])
            self.assertFalse(value['historySync']['enabled'])

    def test_normalized_enabled_default_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config.initialize(root, '123456', os.getuid(), os.getgid())
            for snapshot in (root / 'snowluma/data/config').glob('onebot*.json'):
                value = json.loads(snapshot.read_text())
                del value['networks']['wsClients'][0]['enabled']
                snapshot.write_text(json.dumps(value))
            config.initialize(root, '123456', os.getuid(), os.getgid(), check=True)

    def test_mismatch_is_refused_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config.initialize(root, '123456', os.getuid(), os.getgid())
            snapshot = root / 'snowluma/data/config/onebot_123456.json'
            value = json.loads(snapshot.read_text())
            value['mode'] = 'overlay'
            snapshot.write_text(json.dumps(value))
            before = snapshot.read_bytes()
            with self.assertRaises(ValueError):
                config.initialize(root, '123456', os.getuid(), os.getgid())
            self.assertEqual(snapshot.read_bytes(), before)
            with self.assertRaises(ValueError):
                config.initialize(root, '654321', os.getuid(), os.getgid())

    def test_loose_secret_permissions_are_rejected_and_initialization_repairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config.initialize(root, '123456', os.getuid(), os.getgid())
            secret = root / 'onebot-secrets/onebot_access_token'
            secret.chmod(0o644)
            with self.assertRaises(ValueError):
                config.initialize(root, '123456', os.getuid(), os.getgid(), check=True)
            config.initialize(root, '123456', os.getuid(), os.getgid())
            config.initialize(root, '123456', os.getuid(), os.getgid(), check=True)

    def test_symlink_cannot_escape_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            outside = Path(tmp) / 'outside'
            root.mkdir()
            outside.mkdir()
            (root / 'snowluma').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                config.initialize(root, '123456', os.getuid(), os.getgid())
            self.assertEqual(list(outside.iterdir()), [])

    def test_missing_check_does_not_create_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'missing'
            with self.assertRaises(ValueError):
                config.initialize(root, '123456', os.getuid(), os.getgid(), check=True)
            self.assertFalse(root.exists())
