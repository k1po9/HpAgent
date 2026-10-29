import { TaskButton, TaskSelect, TaskLabel } from "../ui/TaskControls";
import { Alert, Badge, Empty, Skeleton } from "@lobehub/ui";
import { TaskLookup } from "./TaskLookup";
import { useEffect, useRef, useState } from "react";
import {
  Plus,
  RefreshCw,
  Inbox,
  LoaderCircle,
  Clock,
  CircleCheck,
  SlidersHorizontal,
} from "lucide-react";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import {
  bucketLabels,
  taskType,
  presentTask,
  compareTasks,
  type TaskBucket,
} from "./taskPresentation";
import { TaskRow } from "./TaskRow";
import { useTaskOperations } from "./taskOperations";

const bucketIcons = { attention: Inbox, active: LoaderCircle, waiting: Clock, ended: CircleCheck };
type BoardContext = {
  id: string;
  order: string[];
  filter: string;
  placements: Record<string, TaskBucket>;
};

export function TaskScreen() {
  const now = useWorks((s) => s.now);
  const route = useShell((s) => s.route);
  const items = useWorks((s) => s.items);
  const paused = useWorks((s) => s.scanPaused);
  const state = useWorks((s) => s.loadState),
    error = useWorks((s) => s.error),
    last = useWorks((s) => s.lastCompletedAt);
  const [context, setContext] = useState<BoardContext | null>(null);
  const board = useRef<HTMLDivElement>(null);
  const screen = useRef<HTMLDivElement>(null);
  const bucket = route.bucket ?? "attention",
    type = route.type ?? "all";
  const selectedId = route.inspector?.kind === "task" ? route.inspector.objectId : route.workId;
  const selected = items.find((w) => w.work_id === selectedId);
  const filter = `${bucket}:${type}`;
  const focused = context?.filter === filter ? context.id : null;
  const protectedIds = [selectedId, focused].filter(Boolean);
  const matching = items.filter((w) => type === "all" || taskType(w) === type);
  const candidates = items
    .filter((w) => matching.includes(w) || protectedIds.includes(w.work_id))
    .sort((a, b) => compareTasks(a, b, now));
  const frozen = context?.filter === filter && (focused || selectedId) ? context.order : [];
  const rows = [
    ...frozen
      .map((id) => candidates.find((w) => w.work_id === id))
      .filter((w): w is (typeof items)[number] => Boolean(w)),
    ...candidates.filter((w) => !frozen.includes(w.work_id)),
  ];
  const displayBucket = (id: string, actual: TaskBucket) =>
    context?.filter === filter && protectedIds.includes(id)
      ? (context.placements[id] ?? actual)
      : actual;
  useEffect(() => {
    if (bucket === "attention") {
      const viewport = board.current?.closest<HTMLElement>(".hp-screen-scroll");
      if (viewport) viewport.scrollTop = 0;
      return;
    }
    board.current
      ?.querySelector<HTMLElement>(`[data-task-lane="${bucket}"]`)
      ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [bucket]);
  useEffect(() => {
    const release = (event: FocusEvent) => {
      if (selectedId || !context || screen.current?.contains(event.target as Node)) return;
      if (event.target === document.body && useTaskOperations.getState().intents[context.id]?.busy)
        return;
      setContext(null);
    };
    document.addEventListener("focusin", release);
    return () => document.removeEventListener("focusin", release);
  }, [selectedId, context]);
  return (
    <div
      ref={screen}
      className="hp-task-screen hp-lobe-scope"
      onFocusCapture={(e) => {
        const row = (e.target as HTMLElement).closest<HTMLElement>("[data-work-id]");
        const id = row?.dataset.workId;
        if (id && id !== focused)
          setContext({
            id,
            order: rows.map((w) => w.work_id),
            filter,
            placements: Object.fromEntries(
              rows.map((w) => [w.work_id, displayBucket(w.work_id, presentTask(w, now).bucket)]),
            ),
          });
      }}
      onBlurCapture={(e) => {
        const id = (e.target as HTMLElement).closest<HTMLElement>("[data-work-id]")?.dataset.workId;
        if (id && useTaskOperations.getState().intents[id]?.busy) return;
        if (!selectedId && !e.currentTarget.contains(e.relatedTarget as Node)) setContext(null);
      }}
    >
      <div className="hp-task-toolbar">
        <TaskLabel>
          <SlidersHorizontal size={16} aria-hidden="true" />
          任务类型
          <TaskSelect
            value={type}
            onChange={(e) => useShell.getState().navigate({ ...route, type: e.target.value }, true)}
          >
            <option value="all">全部</option>
            <option value="reminder">提醒</option>
            <option value="research">研究</option>
            <option value="general">通用任务</option>
          </TaskSelect>
        </TaskLabel>
        <div className="hp-task-toolbar-actions">
          <TaskButton
            className="hp-task-refresh"
            disabled={state === "scanning"}
            loading={state === "scanning"}
            onClick={() => void useWorks.getState().load()}
          >
            <RefreshCw size={16} aria-hidden="true" />
            刷新任务
          </TaskButton>
          <TaskButton
            className="hp-task-inbox-button"
            onClick={() => useShell.setState({ modal: "task-inbox" })}
          >
            <Inbox size={16} aria-hidden="true" />
            收件箱
          </TaskButton>
          <TaskButton
            className="hp-primary"
            onClick={() => useShell.setState({ modal: "task-create" })}
          >
            <Plus size={16} aria-hidden="true" />
            新建任务
          </TaskButton>
        </div>
      </div>
      <p role="status" className="hp-task-sync">
        已加载 {items.length} 项 ·{" "}
        {type === "all"
          ? "全部类型"
          : type === "research"
            ? "研究"
            : type === "reminder"
              ? "提醒"
              : "通用任务"}{" "}
        ·{" "}
        {state === "complete"
          ? `本轮同步完成${last ? `（${new Date(last).toLocaleTimeString()}）` : ""}`
          : state === "scanning"
            ? "计数加载中"
            : "同步未完成"}
      </p>
      {error && (
        <Alert
          className="hp-task-sync-error"
          role="alert"
          type="error"
          title={error}
          action={
            <TaskButton
              onClick={() => void useWorks.getState().load(Boolean(useWorks.getState().nextBefore))}
            >
              继续同步
            </TaskButton>
          }
        />
      )}
      {state === "scanning" && (
        <TaskButton onClick={() => useWorks.getState().cancelScan(true)}>暂停扫描</TaskButton>
      )}
      {paused && !error && (
        <TaskButton onClick={() => void useWorks.getState().load(true)}>继续扫描</TaskButton>
      )}
      <div ref={board} className="hp-task-board" aria-label="任务状态分区">
        {(Object.keys(bucketLabels) as TaskBucket[]).map((lane) => {
          const Icon = bucketIcons[lane];
          const laneRows = rows.filter(
            (w) => displayBucket(w.work_id, presentTask(w, now).bucket) === lane,
          );
          const count = matching.filter((w) => presentTask(w, now).bucket === lane).length;
          return (
            <section
              key={lane}
              className={`hp-task-lane ${lane === bucket ? "hp-task-lane--current" : ""}`}
              data-task-lane={lane}
              aria-labelledby={`task-lane-${lane}`}
            >
              <header className="hp-task-lane-heading">
                <Icon size={17} aria-hidden="true" />
                <h2 id={`task-lane-${lane}`}>{bucketLabels[lane]}</h2>
                <Badge
                  className="hp-task-count"
                  aria-label={`${count} 项`}
                  count={String(count)}
                  size="small"
                />
              </header>
              {laneRows.map((work) => {
                const actual = presentTask(work, now).bucket;
                return (
                  <div key={work.work_id}>
                    {protectedIds.includes(work.work_id) &&
                      (actual !== lane || !matching.includes(work)) && (
                        <p className="hp-task-context-note" role="status">
                          当前查看：此任务已归入「{bucketLabels[actual]}」，仍保留查看上下文。
                        </p>
                      )}
                    <TaskRow work={work} selected={work === selected} />
                  </div>
                );
              })}
              {!laneRows.length &&
                (state === "initial" || state === "scanning" ? (
                  <div className="hp-task-lane-loading" role="status" aria-label="正在同步任务…">
                    <Skeleton width="100%" height={150} />
                  </div>
                ) : (
                  <Empty
                    className="hp-task-lane-empty"
                    titleProps={{ className: "hp-task-empty-title" }}
                    icon={Icon}
                    title={
                      state === "complete"
                        ? items.length
                          ? "当前筛选没有任务。"
                          : "暂无任务，可新建提醒、研究或通用任务。"
                        : "已加载范围内没有匹配任务，请完成同步后查看。"
                    }
                  />
                ))}
            </section>
          );
        })}
      </div>
      <TaskLookup />
    </div>
  );
}
