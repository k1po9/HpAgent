import type { ModelInputState, TraceNode } from "./traceStore";

interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  usage_source: string;
}

function tokenUsage(value: unknown): TokenUsage | null {
  if (!value || typeof value !== "object") return null;
  const usage = value as Record<string, unknown>;
  if (
    typeof usage.input_tokens !== "number" ||
    typeof usage.output_tokens !== "number" ||
    typeof usage.total_tokens !== "number" ||
    typeof usage.usage_source !== "string"
  )
    return null;
  return usage as unknown as TokenUsage;
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
}

function ModelInputView({ state }: { state: ModelInputState }) {
  if (state.status === "loading") return <p>正在加载 Model Input…</p>;
  if (state.status === "unavailable") return <p>当前账号不可查看 Model Input。</p>;
  if (state.status === "error") return <p>Model Input 暂时无法加载。</p>;
  const detail = state.detail;
  if (!detail) return null;
  const input = detail.model_input;
  return (
    <div data-testid={`model-input-${detail.visibility}`}>
      <dl>
        <div>
          <dt>Snapshot</dt>
          <dd>{input.snapshot_id}</dd>
        </div>
        <div>
          <dt>Content hash</dt>
          <dd>{input.content_hash.slice(0, 12)}</dd>
        </div>
        <div>
          <dt>Phase</dt>
          <dd>{input.phase}</dd>
        </div>
        <div>
          <dt>Fallback</dt>
          <dd>Attempt {input.fallback_attempt}</dd>
        </div>
        <div>
          <dt>Provider / model</dt>
          <dd>
            {input.provider} / {input.model}
          </dd>
        </div>
        <div>
          <dt>Messages / tools</dt>
          <dd>
            {input.message_count} / {input.tool_count}
          </dd>
        </div>
      </dl>
      {detail.visibility === "full_safe" && input.provider_request_body ? (
        <pre data-testid="model-input-provider-body">
          {JSON.stringify(input.provider_request_body, null, 2)}
        </pre>
      ) : (
        <p>此账号仅可查看安全元数据摘要。</p>
      )}
    </div>
  );
}

export function TraceDetail({
  node,
  modelInputs,
  onOpenModelInput,
}: {
  node: TraceNode | null;
  modelInputs: Record<string, ModelInputState>;
  onOpenModelInput: (snapshotId: string) => Promise<void>;
}) {
  if (!node) {
    return <div className="hp-trace-detail hp-trace-empty">选择节点查看详情。</div>;
  }
  const usage = node.type === "llm" ? tokenUsage(node.metadata.token_usage) : null;
  const snapshotId =
    typeof node.metadata.snapshot_id === "string" ? node.metadata.snapshot_id : null;
  const contentHash =
    typeof node.metadata.content_hash === "string" ? node.metadata.content_hash : null;
  return (
    <section className="hp-trace-detail" aria-label="Trace 节点详情">
      <div className="hp-trace-detail__heading">
        <strong>{node.name}</strong>
        <code>{node.type}</code>
      </div>
      <dl>
        <div>
          <dt>状态</dt>
          <dd>{node.status}</dd>
        </div>
        <div>
          <dt>耗时</dt>
          <dd>{node.durationMs === null ? "—" : `${node.durationMs} ms`}</dd>
        </div>
        <div>
          <dt>开始</dt>
          <dd>{formatTime(node.startedAt)}</dd>
        </div>
        <div>
          <dt>结束</dt>
          <dd>{formatTime(node.endedAt)}</dd>
        </div>
      </dl>
      {node.type === "llm" ? (
        <div className="hp-trace-detail__metadata" data-testid="trace-llm-details">
          <span>LLM Call</span>
          <dl>
            {(["phase", "model", "provider", "endpoint_id"] as const).map((key) =>
              typeof node.metadata[key] === "string" ? (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{String(node.metadata[key])}</dd>
                </div>
              ) : null,
            )}
          </dl>
          {usage ? (
            <dl data-testid="trace-token-usage">
              <div>
                <dt>Input</dt>
                <dd>{usage.input_tokens.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Output</dt>
                <dd>{usage.output_tokens.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Total</dt>
                <dd>{usage.total_tokens.toLocaleString()}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>{usage.usage_source}</dd>
              </div>
            </dl>
          ) : null}
        </div>
      ) : null}
      {snapshotId ? (
        <div className="hp-trace-detail__model-input">
          <div>
            <strong>Model Input</strong>
            <code>
              {snapshotId.slice(0, 8)}
              {contentHash ? ` · ${contentHash.slice(0, 12)}` : ""}
            </code>
          </div>
          {!modelInputs[snapshotId] ? (
            <button type="button" onClick={() => void onOpenModelInput(snapshotId)}>
              查看 Model Input
            </button>
          ) : (
            <ModelInputView state={modelInputs[snapshotId]} />
          )}
        </div>
      ) : null}
      <div className="hp-trace-detail__metadata">
        <span>Metadata</span>
        <pre>{JSON.stringify(node.metadata, null, 2)}</pre>
      </div>
    </section>
  );
}
