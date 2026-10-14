import { InspectorTabs } from "../shell/InspectorTabs";
import { useEffect, useState } from "react";
import { useShell, type Inspector } from "../../store/shell";
import { useWorkbench, isTerminalRunStatus, runStatusLabel } from "../../store/workbench";
import { runApi, useRunInspector } from "../../store/runInspector";
import { useTraceStore } from "../trace/traceStore";
import { RunTraceContent } from "../trace/RunTraceContent";
import { ModelInputView } from "../trace/TraceDetail";
import { RunResources } from "./RunResources";
import { RunBudget } from "./RunBudget";
import { VitalityHeading } from "../vitality/VitalitySignal";
import { useVitality } from "../vitality/useVitality";
import { RunStatus } from "../RunStatus";
import type { HpFile, HpModelInputList } from "../../api/types";

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
      <button
        className="hp-run-sync"
        onClick={() => {
          void state.load();
          if (tab === "advanced") {
            useTraceStore.getState().clearModelInputs();
            void useTraceStore.getState().loadTrace();
          }
        }}
      >
        同步执行
      </button>
      <div role="tabpanel" id={`run-panel-${tab}`} aria-labelledby={`run-tab-${tab}`}>
        {tab === "overview" && (
          <section className="hp-inspector-card" aria-label="执行概览">
            {!live && <VitalityHeading view={vitality} runId={id} size="detail" />}
            {live && snapshot.source_kind === "chat" && (
              <RunStatus
                activeRun={snapshot.run}
                generation={generation}
                size="detail"
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
            <dl className="hp-vitality-facts">
              <div>
                <dt>状态</dt>
                <dd>{vitality.runText}</dd>
              </div>
              <div>
                <dt>阶段</dt>
                <dd>{vitality.progressText ?? "阶段未提供"}</dd>
              </div>
              <div>
                <dt>审批</dt>
                <dd>
                  {vitality.approvals === "unknown"
                    ? "尚未核实"
                    : vitality.approvals === "pending"
                      ? "有待审批操作"
                      : "已核实，无待审批"}
                </dd>
              </div>
            </dl>
            {snapshot.source_kind === "chat" ? (
              <>
                {!live && snapshot.run.failure?.message && (
                  <p role="alert">{snapshot.run.failure.message}</p>
                )}
                {!live && (
                  <button
                    className="hp-run-origin-action"
                    onClick={() =>
                      useShell
                        .getState()
                        .navigate({ screen: "ai", conversationId: snapshot.run.conversation_id })
                    }
                  >
                    回到所属对话核对执行与操作
                  </button>
                )}
                {snapshot.assistant_message.content && (
                  <p className="hp-run-result">{snapshot.assistant_message.content}</p>
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
            <dl className="hp-run-timestamps" aria-label="执行时间">
              {(
                [
                  ["创建", run.created_at],
                  ["开始", run.started_at],
                  ["结束", run.finished_at],
                ] as const
              )
                .filter(([, v]) => v)
                .map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>
                      <time className="hp-run-time-readable" dateTime={v!} title={v!}>
                        {new Date(v!).toLocaleString("zh-CN", { hour12: false })}
                      </time>
                    </dd>
                  </div>
                ))}
            </dl>
            <button className="hp-run-approvals-action" onClick={() => changeTab("resources")}>
              查看操作审批
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
            <p>执行标识：{id}</p>
            <RunBudget budget={run.budget} />
            <RunTraceContent />
            <ModelInputs key={id} runId={id} />
          </>
        )}
      </div>
    </div>
  );
}
function ModelInputs({ runId }: { runId: string }) {
  const [list, setList] = useState<HpModelInputList | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const inputs = useTraceStore((s) => s.modelInputs);
  useEffect(() => {
    let valid = true;
    useTraceStore.getState().clearModelInputs();
    void runApi
      .listRunModelInputs(runId)
      .then((data) => {
        if (valid) {
          setList(data);
          setError(false);
          if (data.visibility === "none") useTraceStore.getState().clearModelInputs();
        }
      })
      .catch(() => {
        if (valid) {
          setList(null);
          setError(true);
          useTraceStore.getState().clearModelInputs();
        }
      });
    return () => {
      valid = false;
    };
  }, [runId, attempt]);
  return (
    <section aria-label="Model Input 列表">
      <h3>Model Input</h3>
      <button
        onClick={() => {
          setList(null);
          useTraceStore.getState().clearModelInputs();
          setAttempt((a) => a + 1);
        }}
      >
        同步模型记录
      </button>
      {error && <p role="alert">模型记录待同步，请重试。</p>}
      {list?.visibility === "none" && <p>当前账号不可查看 Model Input。</p>}
      {list && !list.items.length && <p>暂无模型记录。</p>}
      {list?.visibility !== "none" &&
        list?.items.map((input) => (
          <article key={input.snapshot_id}>
            <button onClick={() => void useTraceStore.getState().loadModelInput(input.snapshot_id)}>
              查看模型记录 {input.snapshot_id.slice(0, 8)}
            </button>
            {inputs[input.snapshot_id] && <ModelInputView state={inputs[input.snapshot_id]!} />}
          </article>
        ))}
    </section>
  );
}
