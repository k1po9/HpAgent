import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class EmptyBankInitializeCliTests(unittest.TestCase):
    def run_cli(self, *args, **extra):
        environment = {k:v for k,v in os.environ.items()
                       if k not in {'WORKER_DATABASE_URL','HINDSIGHT_URL'}}
        environment.update(extra)
        return subprocess.run([sys.executable,str(ROOT/'scripts/operations/memory-bank-initialize.py'),*args],
                              env=environment,capture_output=True,text=True,cwd=ROOT)

    def test_help_has_no_service_dependency(self):
        self.assertEqual(self.run_cli('--help').returncode,0)

    def test_missing_targets_refuse(self):
        result = self.run_cli('--senders-stopped')
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['status'],'failed')

    def test_missing_stopped_sender_attestation_never_connects_or_echoes_dsn(self):
        result = self.run_cli(WORKER_DATABASE_URL='postgresql://do-not-print.invalid/db',
                              HINDSIGHT_URL='http://do-not-contact.invalid')
        self.assertEqual(result.returncode,2)
        self.assertNotIn('do-not-',result.stdout+result.stderr)
