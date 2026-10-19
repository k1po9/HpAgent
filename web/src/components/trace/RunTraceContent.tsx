import { useEffect } from "react";
import { Activity, RefreshCw, CircleCheck } from "lucide-react";
import { useTraceStore } from "./traceStore";
import { TraceTree } from "./TraceTree";
import { TraceDetail } from "./TraceDetail";

const statusLabel = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  unknown: "状态未知",
};
export function RunTraceContent() {
  const runId = useTraceStore((state) => state.runId);
  const run = useTraceStore((state) => state.run);
  const nodes = useTraceStore((state) => state.nodes);
  const rootIds = useTraceStore((state) => state.rootIds);
  const selectedNodeId = useTraceStore((state) => state.selectedNodeId);
  const loading = useTraceStore((state) => state.loading);
  const error = useTraceStore((state) => state.error);
  const modelInputs = useTraceStore((state) => state.modelInputs);
  const loadTrace = useTraceStore((state) => state.loadTrace);
  const selectNode = useTraceStore((state) => state.selectNode);
  const loadModelInput = useTraceStore((state) => state.loadModelInput);
  useEffect(() => {
    if (runId) void loadTrace();
  }, [runId, loadTrace]);
  const selectedNode = selectedNodeId ? (nodes[selectedNodeId] ?? null) : null;
  const start = run?.started_at ? Date.parse(run.started_at) : NaN;
  const end = run?.ended_at ? Date.parse(run.ended_at) : NaN;
  const duration =
    Number.isFinite(start) && Number.isFinite(end) && end >= start
      ? `${((end - start) / 1000).toFixed(2)} s`
      : null;
  return (
    <section className="hp-run-trace" aria-label="执行诊断记录">
      <div className="hp-quiet-rail">
        <header className="hp-trace-header">
          <h3 className="hp-trace-title">
            <Activity aria-hidden="true" />
            执行轨迹
          </h3>
          <div className="hp-trace-summary">
            {run ? (
              <>
                <span className={`hp-trace-state hp-trace-state--${run.status}`}>
                  {run.status === "completed" && <CircleCheck size={13} aria-hidden="true" />}
                  {statusLabel[run.status]}
                </span>
                {rootIds.length > 0 && <span>{Object.keys(nodes).length} 个节点</span>}
                {duration && (
                  <span className="hp-trace-total" title="按执行开始与结束时间计算">
                    总跨度 {duration}
                  </span>
                )}
              </>
            ) : (
              <span>{loading ? "正在读取执行记录…" : "诊断记录"}</span>
            )}
          </div>
          <div className="hp-trace-header__actions">
            <button
              type="button"
              aria-label="刷新执行轨迹"
              title="仅刷新执行轨迹"
              aria-busy={loading}
              disabled={!runId || loading}
              onClick={() => void loadTrace()}
            >
              <RefreshCw aria-hidden="true" />
            </button>
          </div>
        </header>
        <div className="hp-trace-tree-wrap" aria-busy={loading}>
          {rootIds.length === 0 ? (
            <div className="hp-trace-empty" role="status">
              {loading ? "正在加载执行轨迹…" : (error ?? "暂无可用诊断记录。")}
            </div>
          ) : (
            <TraceTree
              key={runId}
              nodes={nodes}
              rootIds={rootIds}
              selectedNodeId={selectedNodeId}
              onSelect={selectNode}
            />
          )}
          {error && rootIds.length > 0 ? (
            <div className="hp-trace-warning" role="status">
              {error}
            </div>
          ) : null}
        </div>
      </div>
      <TraceDetail
        node={selectedNode}
        modelInputs={modelInputs}
        onOpenModelInput={loadModelInput}
      />
    </section>
  );
}
