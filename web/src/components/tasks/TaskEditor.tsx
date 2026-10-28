import { Button, Field, Checkbox, TextField, Select, TextArea } from "../ui";
import { buildRequirement, hasLegacyGenericCriteria, localValue } from "./taskRequirement";
import { useEffect, useRef, useState } from "react";
import type { HpWork } from "../../api/types";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { useAuth } from "../../store/auth";
import { useWorkbench } from "../../store/workbench";
import { Surface } from "../shell/Surface";
import { useTaskQuery } from "./useTaskQuery";
import { taskCommand, useTaskOperations, retryTaskCommand } from "./taskOperations";
import { validSnapshot } from "./taskActions";
import { useWorkspace } from "../../store/workspace";
import { WorkspaceDirectoryLoader } from "../workspace/SaveToWorkspaceDialog";
import { nodePath } from "../workspace/workspacePresentation";
export function TaskEditor({ workId, onClose }: { workId?: string; onClose: () => void }) {
  const generation = useWorks((s) => s.generation);
  return (
    <BoundTaskEditor
      key={`${generation}:${workId ?? "create"}`}
      workId={workId}
      onClose={onClose}
    />
  );
}
function BoundTaskEditor({ workId, onClose }: { workId?: string; onClose: () => void }) {
  const query = useTaskQuery<{ work: HpWork }>(
    workId ? `editor:${workId}` : null,
    `/api/v1/works/${encodeURIComponent(workId ?? "")}`,
  );
  // Every editor open verifies the baseline; it is never inferred from a list row.
  useEffect(() => {
    if (workId) query.retry(); /* keyed by editor instance */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workId]);
  const [abandon, setAbandon] = useState(false);
  const dirty = useRef(false);
  const close = () => {
    if (dirty.current) setAbandon(true);
    else onClose();
  };
  return (
    <Surface
      className="hp-object-operation"
      motion="elevate"
      title={workId ? "修改任务要求" : "新建任务"}
      onClose={close}
    >
      {workId && query.error && (
        <p role="alert">
          {query.error}
          <Button type="button" onClick={query.retry}>
            重试要求
          </Button>
        </p>
      )}
      {workId && query.loading && <p role="status">正在核实当前要求…</p>}
      {(!workId || (query.data && query.error !== "对象不可用。")) && (
        <EditorForm
          work={query.data?.work}
          verifying={Boolean(workId && query.loading)}
          onClose={onClose}
          markDirty={() => {
            dirty.current = true;
          }}
        />
      )}
      {abandon && (
        <Surface
          className="hp-object-operation"
          motion="elevate"
          title="放弃未提交的任务草稿？"
          onClose={() => setAbandon(false)}
        >
          <p>尚未提交的要求会丢弃。</p>
          <Button type="button" onClick={() => setAbandon(false)}>
            继续编辑
          </Button>
          <Button type="button" onClick={onClose}>
            放弃草稿并关闭
          </Button>
        </Surface>
      )}
    </Surface>
  );
}
function EditorForm({
  work,
  verifying,
  onClose,
  markDirty,
}: {
  work?: HpWork;
  verifying: boolean;
  onClose: () => void;
  markDirty: () => void;
}) {
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [baseline, setBaseline] = useState(work);
  const [legacyUpgradeBaseline, setLegacyUpgradeBaseline] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [linkedConversation] = useState(useWorkbench.getState().activeConversationId);
  const [objective, setObjective] = useState(work?.requirement.objective ?? "");
  const [capability, setCapability] = useState(
    work?.requirement.capability_key ??
      (useShell.getState().route.type === "research" ? "research_report" : "reminder"),
  );
  const [constraints, setConstraints] = useState(work?.requirement.constraints.join("\n") ?? "");
  const [mode, setMode] = useState(String(work?.requirement.spec.reasoning_mode ?? "react"));
  const [timing, setTiming] = useState(work?.requirement.timing.kind ?? "immediate");
  const [zone, setZone] = useState(work?.requirement.timing.timezone ?? "Asia/Shanghai");
  const [due, setDue] = useState(localValue(work?.requirement.timing.due_at, zone));
  const [time, setTime] = useState(work?.requirement.timing.local_time ?? "09:00");
  const [timingChanged, setTimingChanged] = useState(false);
  const [saveDirectory, setSaveDirectory] = useState(
    String(work?.requirement.deliverable_policy?.directory_id ?? ""),
  );
  const [saveChanged, setSaveChanged] = useState(false);
  const [formats, setFormats] = useState(
    work?.requirement.deliverables?.items.map((d) => d.format).join(",") ?? "",
  );
  const [formatsChanged, setFormatsChanged] = useState(false);
  const tree = useWorkspace((s) => s.tree);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const generation = useWorks((s) => s.generation);
  const owner = baseline?.work_id ?? "task-create";
  const intent = useTaskOperations((s) => s.intents[owner]);
  const stored = useWorks((s) => s.items.find((w) => w.work_id === work?.work_id));
  const latest = work && (!stored || work.row_version >= stored.row_version) ? work : stored;
  const capabilities = useAuth((s) => s.capabilities);
  const supportedZone = ["Asia/Shanghai", "UTC"].includes(zone);
  useEffect(() => {
    if (dirty) useShell.setState({ dirtyTaskEditor: owner });
    return () => {
      if (useShell.getState().dirtyTaskEditor === owner)
        useShell.setState({ dirtyTaskEditor: null });
    };
  }, [dirty, owner]);
  if (
    work &&
    (!validSnapshot(work) ||
      !["active", "pausing", "paused"].includes(work.status) ||
      !["reminder", "generic_work", "research_report"].includes(work.requirement.capability_key))
  )
    return <p>此任务不可通过普通要求表单修改。</p>;
  return (
    <form
      className="hp-operation-form"
      onChange={() => {
        setDirty(true);
        markDirty();
      }}
      onSubmit={(e) => {
        e.preventDefault();
        if (intent?.busy || verifying) return;
        try {
          const requirement = buildRequirement(baseline?.requirement, {
            objective,
            capability,
            constraints,
            mode,
            timing,
            zone,
            due,
            time,
            timingChanged,
            saveDirectory,
            saveChanged,
            formats,
            formatsChanged,
            upgradeLegacyCriteria: legacyUpgradeBaseline === baseline?.row_version,
          });
          setError("");
          const body = baseline
            ? { requirement, change_reason: reason }
            : { title, requirement, conversation_id: linkedConversation };
          void taskCommand(
            owner,
            {
              method: "POST",
              path: baseline ? `/api/v1/works/${baseline.work_id}/revisions` : "/api/v1/works",
              body,
              ...(baseline
                ? { headers: { "If-Match": `"work-${baseline.work_id}-v${baseline.row_version}"` } }
                : {}),
            },
            async (snapshot) => {
              if (!snapshot) {
                if (baseline) await useWorks.getState().refresh(baseline.work_id);
                return;
              }
              useWorks.getState().upsert(snapshot);
              if (!alive.current || generation !== useWorks.getState().generation) return;
              useShell.setState({ dirtyTaskEditor: null });
              setDirty(false);
              onClose();
              if (snapshot)
                useShell.getState().openInspector({ kind: "task", objectId: snapshot.work_id });
            },
          );
        } catch (e) {
          setError(e instanceof Error ? e.message : "要求格式不正确。");
        }
      }}
    >
      {baseline && (
        <p>
          当前基线 r{baseline.current_requirement_revision}
          。修订将取消或收敛当前执行；暂停任务不会自动恢复。
        </p>
      )}
      {capability === "generic_work" && hasLegacyGenericCriteria(baseline?.requirement) && (
        <div role="alert">
          <p>
            此任务包含旧的操作回执验收条件。升级后，相关条件需要已发布的不可变成果及服务器登记的证据，成功工具调用不再满足要求。
            条件编号、必需标记和其他条件保留；提交修订不会直接完成任务或重置预算，旧要求和执行记录保留。
          </p>
          <Field>
            <Checkbox
              type="checkbox"
              checked={legacyUpgradeBaseline === baseline?.row_version}
              onChange={(e) =>
                setLegacyUpgradeBaseline(e.target.checked ? baseline!.row_version : null)
              }
            />
            我确认将操作回执条件升级为已发布成果条件
          </Field>
        </div>
      )}
      {latest && baseline && latest.row_version !== baseline.row_version && (
        <div role="alert">
          服务器已更新到 r{latest.current_requirement_revision}，草稿仍保留。
          <Button type="button" onClick={() => setBaseline(latest)}>
            已审阅最新要求，采用新基线提交
          </Button>
          <details>
            <summary>查看最新要求</summary>
            <pre>{JSON.stringify(latest.requirement, null, 2)}</pre>
          </details>
        </div>
      )}
      {!baseline && (
        <Field>
          工作名称
          <TextField
            required
            maxLength={200}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Field>
      )}
      <Field>
        工作类型
        <Select
          value={capability}
          disabled={Boolean(baseline)}
          onChange={(e) => setCapability(e.target.value)}
        >
          <option value="reminder">提醒</option>
          <option value="research_report">研究报告</option>
          <option value="generic_work">通用任务</option>
        </Select>
      </Field>
      <Field>
        目标 / 提醒内容
        <TextArea
          required
          maxLength={capability === "reminder" ? 12000 : 20000}
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
        />
      </Field>
      <Field>
        约束（每行一条）
        <TextArea value={constraints} onChange={(e) => setConstraints(e.target.value)} />
      </Field>
      {capability === "generic_work" && (
        <Field>
          交付格式
          <Select
            value={formats}
            onChange={(e) => {
              setFormats(e.target.value);
              setFormatsChanged(true);
            }}
          >
            <option value="">未限定（保留其他验收条件）</option>
            <option value="markdown">Markdown</option>
            <option value="docx">Word（DOCX）</option>
            <option value="markdown,docx">Markdown 和 Word</option>
            {formats && !["markdown", "docx", "markdown,docx"].includes(formats) && (
              <option value={formats}>保留当前多个交付目标</option>
            )}
          </Select>
        </Field>
      )}
      {capability === "generic_work" && (
        <Field>
          推理模式
          <Select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="react">ReAct</option>
            {(capabilities.agent_strategies?.includes("plan_and_execute") ||
              mode === "plan_and_execute") && <option value="plan_and_execute">计划后执行</option>}
          </Select>
        </Field>
      )}
      {capability !== "reminder" && (
        <>
          <WorkspaceDirectoryLoader />
          <Field>
            成果保存目录
            <Select
              value={saveDirectory}
              onChange={(e) => {
                setSaveDirectory(e.target.value);
                setSaveChanged(true);
              }}
            >
              <option value="">保留现有保存要求</option>
              {saveDirectory && !tree?.nodes?.some((n) => n.node_id === saveDirectory) && (
                <option value={saveDirectory}>当前目录（等待核实）</option>
              )}
              {tree?.nodes
                ?.filter((n) => n.kind === "directory")
                .map((n) => (
                  <option key={n.node_id} value={n.node_id}>
                    {nodePath(tree, n.node_id)}
                  </option>
                ))}
            </Select>
          </Field>
          <p>
            选定目录会要求保存成果。创建或修订后，请在任务「使用资料」中授予此目录「创建子项」权限；目录选择本身不授权。已有执行将按修订收敛，新授权下一轮生效。
          </p>
        </>
      )}
      {!supportedZone && <p>现有时区 {zone} 的计划只读，提交会保留其原始时间。</p>}
      <Field>
        执行时间
        <Select
          disabled={!supportedZone}
          value={timing}
          onChange={(e) => {
            setTiming(e.target.value);
            setTimingChanged(true);
          }}
        >
          <option value="immediate">立即</option>
          <option value="once">指定时间</option>
          <option value="daily">每天</option>
        </Select>
      </Field>
      <Field>
        计划时区
        <Select
          disabled={!supportedZone}
          value={zone}
          onChange={(e) => {
            setZone(e.target.value);
            setTimingChanged(true);
          }}
        >
          <option value="Asia/Shanghai">Asia/Shanghai（UTC+8）</option>
          <option value="UTC">UTC</option>
          {!supportedZone && <option value={zone}>{zone}</option>}
        </Select>
      </Field>
      {timing === "once" && (
        <Field>
          日期时间
          <TextField
            type="datetime-local"
            required
            disabled={!supportedZone}
            value={due}
            onChange={(e) => {
              setDue(e.target.value);
              setTimingChanged(true);
            }}
          />
        </Field>
      )}
      {timing === "daily" && (
        <Field>
          每天时间
          <TextField
            type="time"
            required
            disabled={!supportedZone}
            value={time}
            onChange={(e) => {
              setTime(e.target.value);
              setTimingChanged(true);
            }}
          />
        </Field>
      )}
      {baseline && (
        <Field>
          修订原因
          <TextArea required value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      )}
      {error && <p role="alert">{error}</p>}
      {intent?.error && <p role="alert">{intent.error}</p>}
      {intent?.uncertain && (
        <>
          <p>上次操作结果未知；重试会保留原要求、版本与幂等标识。</p>
          <Button
            type="button"
            disabled={intent.busy}
            onClick={() =>
              void retryTaskCommand(owner, async (snapshot) => {
                if (!snapshot) {
                  if (baseline) await useWorks.getState().refresh(baseline.work_id);
                  return;
                }
                useWorks.getState().upsert(snapshot);
                if (!alive.current || generation !== useWorks.getState().generation) return;
                useShell.setState({ dirtyTaskEditor: null });
                onClose();
                if (snapshot)
                  useShell.getState().openInspector({ kind: "task", objectId: snapshot.work_id });
              })
            }
          >
            重试原操作
          </Button>
          <details>
            <summary>查看原操作要求</summary>
            <pre>{JSON.stringify(intent.request.body, null, 2)}</pre>
          </details>
        </>
      )}
      <Button variant="solid" type="submit" disabled={intent?.busy || verifying}>
        {baseline ? "提交修订" : "创建工作"}
      </Button>
    </form>
  );
}
