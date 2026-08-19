# ReAct vs Plan-and-Execute Benchmark Report

## Design

- Complete balanced design: **True**
- Repetitions: `[1]`
- Model: `MiniMax-M3`
- Provider: `minimax`
- Temperature: `provider-default`
- Max turns: `5`
- Manifest SHA-256: `e56a94d290a862e2ad98ba0588817d8047dd381122517590c88917a58b7e53e7`

## Results by Difficulty

| Level | Strategy | Recorded | Evaluable | Evaluable Success Rate | Avg Model Calls | Avg Tool Calls | Avg Turns | P50 Latency ms | P95 Latency ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| simple | react | 10 | 10 | 90.00% | 2.4 | 1.4 | 2.4 | 13212.722 | 20689.073 |
| simple | plan_and_execute | 10 | 10 | 50.00% | 5.5 | 1.5 | 5.5 | 23723.729 | 31677.965 |
| medium | react | 10 | 10 | 40.00% | 3.2 | 3.2 | 3.2 | 20251.633 | 25457.223 |
| medium | plan_and_execute | 10 | 10 | 70.00% | 6.2 | 3.1 | 6.2 | 23342.444 | 45240.885 |
| complex | react | 10 | 7 | 28.57% | 4.143 | 4.143 | 4.143 | 28279.314 | 38529.221 |
| complex | plan_and_execute | 10 | 7 | 28.57% | 9.714 | 6.143 | 9.714 | 48477.313 | 104568.563 |

## Failure Classification

- Environment-failure records excluded from evaluable metrics: **6**
- `react`: `{"environment_error": 3, "execution_error": 3, "incorrect_result": 9}`
- `plan_and_execute`: `{"environment_error": 3, "execution_error": 1, "incorrect_result": 10, "model_error": 2}`

## Interpretation Boundary

This report contains deterministic grader outcomes and operation counts. It does not infer
that either strategy is universally superior. Compare simple-task overhead, medium/complex
paired outcomes, and failure classes together. Token usage remains unavailable unless the
provider/runtime exposes reliable per-run usage.
