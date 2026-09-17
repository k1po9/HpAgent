import { useEffect } from "react";
import { Activity, RefreshCw, X } from "lucide-react";
import { useTraceStore } from "./traceStore";
import { TraceTree } from "./TraceTree";
import { TraceDetail } from "./TraceDetail";

export function TracePanel() {
  const open = useTraceStore((state) => state.open);
  const runId = useTraceStore((state) => state.runId);
  const run = useTraceStore((state) => state.run);
  const nodes = useTraceStore((state) => state.nodes);
  const rootIds = useTraceStore((state) => state.rootIds);
  const selectedNodeId = useTraceStore((state) => state.selectedNodeId);
  const loading = useTraceStore((state) => state.loading);
  const error = useTraceStore((state) => state.error);
  const setOpen = useTraceStore((state) => state.setOpen);
  const loadTrace = useTraceStore((state) => state.loadTrace);
  const selectNode = useTraceStore((state) => state.selectNode);

  useEffect(() => {
    if (open && runId) void loadTrace();
  }, [open, runId, loadTrace]);

  if (!open) return null;
  const selectedNode = selectedNodeId ? (nodes[selectedNodeId] ?? null) : null;

  return (
    <aside className="hp-trace-panel" aria-label="Trace Debug Panel">
      <header className="hp-trace-header">
        <div>
          <span className="hp-trace-title">
            <Activity aria-hidden="true" /> Trace Debug
          </span>
          <small>{runId ? `Run ${runId.slice(0, 8)}` : "当前对话暂无 Run"}</small>
        </div>
        <div className="hp-trace-header__actions">
          <button
            type="button"
            aria-label="刷新 Trace"
            disabled={!runId || loading}
            onClick={() => void loadTrace()}
          >
            <RefreshCw aria-hidden="true" />
          </button>
          <button type="button" aria-label="关闭 Trace" onClick={() => setOpen(false)}>
            <X aria-hidden="true" />
          </button>
        </div>
      </header>
      <div className="hp-trace-summary">
        <span className={`hp-trace-badge hp-trace-badge--${run?.status ?? "idle"}`}>
          {run?.status ?? (rootIds.length ? "live" : "idle")}
        </span>
        <span>{Object.keys(nodes).length} nodes</span>
      </div>
      <div className="hp-trace-tree-wrap">
        {loading && rootIds.length === 0 ? (
          <div className="hp-trace-empty">正在加载 Trace…</div>
        ) : null}
        {!loading && rootIds.length === 0 ? (
          <div className="hp-trace-empty">{error ?? "等待 Trace 事件…"}</div>
        ) : (
          <TraceTree
            nodes={nodes}
            rootIds={rootIds}
            selectedNodeId={selectedNodeId}
            onSelect={selectNode}
          />
        )}
        {error && rootIds.length > 0 ? <div className="hp-trace-warning">{error}</div> : null}
      </div>
      <TraceDetail node={selectedNode} />
    </aside>
  );
}
