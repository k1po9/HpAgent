import { useEffect, useState } from "react";
import { useShell, type Inspector } from "../../store/shell";
import { useWorkbench, isTerminalRunStatus, runStatusLabel } from "../../store/workbench";
import { runApi, useRunInspector } from "../../store/runInspector";
import { useTraceStore } from "../trace/traceStore";
import { TracePanel } from "../trace/TracePanel";
import { ModelInputView } from "../trace/TraceDetail";
import { RunResources } from "./RunResources";
import { RunBudget } from "./RunBudget";
import { RunStatus } from "../RunStatus";
import type { HpFile, HpModelInputList } from "../../api/types";

export function RunInspector({
  inspector,
  onSaveFile,
}: {
  inspector: Inspector;
  onSaveFile?: (file: HpFile) => void;
}) {
  const state = useRunInspector();
  const active = useWorkbench((s) => s.activeRun);
  const messages = useWorkbench((s) => s.messages);
  const progress = useWorkbench((s) => s.activeRunProgress);
  const degraded = useWorkbench((s) => s.degraded);
  const stopping = useWorkbench((s) => s.stopping);
  const id = inspector.objectId;
  const tab = inspector.tab ?? "overview";
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
    if (tab !== "advanced" || live || !snapshot) return;
    let valid = true,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!valid) return;
      if (!document.hidden && !useTraceStore.getState().loading)
        await useTraceStore.getState().loadTrace();
      if (valid && !terminal) timer = setTimeout(() => void poll(), 5000);
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
  const changeTab = (next: Inspector["tab"]) =>
    useShell.setState((s) => ({ route: { ...s.route, inspector: { ...inspector, tab: next } } }));
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
    <div className="hp-run-inspector">
      <div
        role="tablist"
        aria-label="执行详情页签"
        onKeyDown={(e) => {
          const tabs = ["overview", "resources", "advanced"] as const;
          const focused =
            tabs.find(
              (value) => e.target instanceof HTMLElement && e.target.id === `run-tab-${value}`,
            ) ?? tab;
          const index = tabs.indexOf(focused);
          const next =
            e.key === "ArrowRight"
              ? tabs[(index + 1) % 3]
              : e.key === "ArrowLeft"
                ? tabs[(index + 2) % 3]
                : e.key === "Home"
                  ? tabs[0]
                  : e.key === "End"
                    ? tabs[2]
                    : null;
          if (next) {
            e.preventDefault();
            changeTab(next);
            document.getElementById(`run-tab-${next}`)?.focus();
          }
        }}
      >
        {(
          [
            ["overview", "概览"],
            ["resources", "使用资料与输出"],
            ["advanced", "高级诊断"],
          ] as const
        ).map(([value, label]) => (
          <button
            role="tab"
            id={`run-tab-${value}`}
            aria-controls={`run-panel-${value}`}
            tabIndex={tab === value ? 0 : -1}
            aria-selected={tab === value}
            key={value}
            onClick={() => changeTab(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {state.error && <p role="status">{state.error}</p>}
      <button
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
          <>
            <h3>{runStatusLabel(run.status)}</h3>
            {snapshot.source_kind === "chat" ? (
              <>
                {live && (
                  <RunStatus
                    activeRun={snapshot.run}
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
                {!live && snapshot.run.failure?.message && (
                  <p role="alert">{snapshot.run.failure.message}</p>
                )}
                {!live && (
                  <button
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
            <dl>
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
                    <dd>{v}</dd>
                  </div>
                ))}
            </dl>
            <button onClick={() => changeTab("resources")}>查看操作审批</button>
          </>
        )}
        {tab === "resources" && (
          <RunResources key={id} runId={id} terminal={terminal} onSaveFile={onSaveFile} />
        )}
        {tab === "advanced" && (
          <>
            <p>执行标识：{id}</p>
            <RunBudget budget={run.budget} />
            <TracePanel embedded />
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
