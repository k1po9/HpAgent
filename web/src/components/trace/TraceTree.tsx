import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Check,
  X,
  Minus,
  CircleDashed,
  LoaderCircle,
} from "lucide-react";
import type { HpTraceStatus } from "../../api/types";
import type { TraceNode } from "./traceStore";
interface TraceTreeProps {
  nodes: Record<string, TraceNode>;
  rootIds: string[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}
const statusLabel = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  unknown: "状态未知",
};
const typeLabel: Record<string, string> = {
  llm: "模型",
  tool: "工具",
  agent: "执行",
  workflow: "流程",
  step: "步骤",
};
function durationLabel(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}
function StatusIcon({ status }: { status: HpTraceStatus }) {
  const Icon = {
    running: LoaderCircle,
    completed: Check,
    failed: X,
    cancelled: Minus,
    unknown: CircleDashed,
  }[status];
  return <Icon className={`hp-trace-status hp-trace-status--${status}`} aria-hidden="true" />;
}
export function TraceTree({ nodes, rootIds, selectedNodeId, onSelect }: TraceTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const childrenByParent = useMemo(() => {
    const index = new Map<string, TraceNode[]>();
    Object.values(nodes).forEach((node) => {
      if (node.parentId) index.set(node.parentId, [...(index.get(node.parentId) ?? []), node]);
    });
    return index;
  }, [nodes]);
  const renderNode = (
    node: TraceNode,
    depth: number,
    ancestors: Set<string>,
  ): import("react").ReactNode => {
    if (ancestors.has(node.id)) return null;
    const children = childrenByParent.get(node.id) ?? [];
    const expanded = !collapsed.has(node.id);
    return (
      <li
        key={node.id}
        className="hp-trace-tree__item"
        data-root={depth === 0}
        data-status={node.status}
      >
        <div
          className={`hp-trace-tree__row${selectedNodeId === node.id ? " is-selected" : ""}`}
          data-selected={selectedNodeId === node.id}
        >
          {children.length > 0 ? (
            <button
              type="button"
              className="hp-trace-tree__expand"
              aria-label={`${expanded ? "折叠" : "展开"}节点：${node.name}`}
              aria-expanded={expanded}
              onClick={() =>
                setCollapsed((previous) => {
                  const next = new Set(previous);
                  if (next.has(node.id)) next.delete(node.id);
                  else next.add(node.id);
                  return next;
                })
              }
            >
              {expanded ? <ChevronDown /> : <ChevronRight />}
            </button>
          ) : (
            <span className="hp-trace-leaf-space" aria-hidden="true" />
          )}
          <button
            type="button"
            className="hp-trace-tree__node"
            aria-label={`${node.name}，${statusLabel[node.status]}，${durationLabel(node.durationMs)}`}
            aria-pressed={selectedNodeId === node.id}
            title={`${node.name} · ${typeLabel[node.type] ?? node.type} · ${statusLabel[node.status]}`}
            onClick={() => onSelect(node.id)}
          >
            <StatusIcon status={node.status} />
            <span className="hp-trace-tree__name">{node.name}</span>
            {depth > 2 ? (
              <span className="hp-trace-type" title={`第 ${depth + 1} 层`}>
                层 {depth + 1}
              </span>
            ) : typeof node.metadata.snapshot_id === "string" ? (
              <span className="hp-trace-model-tag">模型输入</span>
            ) : null}
            <span className="hp-trace-tree__duration">{durationLabel(node.durationMs)}</span>
          </button>
        </div>
        {node.status === "failed" && typeof node.metadata.error_code === "string" && (
          <p className="hp-trace-error">执行异常 · {node.metadata.error_code}</p>
        )}
        {expanded && children.length > 0 ? (
          <ul
            style={{ marginLeft: depth < 2 ? (depth === 0 ? 26 : 16) : 0 }}
            data-compact={depth >= 2}
            className={
              depth === 0 &&
              children.some((child) => (childrenByParent.get(child.id)?.length ?? 0) > 0)
                ? "hp-trace-branches"
                : undefined
            }
          >
            {children.map((child) =>
              renderNode(child, depth + 1, new Set([...ancestors, node.id])),
            )}
          </ul>
        ) : null}
      </li>
    );
  };
  return (
    <ul className="hp-trace-tree">
      {rootIds.map((id) => (nodes[id] ? renderNode(nodes[id]!, 0, new Set()) : null))}
    </ul>
  );
}
