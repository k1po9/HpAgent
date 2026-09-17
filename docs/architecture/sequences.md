# Important Sequences

## Web Agent Run

1. FastAPI authenticates the account and admits a message through `CommandService`.
2. PostgreSQL commits message, Run, session, idempotency, and Outbox state.
3. The dispatcher starts `AgentLifecycleWorkflow`, which starts `AgentRunWorkflow`.
4. ReAct or Plan-and-Execute invokes Context, Brain, and Actions through activities.
5. The result and trace commit to PostgreSQL; terminal publication wakes Web SSE clients.

## QQ Agent Run and Delivery

1. The QQ adapter normalizes provider identity, room/thread facts, mentions, and message identity.
2. The same command boundary commits conversation and Run state.
3. The canonical lifecycle and agent workflows execute the Run.
4. QQ delivery reads the committed result, sends formatted parts, and records delivery state independently.

## Durable Wait and Resume

1. An activity records an approval/wait requirement and the workflow waits for a signal.
2. The API commits the user's decision and signals the workflow.
3. Execution reacquires the account lease, receives a fresh fencing token when needed, and resumes from persisted transcript and operation state.

## Research

1. A task Run starts `ResearchReportWorkflow`.
2. Activities plan, search SearXNG, acquire content, extract evidence, and synthesize a report.
3. Each durable stage writes PostgreSQL state; clients query progress, evidence, and the final report.

## Heavy Document

1. File analysis requests normalization with a stable operation identifier.
2. `NormalizeDocumentWorkflow` schedules the activity on `hpagent-document`.
3. The dedicated worker reads the stored file, performs conversion/normalization in its Run directory, settles usage, and records the normalized result.
