import { useRef, useState } from "react";
import { api } from "../api/client";
import { type HpWork } from "../api/types";
import { useWorks } from "../store/works";
import { commandError, useCommandKey } from "../utils/commands";

type EditableWork = HpWork & {
  requirement: {
    spec: { reasoning_mode?: string; [key: string]: unknown };
    constraints: string[];
    acceptance_criteria: unknown[];
    resource_requests?: unknown[];
    deliverable_policy?: Record<string, unknown>;
    timing: { kind: string; local_time?: string; due_at?: string };
  };
};

import { useShell } from "../store/shell";
import { useWorkspace } from "../store/workspace";
import { GrantEditor } from "./workspace/GrantEditor";
import { WorkspaceDirectoryLoader } from "./workspace/SaveToWorkspaceDialog";
import { nodePath } from "./workspace/workspacePresentation";
import { WorkInputs } from "./tasks/TaskInputs";
import { type Subject } from "./workspace/workspaceOperations";

export function WorkCreateForm({
  conversationId,
  research = false,
}: {
  conversationId: string | null;
  research?: boolean;
}) {
  const works = useWorks((s) => s.items);
  const load = useWorks((s) => s.load);
  const [target, setTarget] = useState("");
  const [editingWork, setEditingWork] = useState<EditableWork | null>(null);
  const selection = useRef({ id: "" });
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [capability, setCapability] = useState(research ? "research_report" : "reminder");
  const [timing, setTiming] = useState("immediate");
  const [due, setDue] = useState("");
  const [time, setTime] = useState("09:00");
  const [mode, setMode] = useState("react");
  const [constraints, setConstraints] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const keyFor = useCommandKey();

  async function submit() {
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      const work = target ? editingWork : null;
      if (target && (!work || work.work_id !== target)) throw new Error("请先加载工作当前要求。");
      const spec =
        capability === "reminder"
          ? {
              schema_version: 1,
              content: objective,
              target_ref:
                work?.requirement.capability_key === capability
                  ? (work.requirement.spec.target_ref ?? "account_inbox")
                  : "account_inbox",
            }
          : capability === "research_report"
            ? {
                schema_version: 1,
                source_strategy:
                  work?.requirement.capability_key === capability
                    ? (work.requirement.spec.source_strategy ?? {})
                    : {},
              }
            : { schema_version: 1, reasoning_mode: mode };
      const requirement = {
        resource_requests: work?.requirement.resource_requests ?? [],
        deliverable_policy: work?.requirement.deliverable_policy ?? {
          schema_version: 1,
          required: false,
        },
        objective,
        capability_key: capability,
        spec,
        constraints: constraints
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
        acceptance_criteria:
          work && work.requirement.capability_key === capability
            ? work.requirement.acceptance_criteria
            : [
                {
                  id: "result",
                  required: true,
                  evidence_types: [
                    capability === "reminder"
                      ? "delivery_receipt"
                      : capability === "research_report"
                        ? "research_report"
                        : "operation_receipt",
                  ],
                },
              ],
        completion_mode: timing === "daily" ? "ongoing" : "deliverable",
        timing: {
          schema_version: 1,
          kind: timing,
          timezone: "Asia/Shanghai",
          ...(timing === "once"
            ? { due_at: new Date(due).toISOString() }
            : timing === "daily"
              ? { local_time: time }
              : {}),
        },
      };
      const body = work
        ? { requirement, change_reason: reason }
        : { title, requirement, conversation_id: conversationId };
      const result = await api.request<{ work: HpWork }>({
        method: "POST",
        path: work ? `/api/v1/works/${target}/revisions` : "/api/v1/works",
        body,
        idempotencyKey: keyFor({ target, body, version: work?.row_version }),
        ...(work ? { headers: { "If-Match": `"work-${target}-v${work.row_version}"` } } : {}),
      });
      setNotice(
        `${work ? "要求已修订" : "工作已创建"}：${result.work.title}。执行状态见下方列表。`,
      );
      await load();
      if (!work) {
        setTitle("");
        setObjective("");
      }
    } catch (cause) {
      setError(commandError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function chooseTarget(id: string) {
    const current = { id };
    selection.current = current;
    setTarget(id);
    setEditingWork(null);
    setError(null);
    setNotice("");
    if (!id) {
      setTitle("");
      setObjective("");
      setCapability(research ? "research_report" : "reminder");
      setTiming("immediate");
      setConstraints("");
      return;
    }
    try {
      const { work } = await api.request<{ work: EditableWork }>({
        method: "GET",
        path: `/api/v1/works/${id}`,
      });
      if (selection.current !== current) return;
      setEditingWork(work);
      setTitle(work.title);
      setObjective(work.requirement.objective);
      setCapability(work.requirement.capability_key);
      setConstraints((work.requirement.constraints ?? []).join("\n"));
      setTiming(work.requirement.timing.kind);
      setTime(work.requirement.timing.local_time ?? "09:00");
      setMode(work.requirement.spec.reasoning_mode ?? "react");
      if (work.requirement.timing.due_at) {
        const d = new Date(work.requirement.timing.due_at);
        setDue(new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16));
      }
    } catch (cause) {
      if (selection.current === current) setError(commandError(cause));
    }
  }

  return (
    <form
      className="hp-operation-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h2>{research ? "创建研究或修订要求" : "创建工作或修订要求"}</h2>
      <label>
        操作对象
        <select disabled={busy} value={target} onChange={(e) => void chooseTarget(e.target.value)}>
          <option value="">新建{research ? "研究" : "工作"}</option>
          {works
            .filter(
              (w) =>
                ["reminder", "generic_work", "research_report"].includes(
                  w.requirement.capability_key,
                ) &&
                (!research || w.requirement.capability_key === "research_report"),
            )
            .map((w) => (
              <option key={w.work_id} value={w.work_id}>
                {w.title}
              </option>
            ))}
        </select>
      </label>
      {!target && (
        <label>
          工作名称
          <input
            required
            maxLength={200}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
      )}
      {!research && (
        <label>
          工作类型
          <select value={capability} onChange={(e) => setCapability(e.target.value)}>
            <option value="reminder">提醒（投递到账户收件箱）</option>
            <option value="generic_work">通用工作</option>
            <option value="research_report">研究报告</option>
          </select>
        </label>
      )}
      <label>
        目标 / 提醒内容
        <textarea
          required
          maxLength={12000}
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
        />
      </label>
      <label>
        执行时间
        <select value={timing} onChange={(e) => setTiming(e.target.value)}>
          <option value="immediate">立即</option>
          <option value="once">指定时间</option>
          <option value="daily">每天（持续工作）</option>
        </select>
      </label>
      {timing === "once" && (
        <label>
          日期时间
          <input
            required
            type="datetime-local"
            value={due}
            onChange={(e) => setDue(e.target.value)}
          />
        </label>
      )}
      {timing === "daily" && (
        <label>
          每天时间（上海）
          <input required type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </label>
      )}
      {capability === "generic_work" && (
        <label>
          执行策略
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="react">逐步执行</option>
            <option value="plan_and_execute">先规划后执行</option>
          </select>
        </label>
      )}
      <label>
        约束（每行一条）
        <textarea value={constraints} onChange={(e) => setConstraints(e.target.value)} />
      </label>
      {target && (
        <label>
          修订原因
          <input
            required
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
      )}
      <p>工作资料需在“资料授权”页单独授权。提醒投递到本账户收件箱。</p>
      <button disabled={busy || Boolean(target && !editingWork)}>
        {busy ? "提交中…" : target ? "提交修订" : research ? "创建研究" : "创建工作"}
      </button>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </form>
  );
}

export function WorkResourcePanel() {
  const works = useWorks((s) => s.items);
  const [subject, setSubject] = useState<Subject>();
  const [nodeId, setNodeId] = useState("");
  const tree = useWorkspace((s) => s.tree);
  const node = tree?.nodes.find((n) => n.node_id === nodeId);
  return (
    <section className="hp-operation-form">
      <h2>工作资料与输入</h2>
      <WorkspaceDirectoryLoader />
      <label>
        选择工作
        <select
          value={subject?.id ?? ""}
          onChange={(e) => {
            const work = works.find((w) => w.work_id === e.target.value);
            useShell
              .getState()
              .requestResourceChange(() =>
                setSubject(
                  work ? { kind: "work", id: work.work_id, title: work.title } : undefined,
                ),
              );
          }}
        >
          <option value="">请选择</option>
          {works.map((w) => (
            <option key={w.work_id} value={w.work_id}>
              {w.title}
            </option>
          ))}
        </select>
      </label>
      <label>
        选择长期目录或文件
        <select
          value={nodeId}
          onChange={(e) => {
            const next = e.target.value;
            useShell.getState().requestResourceChange(() => setNodeId(next));
          }}
        >
          <option value="">请选择</option>
          {tree?.nodes.map((n) => (
            <option key={n.node_id} value={n.node_id}>
              {nodePath(tree, n.node_id)}
            </option>
          ))}
        </select>
      </label>
      {subject && node && <GrantEditor node={node} subject={subject} />}
      {subject && <WorkInputs key={subject.id} subject={subject} fileId={node?.file_id ?? null} />}
    </section>
  );
}
