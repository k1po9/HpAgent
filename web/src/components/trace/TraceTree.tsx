import { useMemo, useState, type ReactNode } from "react";
import {
  ChevronDown,
  ChevronRight,
  Circle,
  CircleCheck,
  CircleX,
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

function durationLabel(durationMs: number | null): string {
  if (durationMs === null) return "";
  return durationMs < 1000 ? `${durationMs} ms` : `${(durationMs / 1000).toFixed(2)} s`;
}

function StatusIcon({ status }: { status: HpTraceStatus }) {
  if (status === "running")
    return <LoaderCircle className="hp-trace-status hp-trace-status--running" />;
  if (status === "completed")
    return <CircleCheck className="hp-trace-status hp-trace-status--completed" />;
  if (status === "failed") return <CircleX className="hp-trace-status hp-trace-status--failed" />;
  return <Circle className="hp-trace-status hp-trace-status--cancelled" />;
}

function TraceTreeNode({
  node,
  childrenByParent,
  selected,
  onSelect,
}: {
  node: TraceNode;
  childrenByParent: Map<string, TraceNode[]>;
  selected: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const children = childrenByParent.get(node.id) ?? [];
  return (
    <li className="hp-trace-tree__item">
      <div className={`hp-trace-tree__row${selected === node.id ? " is-selected" : ""}`}>
        <button
          type="button"
          className="hp-trace-tree__expand"
          aria-label={expanded ? "折叠节点" : "展开节点"}
          disabled={children.length === 0}
          onClick={() => setExpanded((value) => !value)}
        >
          {children.length === 0 ? null : expanded ? <ChevronDown /> : <ChevronRight />}
        </button>
        <button type="button" className="hp-trace-tree__node" onClick={() => onSelect(node.id)}>
          <StatusIcon status={node.status} />
          <span className="hp-trace-tree__name">{node.name}</span>
          <span className="hp-trace-tree__duration">{durationLabel(node.durationMs)}</span>
        </button>
      </div>
      {expanded && children.length ? (
        <ul>
          {children.map((child) => (
            <ConnectedNode
              key={child.id}
              node={child}
              childrenByParent={childrenByParent}
              selected={selected}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function ConnectedNode({
  node,
  childrenByParent,
  selected,
  onSelect,
}: {
  node: TraceNode;
  childrenByParent: Map<string, TraceNode[]>;
  selected: string | null;
  onSelect: (nodeId: string) => void;
}) {
  return (
    <TraceTreeNode
      node={node}
      childrenByParent={childrenByParent}
      selected={selected}
      onSelect={onSelect}
    />
  );
}

export function TraceTree({ nodes, rootIds, selectedNodeId, onSelect }: TraceTreeProps) {
  const childrenByParent = useMemo(() => {
    const index = new Map<string, TraceNode[]>();
    Object.values(nodes).forEach((node) => {
      if (!node.parentId) return;
      index.set(node.parentId, [...(index.get(node.parentId) ?? []), node]);
    });
    return index;
  }, [nodes]);

  const renderNode = (node: TraceNode): ReactNode => {
    return (
      <TraceTreeNode
        key={node.id}
        node={node}
        childrenByParent={childrenByParent}
        selected={selectedNodeId}
        onSelect={onSelect}
      />
    );
  };

  const roots = rootIds
    .map((id) => nodes[id])
    .filter((node): node is TraceNode => node !== undefined);
  return <ul className="hp-trace-tree">{roots.map(renderNode)}</ul>;
}
