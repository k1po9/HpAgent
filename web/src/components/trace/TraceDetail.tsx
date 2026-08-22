import type { TraceNode } from "./traceStore";

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
}

export function TraceDetail({ node }: { node: TraceNode | null }) {
  if (!node) {
    return <div className="hp-trace-detail hp-trace-empty">选择节点查看详情。</div>;
  }
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
      <div className="hp-trace-detail__metadata">
        <span>Metadata</span>
        <pre>{JSON.stringify(node.metadata, null, 2)}</pre>
      </div>
    </section>
  );
}
