# 04 — 关键时序

## Web Chat

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as FastAPI
    participant D as PostgreSQL/Outbox
    participant T as Temporal
    participant H as WebExecutionHost
    participant F as AgentExecutionFacade
    participant C as Brain/Action/Sandbox
    participant S as SSE
    participant M as Hindsight

    B->>A: send message (idempotency key)
    A->>D: message + run + start_run outbox (one transaction)
    A-->>B: accepted run_id
    D->>T: dispatcher starts WebRunWorkflow
    T->>H: execute_agent_activity(run_id)
    H->>F: execute(request, control, sinks)
    F->>C: model/tool loop
    C-->>F: result
    F-->>H: ExecutionResult
    H->>D: complete run/message
    D-->>S: terminal/online event
    S-->>B: progress and terminal snapshot
    D->>M: retain_memory outbox worker
```

Outbox recovery reclaims expired leases. Reconciler compares active PostgreSQL runs with Temporal facts after worker/process interruption.

## QQ Chat

```mermaid
sequenceDiagram
    participant Q as QQ
    participant I as Channel/Ingress
    participant C as ConversationService
    participant T as QQ Workflow
    participant H as QQExecutionHost
    participant F as AgentExecutionFacade
    participant L as DefaultBrainActionLoop
    participant R as ReplyService
    participant M as Hindsight

    Q->>I: message
    I->>C: normalized UnifiedMessage
    C->>C: resolve PostgreSQL identity and prepare workspace
    C->>T: start/signal user_message
    T->>H: process_turn_activity
    H->>F: ExecutionRequest + QQ sinks
    F->>L: execute
    L->>M: rewrite + recall
    L->>L: model/tool iterations
    L-->>F: ExecutionResult
    H->>R: final/progress reply
    R-->>Q: QQ reply
    H->>M: retain per-execution document
```

`execution_id` 由 Workflow ID 与 message ID 确定生成；Activity retry 会复用同一 ID，用户事件与副作用审计按 execution 隔离。

## Web Failure / Cancel

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API
    participant D as PostgreSQL/Outbox
    participant T as Temporal
    participant H as WebExecutionHost

    alt User cancel
        B->>A: cancel run
        A->>D: cancel_requested + cancel_run outbox
        D->>T: cancel workflow
        T->>H: cancellation observed at checkpoint
        H->>D: finalize cancelled
    else Model/tool/run timeout or stable failure
        H-->>T: StableExecutionFailure(error_code)
        T->>D: finalize failed
    else Dispatcher/worker interruption
        D->>D: recover expired outbox lease
        D->>T: retry idempotent dispatch/activity
        D->>D: reconciler converges terminal state
    end
    D-->>B: SSE terminal snapshot; reconnect resumes by cursor
```

Temporal retry只重做可重试边界；数据库 terminal transition、Outbox claim 和 execution-scoped audit 提供幂等保护。Workspace 无法安全恢复时使用稳定 `workspace_recovery_required` 失败码收口。
