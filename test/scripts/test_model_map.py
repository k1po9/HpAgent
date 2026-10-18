"""Offline mapping and credential redaction; fixtures never call providers."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("model_map", ROOT / "scripts/check/model-map.py")
model_map = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_map)


class ModelMapTests(unittest.TestCase):
    def fixture(self):
        env = {"MINIMAX_API_KEY": "fixture-secret-not-for-output",
               "MINIMAX_API_BASE_URL": "https://fixture.invalid/v1",
               "MINIMAX_FLAGSHIP_MODEL": "inactive-candidate", "MINIMAX_STANDARD_MODEL": "chat-model",
               "MINIMAX_HIGHSPEED_MODEL": "fast-model",
               "SILICONFLOW_API_KEY": "fixture-embedding-secret",
               "SILICONFLOW_BASE_URL": "https://fixture.invalid/v1",
               "SILICONFLOW_EMBEDDING_MODEL": "vector", "SILICONFLOW_RERANK_MODEL": "rank",
               "SILICONFLOW_FLAGSHIP_MODEL": "reasoner",
               "SILICONFLOW_STANDARD_MODEL": "chat-model", "SILICONFLOW_HIGHSPEED_MODEL": "fast-model"}
        hindsight = {}
        for prefix, model in [("HINDSIGHT_API_LLM", "memory-model"),
                              ("HINDSIGHT_API_EMBEDDINGS_OPENAI", "memory-vector"),
                              ("HINDSIGHT_API_RERANKER_SILICONFLOW", "memory-rank")]:
            hindsight.update({prefix + "_MODEL": model, prefix + "_API_KEY": "memory-secret",
                              prefix + "_BASE_URL": "https://fixture.invalid/v1"})
        hindsight["HINDSIGHT_API_LLM_PROVIDER"] = "openai"
        return {"services": {
            "hpagent": {"environment": env, "volumes": [{"type": "bind",
                "source": str(ROOT / "config"), "target": "/app/config"}]},
            "hindsight": {"environment": hindsight}}}

    def test_role_routing_and_separate_memory_without_keys_in_report(self):
        report = model_map.summarize(self.fixture())
        routes = {row["category"]: row for row in report["routes"]}
        self.assertEqual(routes["chat"]["model"], "chat-model")
        self.assertEqual(routes["fast"]["model"], "fast-model")
        self.assertEqual(routes["reasoning"]["model"], "reasoner")
        self.assertEqual(report["hindsight"][0]["model"], "memory-model")
        self.assertIn("MINIMAX_FLAGSHIP_MODEL", report["catalogue_not_selected_by_worker_yaml"])
        self.assertFalse(report["issues"])
        serialized = json.dumps(report)
        for secret in model_map.secret_values(self.fixture()):
            self.assertNotIn(secret, serialized)

    def test_compose_alias_left_literal_is_reported_as_invalid(self):
        fixture = self.fixture()
        fixture["services"]["hindsight"]["environment"]["HINDSIGHT_API_LLM_API_KEY"] = "${MINIMAX_API_KEY}"
        self.assertTrue(model_map.summarize(fixture)["issues"])

    def test_host_environment_does_not_complete_missing_worker_variable(self):
        fixture = self.fixture()
        del fixture["services"]["hpagent"]["environment"]["SILICONFLOW_STANDARD_MODEL"]
        with patch.dict(os.environ, {"SILICONFLOW_STANDARD_MODEL": "unrelated-host-model"}):
            report = model_map.summarize(fixture)
            self.assertTrue(any(issue.startswith("chat") for issue in report["issues"]))
            self.assertEqual(os.environ["SILICONFLOW_STANDARD_MODEL"], "unrelated-host-model")

    def test_final_output_redacts_secret_misconfigured_as_model(self):
        fixture = self.fixture()
        env = fixture["services"]["hpagent"]["environment"]
        env["SILICONFLOW_STANDARD_MODEL"] = env["SILICONFLOW_API_KEY"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("MINIMAX_API_KEY='fixture-secret-not-for-output'\n")
            output = io.StringIO()
            result = SimpleNamespace(returncode=0, stdout=json.dumps(fixture), stderr="")
            with patch.object(model_map.subprocess, "run", return_value=result), contextlib.redirect_stdout(output):
                self.assertEqual(model_map.main(["--env-file", str(path)]), 0)
            self.assertNotIn(env["SILICONFLOW_API_KEY"], output.getvalue())
            self.assertIn("[REDACTED]", output.getvalue())

    def test_redaction_happens_before_json_escaping(self):
        secret = 'fixture-"quoted"-secret'
        serialized = json.dumps(model_map.redact_report({"model": secret}, [secret]))
        self.assertEqual(json.loads(serialized)["model"], "[REDACTED]")

    def test_compose_failure_does_not_echo_sensitive_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.touch()
            error = io.StringIO()
            result = SimpleNamespace(returncode=1, stdout="", stderr="private-credential")
            with patch.object(model_map.subprocess, "run", return_value=result), contextlib.redirect_stderr(error):
                self.assertEqual(model_map.main(["--env-file", str(path)]), 2)
            self.assertNotIn("private-credential", error.getvalue())


if __name__ == "__main__":
    unittest.main()
