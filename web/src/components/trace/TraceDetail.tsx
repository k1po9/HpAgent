import type { TraceNode } from "./traceStore";

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

export function TraceDetail({ node }: { node: TraceNode | null }) {
  if (!node) {
    return <div className="hp-trace-detail hp-trace-empty">选择节点查看详情。</div>;
  }
  const usage = node.type === "llm" ? tokenUsage(node.metadata.token_usage) : null;
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
      <div className="hp-trace-detail__metadata">
        <span>Metadata</span>
        <pre>{JSON.stringify(node.metadata, null, 2)}</pre>
      </div>
    </section>
  );
}
