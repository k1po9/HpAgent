"""Exercise invite CLI defaults and owner rejection without touching a database."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class InviteCliTests(unittest.TestCase):
    def test_ordinary_invite_defaults_to_no_prompt_access_and_rejects_owner(self):
        path = Path(__file__).resolve().parents[2] / "scripts/operations/create-registration-invite.py"
        spec = importlib.util.spec_from_file_location("beta_invite_cli", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = [str(path), "--profile", "standard", "--daily-token-limit", "50000",
                "--database-url", "fixture-no-database"]
        with patch("account.invite_service.RegistrationInviteService") as service:
            service.return_value.create.return_value = SimpleNamespace(invite_id="fixture", code="fixture")
            with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 0)
            profile = service.return_value.create.call_args.args[0]
            self.assertEqual((profile.model_access_tier, profile.daily_token_limit,
                              profile.prompt_visibility), ("standard", 50000, "none"))
            service.reset_mock()
            with patch.object(sys, "argv", [str(path), "--profile", "owner"]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    module.main()
                self.assertEqual(error.exception.code, 2)
            service.assert_not_called()
