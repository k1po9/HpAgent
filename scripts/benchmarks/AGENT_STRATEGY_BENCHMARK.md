# Agent Strategy Benchmark Runbook

This package prepares experiment 3 without calling a model API during setup or preflight.

## Two-command path

```bash
make agent-benchmark-check
make agent-benchmark-run
```

The first command performs no model generation calls. It checks services, both database roles,
Durable Agent configuration, native workspace tools, whether the Worker was restarted after its
current config was written, three provider connectivity probes from inside the Worker container,
and a dedicated benchmark account/workspace. A failed item produces a non-zero exit code.

The second command repeats the environment gate, runs a two-trial latency pilot, and starts the
formal 60-run experiment only when both pilot strategies succeed within 180 seconds each. It then
generates the CSV, JSON summary, and Markdown report. Re-running the command resumes completed
formal task/strategy pairs rather than starting over.

## Safety and fairness prerequisites

1. Use a dedicated benchmark account, not a personal or production account.
2. Start the real Web API, Outbox dispatcher, Temporal, and durable Agent Worker with
   `DURABLE_AGENT_ENABLED=true`.
3. Configure one provider/model and the same temperature, tool set, max turns, and Worker
   configuration for both strategies. Do not change them during a run group. If the Worker omits
   `temperature`, record `--temperature provider-default` rather than inventing a number.
4. Confirm Worker logs show native workspace tools enabled. The supplied task set requires
   `fs_read`, `fs_write`, `fs_edit`, `Glob`, `Grep`, and `Bash`; an MCP-only tool registry makes
   the experiment invalid.
5. Point `HPAGENT_BENCHMARK_WORKSPACE` to the authenticated account's existing Git repo at
   `<WORKSPACE_ROOT>/<account_id>/repo`. The runner rejects a mismatched account directory.
6. Export secrets in the shell. Do not edit them into scripts or artifacts.

The runner creates new paths below `agent_strategy_benchmark/`; it does not clear or overwrite the
rest of the workspace. Every task/strategy/repetition gets an independent fixture copy.

## 1. Offline preflight (zero API calls)

```bash
PYTHONPATH=src .venv/bin/python \
  scripts/benchmarks/agent_strategy_benchmark.py preflight
```

Expected: 30 tasks, split into 10 simple, 10 medium, and 10 complex. The output explicitly says
`will_call_model_api: false`.

## 2. Optional low-cost pilot

After exporting the variables shown in `agent_strategy.env.example`, run one simple task for both
strategies into a separate pilot file. This is the first command that can call the model provider:

```bash
PYTHONPATH=src .venv/bin/python \
  scripts/benchmarks/agent_strategy_benchmark.py run \
  --execute-model-api \
  --task-ids simple_001 \
  --output artifacts/benchmarks/agent_strategy_pilot.jsonl \
  --repetitions 1 \
  --model YOUR_EXACT_MODEL_ID \
  --provider YOUR_PROVIDER \
  --temperature YOUR_TEMPERATURE \
  --max-turns YOUR_CONFIGURED_MAX_TURNS
```

Do not append pilot records to the formal default output and do not edit the canonical manifest.

## 3. Formal balanced experiment

The first complete version is 60 runs (30 tasks × 2 strategies × 1 repetition):

```bash
PYTHONPATH=src .venv/bin/python \
  scripts/benchmarks/agent_strategy_benchmark.py run \
  --execute-model-api \
  --repetitions 1 \
  --run-group formal-01 \
  --model YOUR_EXACT_MODEL_ID \
  --provider YOUR_PROVIDER \
  --temperature YOUR_TEMPERATURE \
  --max-turns YOUR_CONFIGURED_MAX_TURNS
```

If budget permits, use `--repetitions 3` from the beginning. Do not append a different repetition
count or different model configuration into the same result file. To start a genuinely different
configuration, provide a new `--output` filename.

The JSONL file is flushed and fsynced after every run. Each trial is printed as soon as it
finishes, including elapsed seconds. The default `--resume` behavior skips completed
task/strategy/repetition keys, so the same command can continue after interruption. A Ctrl-C
attempt remains in the raw file as an audit record, but it is retried on resume and the summarizer
uses the retry result.

If Ctrl-C lands between trials and leaves fixture files behind, the next run preserves those
managed files in the interrupted session branch before returning to the clean base branch. Any
dirty path outside `agent_strategy_benchmark/` remains a hard failure and is never modified.

The pilot retries only a strategy that fails with a transient `model_error`, up to three total
attempts. A strategy that already passed is not repeated. Environment and grading failures remain
immediate gate failures, and formal trial results are never automatically retried or hidden.

## 4. Generate deterministic summaries

Only summarize a complete balanced design for formal claims:

```bash
PYTHONPATH=src .venv/bin/python \
  scripts/benchmarks/summarize_agent_strategy_benchmark.py
```

For pilot inspection only, add `--allow-partial`; such a report must be labeled preliminary.

Formal outputs:

- `artifacts/benchmarks/agent_strategy_trials.jsonl` — append-only raw records and answers
- `artifacts/benchmarks/agent_strategy_trials.csv` — flattened trial table
- `artifacts/benchmarks/agent_strategy_summary.json` — validated grouped and paired statistics
- `artifacts/benchmarks/agent_strategy_report.md` — readable result table and boundaries

## Metrics and limitations

- Model/tool/planning counts come from real `agent_operations` rows for each Run. `model_calls`
  includes `model`, `synthesis`, and `planning` operations because both plan creation and plan
  evaluation invoke `BrainEngine` in the current implementation.
- Latency is measured from HTTP submission workflow start through terminal Run observation and
  includes polling granularity.
- Task success is determined by exact tags or local pytest, not another LLM.
- Token fields are `null` because the current durable result contract does not persist reliable
  provider token usage. The scripts do not invent these values.
- The CLI model/provider/temperature values are recorded for reproducibility. The operator must
  ensure they match the actual Worker configuration; the current API does not expose those secrets
  or effective provider settings.
