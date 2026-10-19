import { useEffect, useState } from "react";
import type { HpRunSnapshot } from "../../api/types";
import { isTerminalRunStatus } from "../../store/workbench";

function instant(value: string | null): number | null {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}
function elapsedLabel(start: number | null, end: number | null): string | null {
  if (start === null || end === null || end < start) return null;
  const seconds = Math.floor((end - start) / 1000);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return [
    days && `${days}天`,
    hours && `${hours}小时`,
    minutes && `${minutes}分`,
    `${seconds % 60}秒`,
  ]
    .filter(Boolean)
    .join(" ");
}

type TimelineRun = Pick<
  HpRunSnapshot["run"],
  "run_id" | "status" | "created_at" | "started_at" | "finished_at"
>;
export function RunTimeline({ run }: { run: TimelineRun }) {
  const terminal = isTerminalRunStatus(run.status);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (terminal) return;
    const tick = () => {
      if (!document.hidden) setNow(Date.now());
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [run.run_id, terminal]);
  const created = instant(run.created_at);
  const started = instant(run.started_at);
  const finished = instant(run.finished_at);
  const waiting = elapsedLabel(created, started ?? (run.status === "queued" ? now : null));
  const orderedStart =
    started !== null && (created === null || started >= created) ? started : null;
  const execution = elapsedLabel(orderedStart, finished ?? (!terminal ? now : null));
  const endLabel = run.status === "failed" ? "失败" : run.status === "cancelled" ? "取消" : "结束";
  const rows = [
    { label: "创建", value: run.created_at, fallback: "时间未提供", duration: null },
    {
      label: "开始",
      value: run.started_at,
      fallback: run.status === "queued" ? "尚未开始" : "时间未提供",
      duration: waiting ? `等待${started === null ? "中" : ""} ${waiting}` : null,
    },
    {
      label: endLabel,
      value: run.finished_at,
      fallback: terminal
        ? "时间未提供"
        : started !== null
          ? run.status === "cancelling"
            ? "正在停止"
            : "执行中"
          : run.status === "queued"
            ? "等待开始"
            : "时间未提供",
      duration: execution ? `${terminal ? "执行" : "已执行"} ${execution}` : null,
    },
  ];
  return (
    <section className="hp-run-timeline" aria-label="执行时间">
      <h3>时间过程</h3>
      <ol>
        {rows.map(({ label, value, fallback, duration }) => (
          <li key={label} data-recorded={instant(value) !== null}>
            <div className="hp-run-timeline-event">
              <span>{label}</span>
              {duration && <span className="hp-run-duration">{duration}</span>}
            </div>
            {instant(value) !== null ? (
              <time dateTime={value!} title={value!}>
                {new Date(value!).toLocaleString("zh-CN", { hour12: false })}
              </time>
            ) : (
              <span className="hp-run-time-missing">{fallback}</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
