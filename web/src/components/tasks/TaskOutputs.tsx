import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { HpWork, HpTaskRun, HpRunSnapshot } from "../../api/types";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { Surface } from "../shell/Surface";
import { useTaskQuery } from "./useTaskQuery";
import { acceptanceEligible } from "./taskActions";
import { useTaskOperations } from "./taskOperations";
import { TaskDeliveryDecision } from "./TaskDeliveryDecision";
type SaveFile = (file: { file_id: string; file_name: string }) => void;
export function TaskOutputs({ work, onSaveFile }: { work: HpWork; onSaveFile: SaveFile }) {
  const runs = useTaskQuery<{ items: HpTaskRun[] }>(
    `runs:${work.work_id}`,
    `/api/v1/works/${work.work_id}/runs`,
    work.row_version,
  );
  return (
    <div className="hp-task-outputs">
      <h4>任务交付引用</h4>
      <p>查看、保存或生成新版本均不等于接受任务成果。后续手工版本不会替代此处原交付。</p>
      {!work.artifacts.length && <p>暂无 HTML 交付引用。</p>}
      {work.artifacts.map((a, i) => (
        <ArtifactResult
          key={a.reference_id ?? `${a.artifact_version_id}:${a.role}:${i}`}
          work={work}
          artifact={a}
          runs={runs.data?.items}
        />
      ))}
      <h4>执行记录</h4>
      <p>仅查询最近最多 100 次执行；Run 成功不等于任务已结束。</p>
      {runs.error && (
        <p role="alert">
          执行记录暂不可用。<button onClick={runs.retry}>重试执行记录</button>
        </p>
      )}
      {runs.loading && <p role="status">正在加载执行记录…</p>}
      {runs.data?.items.length === 0 && <p>暂无执行记录。</p>}
      {runs.data?.items.map((run) => (
        <RunOutput key={run.run_id} work={work} run={run} onSaveFile={onSaveFile} />
      ))}
      <h4>投递记录</h4>
      {work.deliveries.length === 0 && <p>暂无投递记录。</p>}
      {work.deliveries.map((d) => (
        <TaskDeliveryDecision key={d.delivery_id} work={work} delivery={d} />
      ))}
      {work.continuation?.operation_ref && (
        <p role="status">
          外部操作 {work.continuation.operation_ref}{" "}
          结果未决，请进入执行详情核查。当前没有通用人工解除接口，停止或修订不能代替核查。
        </p>
      )}
      <h4>自动保存到空间</h4>
      {!work.workspace_saves.length && <p>暂无自动保存记录。手动保存结果在保存表单中显示。</p>}
      {work.workspace_saves.map((s) => (
        <p key={s.run_id}>
          r{s.requirement_revision} ·{" "}
          {s.state === "succeeded"
            ? "已保存"
            : s.state === "failed"
              ? `保存失败：${s.failure_code ?? "原因待同步"}`
              : "等待保存"}
          {s.state === "succeeded" && s.entry_id && (
            <button
              onClick={() =>
                useShell
                  .getState()
                  .openInspector(
                    { kind: "file", objectId: s.entry_id!, origin: { workId: work.work_id } },
                    true,
                  )
              }
            >
              查看空间入口
            </button>
          )}
        </p>
      ))}
      {[work.artifacts, work.deliveries, work.workspace_saves].some(
        (list) => list.length >= 100,
      ) && <p>仅展示最近明细，可能还有更早记录；任务列表扫描完成不代表明细历史完整。</p>}
    </div>
  );
}
function ArtifactResult({
  work,
  artifact: a,
  runs,
}: {
  work: HpWork;
  artifact: HpWork["artifacts"][number];
  runs?: HpTaskRun[];
}) {
  const [confirm, setConfirm] = useState(false);
  const intent = useTaskOperations((s) => s.intents[work.work_id]);
  const known = runs?.find((r) => r.run_id === a.producing_run_id);
  const evidence = useTaskQuery<HpRunSnapshot>(
    a.producing_run_id &&
      runs &&
      !known &&
      a.role === "deliverable" &&
      a.source_requirement_revision === work.current_requirement_revision &&
      a.accepted_for_revision !== work.current_requirement_revision &&
      work.requirement.acceptance_criteria.some((c) => c.evidence_types.includes("user_acceptance"))
      ? `run-evidence:${a.producing_run_id}`
      : null,
    `/api/v1/runs/${a.producing_run_id}`,
  );
  const run = known ?? (evidence.data?.source_kind === "work" ? evidence.data.run : undefined);
  const eligible = acceptanceEligible(work, a, run);
  return (
    <article className="hp-task-artifact">
      <p>
        {a.role === "deliverable" ? "任务交付" : a.role === "input" ? "输入引用" : "证据引用"} · r
        {a.source_requirement_revision} · {a.status === "completed" ? "生成完成" : "生成未完成"} ·{" "}
        {a.accepted_for_revision === work.current_requirement_revision
          ? "当前要求已接受"
          : a.accepted_for_revision
            ? "历史接受"
            : "未接受"}
      </p>
      <button
        onClick={() =>
          useShell.getState().openInspector(
            {
              kind: "artifact",
              objectId: a.artifact_id,
              versionId: a.artifact_version_id,
              origin: { workId: work.work_id },
            },
            true,
          )
        }
      >
        查看原引用版本
      </button>
      {eligible ? (
        <button disabled={intent?.busy || intent?.uncertain} onClick={() => setConfirm(true)}>
          接受这份成果
        </button>
      ) : (
        a.role === "deliverable" &&
        a.accepted_for_revision !== work.current_requirement_revision && (
          <p className="hp-muted">
            只有当前要求与控制版本匹配、成功执行证据明确且需要用户验收的交付可以接受。
          </p>
        )
      )}
      {evidence.error && (
        <p role="alert">
          验收证据未能核实。<button onClick={evidence.retry}>重新核实证据</button>
        </p>
      )}
      {confirm && (
        <Surface title="接受指定成果版本" onClose={() => setConfirm(false)}>
          <p>
            将接受任务 r{work.current_requirement_revision} 的原交付版本 {a.artifact_version_id}
            。此操作独立于查看或保存。
          </p>
          <button
            disabled={!eligible || intent?.busy}
            onClick={() =>
              void useWorks
                .getState()
                .control(work, "accept-result", a.artifact_version_id)
                .then((ok) => {
                  if (ok) setConfirm(false);
                })
            }
          >
            确认接受此版本
          </button>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
    </article>
  );
}
function RunOutput({
  work,
  run,
  onSaveFile,
}: {
  work: HpWork;
  run: HpTaskRun;
  onSaveFile: SaveFile;
}) {
  const [open, setOpen] = useState(false);
  return (
    <article className="hp-task-run">
      <p>
        r{run.requirement_revision} ·{" "}
        {(
          {
            succeeded: "执行成功",
            failed: "执行失败",
            running: "执行中",
            queued: "等待执行",
            cancelled: "已取消",
          } as Record<string, string>
        )[run.status] ?? "执行状态待核实"}
      </p>
      <button
        onClick={() =>
          useShell
            .getState()
            .openInspector(
              { kind: "run", objectId: run.run_id, origin: { workId: work.work_id } },
              true,
            )
        }
      >
        查看执行详情与诊断
      </button>
      <button onClick={() => setOpen((v) => !v)}>{open ? "收起输出" : "读取报告与输出文件"}</button>
      {run.failure_code && <p>失败原因：{run.failure_code}</p>}
      {open && <PublishedOutput work={work} run={run} onSaveFile={onSaveFile} />}
    </article>
  );
}
function PublishedOutput({
  work,
  run,
  onSaveFile,
}: {
  work: HpWork;
  run: HpTaskRun;
  onSaveFile: SaveFile;
}) {
  const research = work.requirement.capability_key === "research_report";
  const report = useTaskQuery<{ report: { report_markdown: string } }>(
    research ? `report:${work.work_id}:${run.run_id}` : null,
    `/api/v1/runs/${run.run_id}/research/report`,
    run.status,
  );
  const files = useTaskQuery<{ files: Array<{ file_id: string; name: string }> }>(
    `outputs:${work.work_id}:${run.run_id}`,
    `/api/v1/runs/${run.run_id}/published-files`,
    run.status,
  );
  return (
    <div>
      <div className="hp-task-actions">
        <button disabled={files.loading} onClick={files.retry}>
          刷新输出文件
        </button>
        {research && (
          <button disabled={report.loading} onClick={report.retry}>
            刷新报告
          </button>
        )}
      </div>
      {report.loading && <p role="status">正在读取报告…</p>}
      {report.error && (
        <p role="alert">
          报告暂不可用。<button onClick={report.retry}>重试报告</button>
        </p>
      )}
      {report.data && (
        <div className="hp-task-report">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {report.data.report.report_markdown}
          </ReactMarkdown>
        </div>
      )}
      {files.loading && <p role="status">正在读取输出文件…</p>}
      {files.error && (
        <p role="alert">
          输出文件暂不可用。<button onClick={files.retry}>重试输出文件</button>
        </p>
      )}
      {files.data?.files.map((f) => (
        <p key={f.file_id}>
          <a href={`/api/v1/files/${encodeURIComponent(f.file_id)}/content`} download>
            {f.name}
          </a>{" "}
          <button onClick={() => onSaveFile({ file_id: f.file_id, file_name: f.name })}>
            保存到空间
          </button>
        </p>
      ))}
      {files.data?.files.length === 0 && <p>暂无已发布文件。</p>}
    </div>
  );
}
