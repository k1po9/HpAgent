import { TaskLookup } from "./TaskLookup";
import { useState } from "react";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { bucketLabels, taskType, presentTask, compareTasks } from "./taskPresentation";
import { TaskRow } from "./TaskRow";
export function TaskScreen() {
  const now = useWorks((s) => s.now);
  const route = useShell((s) => s.route);
  const items = useWorks((s) => s.items);
  const paused = useWorks((s) => s.scanPaused);
  const state = useWorks((s) => s.loadState),
    error = useWorks((s) => s.error),
    last = useWorks((s) => s.lastCompletedAt);
  const [context, setContext] = useState<{ id: string; order: string[]; filter: string } | null>(
    null,
  );
  const bucket = route.bucket ?? "attention",
    type = route.type ?? "all";
  const selectedId = route.inspector?.kind === "task" ? route.inspector.objectId : route.workId;
  const selected = items.find((w) => w.work_id === selectedId);
  const matching = items.filter(
    (w) => (type === "all" || taskType(w) === type) && presentTask(w, now).bucket === bucket,
  );
  const filter = `${bucket}:${type}`;
  const focused = context?.filter === filter ? context.id : null;
  const protectedIds = [selectedId, focused].filter(Boolean);
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
  return (
    <div
      className="hp-task-screen"
      onFocusCapture={(e) => {
        const row = (e.target as HTMLElement).closest("article");
        const id = row?.querySelector(".hp-task-title")?.id.replace(/^task-/, "");
        if (id && id !== focused) setContext({ id, order: rows.map((w) => w.work_id), filter });
      }}
      onBlurCapture={(e) => {
        if (!selectedId && !e.currentTarget.contains(e.relatedTarget as Node)) setContext(null);
      }}
    >
      <div className="hp-task-toolbar">
        <label>
          任务类型
          <select
            value={type}
            onChange={(e) => useShell.getState().navigate({ ...route, type: e.target.value }, true)}
          >
            <option value="all">全部</option>
            <option value="reminder">提醒</option>
            <option value="research">研究</option>
            <option value="general">通用任务</option>
          </select>
        </label>
        <button disabled={state === "scanning"} onClick={() => void useWorks.getState().load()}>
          刷新任务
        </button>
        <button className="hp-primary" onClick={() => useShell.setState({ modal: "task-create" })}>
          新建任务
        </button>
        <button onClick={() => useShell.setState({ modal: "task-inbox" })}>收件箱</button>
      </div>
      <h2>{bucketLabels[bucket]}</h2>
      <p role="status">
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
        <p role="alert">
          {error}
          <button
            onClick={() => void useWorks.getState().load(Boolean(useWorks.getState().nextBefore))}
          >
            继续同步
          </button>
        </p>
      )}
      {state === "scanning" && (
        <button onClick={() => useWorks.getState().cancelScan(true)}>暂停扫描</button>
      )}
      {paused && !error && (
        <button onClick={() => void useWorks.getState().load(true)}>继续扫描</button>
      )}
      {rows.map((work) => (
        <div key={work.work_id}>
          {protectedIds.includes(work.work_id) && !matching.includes(work) && (
            <p role="status">
              当前查看：此任务已归入「{bucketLabels[presentTask(work, now).bucket]}
              」，仍保留查看上下文。
            </p>
          )}
          <TaskRow work={work} selected={work === selected} />
        </div>
      ))}
      <TaskLookup />
      {!rows.length && (
        <p>
          {state === "initial" || state === "scanning"
            ? "正在同步任务…"
            : state === "complete"
              ? items.length
                ? "当前筛选没有任务。"
                : "暂无任务，可新建提醒、研究或通用任务。"
              : "已加载范围内没有匹配任务，请完成同步后查看。"}
        </p>
      )}
    </div>
  );
}
