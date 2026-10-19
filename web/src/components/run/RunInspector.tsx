import { Check, ShieldCheck, ShieldQuestion, ShieldAlert, ArrowUpRight } from "lucide-react";
import { RunTimeline } from "./RunTimeline";
import { RunRefresh } from "./RunRefresh";
import { InspectorTabs } from "../shell/InspectorTabs";
import { useEffect } from "react";
import { useShell, type Inspector } from "../../store/shell";
import { useWorkbench, isTerminalRunStatus, runStatusLabel } from "../../store/workbench";
import { useRunInspector } from "../../store/runInspector";
import { useTraceStore } from "../trace/traceStore";
import { RunTraceContent } from "../trace/RunTraceContent";
import { RunResources } from "./RunResources";
import { RunBudget } from "./RunBudget";
import { RunDiagnosticIdentity } from "./RunDiagnosticIdentity";
import { VitalityHeading } from "../vitality/VitalitySignal";
import { useVitality } from "../vitality/useVitality";
import { RunStatus } from "../RunStatus";
import type { HpFile } from "../../api/types";

export function RunInspector({
  inspector,
  onSaveFile,
  hideIdentity = false,
}: {
  inspector: Inspector;
  hideIdentity?: boolean;
  onSaveFile?: (file: HpFile) => void;
}) {
  const state = useRunInspector();
  const active = useWorkbench((s) => s.activeRun);
  const messages = useWorkbench((s) => s.messages);
  const generation = useWorkbench((s) => s.pollGeneration);
  const progress = useWorkbench((s) => s.activeRunProgress);
  const degraded = useWorkbench((s) => s.degraded);
  const stopping = useWorkbench((s) => s.stopping);
  const id = inspector.objectId;
  const tab =
    inspector.tab === "resources" || inspector.tab === "advanced" ? inspector.tab : "overview";
  const live =
    active?.run_id === id &&
    messages.some((m) => m.role === "assistant" && m.produced_by_run_id === id);
  const snapshot =
    live && active
      ? {
          source_kind: "chat" as const,
          run: active,
          assistant_message: messages.find(
            (m) => m.role === "assistant" && m.produced_by_run_id === id,
          )!,
        }
      : state.runId === id
        ? state.snapshot
        : null;
  const vitality = useVitality(
    snapshot?.run ?? null,
    live ? progress : null,
    live ? generation : undefined,
    live && degraded,
  );
  const terminal = snapshot ? isTerminalRunStatus(snapshot.run.status) : false;
  useEffect(() => {
    useRunInspector.getState().select(id);
    return () => {
      useRunInspector.getState().select("");
      useTraceStore.getState().reset();
    };
  }, [id]);
  useEffect(() => {
    let valid = true,
      timer: ReturnType<typeof setTimeout>,
      inFlight = false,
      delay = 2000;
    const poll = async () => {
      if (!valid || inFlight || document.hidden) return;
      inFlight = true;
      await useRunInspector.getState().load();
      inFlight = false;
      if (!valid) return;
      delay = useRunInspector.getState().error ? Math.min(10000, delay * 2) : 2000;
      const run = useRunInspector.getState().snapshot?.run;
      if (
        !live &&
        (!run || !isTerminalRunStatus(run.status)) &&
        !useRunInspector.getState().unavailable
      )
        timer = setTimeout(() => void poll(), delay);
    };
    const visible = () => {
      clearTimeout(timer);
      if (!document.hidden) void poll();
    };
    void poll();
    document.addEventListener("visibilitychange", visible);
    return () => {
      valid = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [id, live]);
  useEffect(() => {
    if (tab !== "advanced" || !snapshot) return;
    useTraceStore.getState().selectRun(id);
    return () => useTraceStore.getState().reset();
    // Selection lifetime is the advanced tab, not a streamed snapshot update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, tab, Boolean(snapshot)]);
  useEffect(() => {
    if (tab !== "advanced" || live || !snapshot || terminal) return;
    let valid = true,
      inFlight = false,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!valid || inFlight || document.hidden) return;
      inFlight = true;
      if (!useTraceStore.getState().loading) await useTraceStore.getState().loadTrace();
      inFlight = false;
      if (valid && !document.hidden) timer = setTimeout(() => void poll(), 5000);
    };
    timer = setTimeout(() => void poll(), 5000);
    const visible = () => {
      clearTimeout(timer);
      if (!document.hidden) void poll();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      valid = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, tab, live, terminal, Boolean(snapshot)]);
  const changeTab = (next: Inspector["tab"]) => useShell.getState().setInspectorTab(next);
  if (state.runId === id && state.unavailable && !live)
    return (
      <div>
        <p role="alert">对象不可用。</p>
        <button onClick={() => useShell.getState().closeInspector()}>回到所属页面</button>
      </div>
    );
  if (!snapshot)
    return (
      <div>
        <p role="status">{state.error ?? "正在加载执行…"}</p>
        <button onClick={() => void state.load()}>重试</button>
      </div>
    );
  const run = snapshot.run;
  return (
    <div className="hp-run-inspector" data-status={run.status}>
      {!hideIdentity && (
        <header className="hp-run-identity">
          <span className="hp-object-kind">
            {snapshot.source_kind === "chat" ? "对话执行" : "任务执行"}
          </span>
        </header>
      )}
      <InspectorTabs
        prefix="run"
        label="执行详情页签"
        tabs={[
          { id: "overview", label: "概览" },
          { id: "resources", label: "使用资料与输出" },
          { id: "advanced", label: "高级诊断" },
        ]}
        selected={tab}
      />
      {state.error && <p role="status">{state.error}</p>}
      {!hideIdentity && <RunRefresh inspector={inspector} />}
      <div role="tabpanel" id={`run-panel-${tab}`} aria-labelledby={`run-tab-${tab}`}>
        {tab === "overview" && (
          <section className="hp-run-overview" aria-label="执行概览">
            <dl className="hp-run-overview-facts">
              <div className="hp-run-overview-state">
                <dt>执行状态</dt>
                <dd>
                  {run.status === "succeeded" ? (
                    <>
                      <span
                        className="hp-run-completion"
                        role="img"
                        aria-label="执行成功"
                        title="执行成功"
                      >
                        <Check size={20} strokeWidth={1.8} aria-hidden="true" />
                      </span>
                      <span>{vitality.runText}</span>
                    </>
                  ) : (
                    <VitalityHeading
                      view={vitality}
                      runId={id}
                      generation={live ? generation : undefined}
                      size="compact"
                    />
                  )}
                </dd>
              </div>
              <div className="hp-run-overview-approval" data-approval={vitality.approvals}>
                <dt>操作审批</dt>
                <dd>
                  {vitality.approvals === "unknown" ? (
                    <ShieldQuestion size={16} aria-hidden="true" />
                  ) : vitality.approvals === "pending" ? (
                    <ShieldAlert size={16} aria-hidden="true" />
                  ) : (
                    <ShieldCheck size={16} aria-hidden="true" />
                  )}
                  <span>
                    {vitality.approvals === "unknown"
                      ? "尚未核实"
                      : vitality.approvals === "pending"
                        ? "有待审批操作"
                        : "已核实，无待审批"}
                  </span>
                </dd>
              </div>
              {vitality.progressText && (
                <div className="hp-run-overview-activity">
                  <dt>当前活动</dt>
                  <dd>{vitality.progressText}</dd>
                </div>
              )}
            </dl>
            {live &&
              snapshot.source_kind === "chat" &&
              !["succeeded", "cancelled"].includes(run.status) && (
                <RunStatus
                  activeRun={snapshot.run}
                  generation={generation}
                  size="detail"
                  showHeading={false}
                  busyMessage={null}
                  progress={live ? progress : null}
                  degraded={live && degraded}
                  stopping={stopping}
                  onStop={() => {
                    if (useWorkbench.getState().activeRun?.run_id === id)
                      void useWorkbench.getState().stopRun();
                  }}
                  onRetry={() => {
                    if (useWorkbench.getState().activeRun?.run_id === id)
                      void useWorkbench.getState().retryRun();
                  }}
                />
              )}
            {snapshot.source_kind === "chat" ? (
              <>
                {!live && snapshot.run.failure?.message && (
                  <p role="alert">{snapshot.run.failure.message}</p>
                )}
                {snapshot.assistant_message.content && (
                  <div className="hp-run-overview-result" aria-label="回复正文">
                    <p className="hp-run-result" tabIndex={0}>
                      {snapshot.assistant_message.content}
                    </p>
                  </div>
                )}
              </>
            ) : (
              <>
                <p>
                  任务 {snapshot.run.work_id} · 要求版本 {snapshot.run.requirement_revision}
                </p>
                <p>本次执行状态独立于所属任务状态。</p>
                <button
                  onClick={() => {
                    useShell.getState().navigate({ screen: "tasks", workId: snapshot.run.work_id });
                  }}
                >
                  查看所属任务
                </button>
                {snapshot.run.failure_message && <p role="alert">{snapshot.run.failure_message}</p>}
                {snapshot.run.failure_code?.includes("uncertain") && (
                  <p>操作结果待确认，请核查所属任务及实际操作记录。</p>
                )}
                {snapshot.run.result_json && (
                  <pre>{JSON.stringify(snapshot.run.result_json, null, 2)}</pre>
                )}
                {snapshot.run.branches?.map((b) => (
                  <p key={b.execution_id}>
                    {b.branch_key} · {runStatusLabel(b.status)}
                    {b.error_code ? ` · ${b.error_code}` : ""}
                  </p>
                ))}
              </>
            )}
            <RunTimeline key={id} run={run} />
            <button className="hp-run-approvals-action" onClick={() => changeTab("resources")}>
              <ShieldQuestion size={20} aria-hidden="true" />
              <span>查看操作审批</span>
              <ArrowUpRight size={18} aria-hidden="true" />
            </button>
          </section>
        )}
        {tab === "resources" && (
          <RunResources
            key={id}
            runId={id}
            terminal={terminal}
            runStatus={run.status}
            onSaveFile={onSaveFile}
          />
        )}
        {tab === "advanced" && (
          <>
            <RunDiagnosticIdentity key={id} snapshot={snapshot} />
            <RunBudget budget={run.budget} />
            <RunTraceContent />
          </>
        )}
      </div>
    </div>
  );
}
