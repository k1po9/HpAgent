# 04 — 关键时序

## Web / QQ canonical Run

```mermaid
sequenceDiagram
    participant S as Web or QQ surface
    participant C as Conversation Command
    participant P as PostgreSQL / Outbox
    participant D as Dispatcher
    participant L as AgentLifecycleWorkflow
    participant A as AgentRunWorkflow
    participant X as Durable Activities
    participant O as Web SSE or QQ delivery

    S->>C: message / cancel / retry
    C->>P: Message + Session + Run + Outbox (one transaction)
    P->>D: claim start_run
    D->>L: start deterministic workflow ID
    L->>X: prepare + load source input
    L->>A: execute child Agent workflow
    A->>X: context / model / tool / planning segments
    X->>P: operation results + transcript events
    A-->>L: stable result reference
    L->>X: finalize authoritative result
    X->>P: terminal Run + Message + delivery/outbox
    P-->>O: committed result projection
```

Web and QQ enter the same chain. QQ protocol identity and group context are frozen at ingress;
execution does not return to a channel-specific Host. QQ delivery retries committed payload
without creating a new Run or model call.

## Failure / recovery

```mermaid
sequenceDiagram
    participant P as PostgreSQL / Outbox
    participant T as Temporal
    participant X as Durable Activity

    alt Dispatcher loses Start response
        P->>T: retry deterministic Start
        T-->>P: existing canonical Run ID
    else Worker or Activity exits
        T->>X: replay / retry stable operation ID
        X->>P: validate fencing and deduplicate result/intent
    else User cancels
        P->>T: cancel canonical lifecycle
        T->>X: heartbeat cancellation
        X->>P: finalize cancelled
    else Unsafe side effect cannot be reconciled
        X->>P: mark operation uncertain
        T-->>P: fail Run through lifecycle finalizer
    end
```

Canonical phase3 History fixtures cover lifecycle, ReAct, Plan-and-Execute and tool approval.
History for deleted `WebRunWorkflow` retired with that Workflow type.
