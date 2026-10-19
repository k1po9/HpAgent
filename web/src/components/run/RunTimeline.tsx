import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Check } from "lucide-react";
import type { HpRunSnapshot } from "../../api/types";
import { isTerminalRunStatus } from "../../store/workbench";

function instant(value: string | null): number | null {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}
function elapsedLabel(start: number | null, end: number | null): string | null {
  if (start === null || end === null || end < start) return null;
  const seconds = Math.floor((end - start) / 1000);
  return [
    Math.floor(seconds / 86400) && `${Math.floor(seconds / 86400)}天`,
    Math.floor((seconds % 86400) / 3600) && `${Math.floor((seconds % 86400) / 3600)}小时`,
    Math.floor((seconds % 3600) / 60) && `${Math.floor((seconds % 3600) / 60)}分`,
    `${seconds % 60}秒`,
  ]
    .filter(Boolean)
    .join(" ");
}
type TimelineRun = Pick<
  HpRunSnapshot["run"],
  "run_id" | "status" | "created_at" | "started_at" | "finished_at"
>;
type Point = { at: number; labels: string[]; end: boolean; current?: boolean };
const date = (at: number) =>
  new Date(at).toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" });
const clock = (at: number) => new Date(at).toLocaleTimeString("zh-CN", { hour12: false });

export function RunTimeline({ run }: { run: TimelineRun }) {
  const terminal = isTerminalRunStatus(run.status);
  const [now, setNow] = useState(Date.now);
  const chart = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(300);
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
  const created = instant(run.created_at),
    started = instant(run.started_at),
    finished = instant(run.finished_at);
  const orderedStart =
    started !== null && (created === null || started >= created) ? started : null;
  const waiting = elapsedLabel(created, started ?? (run.status === "queued" ? now : null));
  const execution = elapsedLabel(orderedStart, finished ?? (!terminal ? now : null));
  const endLabel = run.status === "failed" ? "失败" : run.status === "cancelled" ? "取消" : "结束";
  const end = terminal ? finished : now;
  // A proportional track requires a complete, ordered span. Unknown time is never zero.
  const proportional =
    created !== null &&
    end !== null &&
    end >= created &&
    (run.status === "queued"
      ? started === null
      : started !== null && started >= created && started <= end);
  useEffect(() => {
    const element = chart.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [proportional]);
  const points: Point[] = [];
  if (proportional) {
    points.push({ at: created!, labels: ["创建"], end: false });
    if (started !== null) points.push({ at: started, labels: ["开始"], end: false });
    points.push({
      at: end!,
      labels: [
        terminal
          ? endLabel
          : run.status === "queued"
            ? "等待中"
            : run.status === "cancelling"
              ? "正在停止"
              : "执行中",
      ],
      end: terminal,
      current: !terminal,
    });
  }
  const grouped: Point[] = [];
  for (const point of points) {
    const previous = grouped.at(-1);
    if (previous?.at === point.at) {
      previous.labels.push(...point.labels);
      previous.end ||= point.end;
      previous.current ||= point.current;
    } else grouped.push({ ...point });
  }
  const span = proportional ? end! - created! : 0;
  const position = (at: number) => (span > 0 ? ((at - created!) / span) * 100 : 0);
  // Only annotations move to another lane on collision; time points retain their exact positions.
  const labelWidth = Math.min(width, 100);
  const lanes: number[] = [];
  const annotations = grouped.map((point) => {
    const percent = position(point.at);
    const left = Math.max(
      0,
      Math.min(width - labelWidth, (width * percent) / 100 - labelWidth / 2),
    );
    let lane = lanes.findIndex((right) => left >= right + 10);
    if (lane < 0) lane = lanes.length;
    lanes[lane] = left + labelWidth;
    return { point, percent, lane };
  });
  const crossDay = proportional && date(created!) !== date(end!);
  const startPercent = started !== null && proportional ? position(started) : 100;
  return (
    <section className="hp-run-timeline" aria-label="执行时间">
      <div className="hp-run-timeline-summary">
        <h3>{created === null ? "执行时间" : date(created)}</h3>
        <div className="hp-run-durations">
          {waiting && (
            <span className="hp-run-duration" data-phase="waiting">
              等待{started === null ? "中" : ""} {waiting}
            </span>
          )}
          {execution && (
            <span className="hp-run-duration" data-phase="execution">
              {terminal ? "执行" : "已执行"} {execution}
            </span>
          )}
        </div>
      </div>
      {run.status === "queued" && started === null && <p className="hp-run-time-note">尚未开始</p>}
      {proportional ? (
        <div
          ref={chart}
          className="hp-run-time-chart"
          data-status={run.status}
          style={{ height: `${22 + lanes.length * (crossDay ? 62 : 46)}px` }}
        >
          <div className="hp-run-time-track" aria-hidden="true">
            {span > 0 && (
              <>
                <span
                  className="hp-run-time-segment"
                  data-phase="waiting"
                  style={{ left: 0, width: `${startPercent}%` }}
                />
                {started !== null && (
                  <span
                    className="hp-run-time-segment"
                    data-phase="execution"
                    style={{ left: `${startPercent}%`, width: `${100 - startPercent}%` }}
                  />
                )}
              </>
            )}
            {annotations.map(({ point, percent }) => (
              <span
                key={point.at}
                className="hp-run-time-point"
                data-end={point.end}
                data-start={point.labels.includes("开始") || undefined}
                data-current={point.current || undefined}
                style={{ left: `${percent}%` }}
              >
                {point.end && run.status === "succeeded" ? (
                  <Check size={9} strokeWidth={2.5} />
                ) : null}
              </span>
            ))}
          </div>
          <ol>
            {annotations.map(({ point, percent, lane }) => (
              <li
                key={point.at}
                data-position={percent}
                data-current={point.current || undefined}
                style={
                  {
                    "--time-position": `${percent}%`,
                    "--time-label-width": `${labelWidth}px`,
                    top: `${28 + lane * (crossDay ? 62 : 46)}px`,
                  } as CSSProperties
                }
              >
                <span>{point.labels.join(" / ")}</span>
                <time
                  dateTime={new Date(point.at).toISOString()}
                  title={new Date(point.at).toLocaleString("zh-CN", { hour12: false })}
                >
                  {crossDay && <span>{new Date(point.at).toLocaleDateString("zh-CN")}</span>}
                  <span>
                    {point.current ? "截至 " : ""}
                    {clock(point.at)}
                  </span>
                </time>
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <div>
          <dl className="hp-run-time-unavailable">
            {[
              ["创建", run.created_at],
              ["开始", run.started_at],
              [endLabel, run.finished_at],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>
                  {instant(value!) !== null ? (
                    <time dateTime={value!}>
                      {new Date(value!).toLocaleString("zh-CN", { hour12: false })}
                    </time>
                  ) : label === "开始" && run.status === "queued" ? (
                    "尚未开始"
                  ) : (
                    "时间未提供"
                  )}
                </dd>
              </div>
            ))}
          </dl>
          <p className="hp-run-time-note">时间记录不完整或顺序异常，暂不绘制比例。</p>
        </div>
      )}
    </section>
  );
}
