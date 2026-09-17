# Reliability

- **Transactional Outbox:** command state and dispatch intent commit together. Dispatch leases allow recovery after process interruption.
- **Temporal durability:** workflows preserve lifecycle and strategy progress across worker restarts. Nondeterministic work is isolated in activities.
- **Activity retry:** retryable failures use Temporal policy; permanent validation and contract errors are non-retryable.
- **Idempotency:** command keys, workflow identifiers, operation identifiers, provider keys, and persisted results prevent duplicate business effects.
- **Lease and fencing:** an account execution lease serializes conflicting Runs. Monotonic fencing tokens reject stale activity writes after lease reacquisition.
- **Durable wait/resume:** approval and waiting state is persisted. A resumed segment reacquires authority before continuing.
- **Side-effect safety:** the operation ledger records intent and completion. Activities reconcile uncertain external effects instead of blindly repeating them.
- **Cancellation:** cancellation flows through the lifecycle workflow, child workflows, activities, and PostgreSQL terminal state. Cleanup releases owned resources without allowing a stale worker to commit.
- **Delivery separation:** QQ delivery retries consume committed output; they never re-run agent execution. A delivery failure is therefore distinct from an execution failure.
- **Process ownership:** the worker stops ingress/background producers before Temporal workers, then closes shared infrastructure in reverse acquisition order.
