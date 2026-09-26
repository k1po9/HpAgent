import { useState } from "react";
import { api as transport } from "../api/client";
import { HpApi } from "../api/resources";

const api = new HpApi(transport);

type Task = {
  task_id: string;
  title: string;
  status: string;
  output_directory_id: string | null;
  output_directory_name: string | null;
  output_required: boolean;
  output_operation: string;
  output_entry_id: string | null;
  schedule_type: string;
  schedule_timezone: string;
  schedule_expression: string | null;
};
type Run = {
  run_id: string;
  status: string;
  failure_code: string | null;
  save_status: string | null;
  save_entry_id: string | null;
  save_failure_code: string | null;
  published_file: {
    file_id: string;
    file_name: string;
    download_url: string;
  } | null;
};

export function ResearchOutputs({
  onSaveFile,
}: {
  onSaveFile?: (file: { file_id: string; file_name: string }) => void;
}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tasksBefore, setTasksBefore] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runsBefore, setRunsBefore] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<Awaited<ReturnType<HpApi["getWorkspace"]>> | null>(
    null,
  );
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [directoryId, setDirectoryId] = useState("");
  const [inputNodeId, setInputNodeId] = useState("");
  const [reports, setReports] = useState<Record<string, string>>({});
  const [scheduleTimezone, setScheduleTimezone] = useState("Asia/Shanghai");
  const [scheduleTime, setScheduleTime] = useState("09:00");

  function pathFor(nodeId: string | null): string {
    if (!workspace || !nodeId) return "未配置";
    const byId = new Map(workspace.nodes.map((node) => [node.node_id, node]));
    const segments: string[] = [];
    let current = byId.get(nodeId);
    while (current) {
      if (current.name) segments.unshift(current.name);
      current = current.parent_id ? byId.get(current.parent_id) : undefined;
    }
    return `/${segments.join("/")}`;
  }

  async function loadTasks(more = false) {
    try {
      const [page, tree] = await Promise.all([
        api.listResearchTasks(more ? tasksBefore : null),
        api.getWorkspace(),
      ]);
      setWorkspace(tree);
      if (!directoryId) {
        const first = tree.nodes.find((node) => node.kind === "directory" && node.parent_id);
        if (first) setDirectoryId(first.node_id);
      }
      setTasks(more ? [...tasks, ...page.items] : page.items);
      setTasksBefore(page.next_before);
      setError(null);
    } catch {
      setError("无法加载 Research Task 历史");
    }
  }

  async function createTask() {
    try {
      const created = await api.createResearchTask(
        title,
        objective,
        directoryId,
        crypto.randomUUID(),
      );
      if (inputNodeId) {
        await api.grantResearchInput(
          created.task.task_id,
          inputNodeId,
          workspace?.nodes.find((node) => node.node_id === inputNodeId)?.kind === "directory",
        );
      }
      setTitle("");
      setObjective("");
      await loadTasks();
    } catch {
      setError("创建 Task 失败");
    }
  }

  async function changeTarget(
    selected: string,
    operation: "create_child" | "update_content" = "create_child",
    entryId: string | null = null,
  ) {
    try {
      if (!taskId) return;
      await api.setResearchOutput(taskId, selected, operation, entryId);
      await loadTasks();
    } catch {
      setError("更新 Task 目标失败");
    }
  }

  async function loadRuns(selected: string, more = false) {
    try {
      const task = tasks.find((item) => item.task_id === selected);
      if (task) {
        setScheduleTimezone(task.schedule_timezone);
        setScheduleTime(task.schedule_expression ?? "09:00");
      }
      const page = await api.listResearchRuns(selected, more ? runsBefore : null);
      setTaskId(selected);
      setRuns(more ? [...runs, ...page.items] : page.items);
      setRunsBefore(page.next_before);
      setError(null);
    } catch {
      setError("无法加载 Research Run 历史");
    }
  }

  return (
    <details
      onToggle={(event) => {
        if (event.currentTarget.open) void loadTasks();
      }}
    >
      <summary>Research 输出</summary>
      {error ? <p role="alert">{error}</p> : null}
      <div>
        <input
          aria-label="Task 标题"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
        <input
          aria-label="Research 目标"
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
        />
        <select
          aria-label="Task 输出目录"
          value={directoryId}
          onChange={(event) => setDirectoryId(event.target.value)}
        >
          {workspace?.nodes
            .filter((node) => node.kind === "directory")
            .map((node) => (
              <option key={node.node_id} value={node.node_id}>
                {pathFor(node.node_id)}
              </option>
            ))}
        </select>
        <select
          aria-label="Task 输入文件或目录"
          value={inputNodeId}
          onChange={(event) => setInputNodeId(event.target.value)}
        >
          <option value="">无额外输入</option>
          {workspace?.nodes
            .filter((node) => node.name)
            .map((node) => (
              <option key={node.node_id} value={node.node_id}>
                {pathFor(node.node_id)}
              </option>
            ))}
        </select>
        <button
          type="button"
          disabled={!title.trim() || !objective.trim() || !directoryId}
          onClick={() => void createTask()}
        >
          创建 Research Task
        </button>
      </div>
      <div style={{ maxHeight: 180, overflowY: "auto" }}>
        {tasks.map((task) => (
          <div key={task.task_id}>
            <button type="button" onClick={() => void loadRuns(task.task_id)}>
              {task.title}
            </button>
            {task.output_directory_id ? (
              <small>
                目标：{pathFor(task.output_directory_id)} · {task.output_operation}
                {task.output_required ? " · required" : ""}
              </small>
            ) : null}
            <small>
              {" "}
              · 时区：{task.schedule_timezone}
              {task.schedule_type === "daily" ? ` · 每日 ${task.schedule_expression}` : " · 手动"}
            </small>
            {taskId === task.task_id ? (
              <>
                <input
                  aria-label="Task 时区"
                  value={scheduleTimezone}
                  onChange={(event) => setScheduleTimezone(event.target.value)}
                />
                <input
                  aria-label="Task 每日时间"
                  type="time"
                  value={scheduleTime}
                  onChange={(event) => setScheduleTime(event.target.value)}
                />
                <button
                  type="button"
                  onClick={() =>
                    void api
                      .setResearchSchedule(
                        task.task_id,
                        scheduleTimezone,
                        scheduleTime,
                        crypto.randomUUID(),
                      )
                      .then(() => loadTasks())
                      .catch(() => setError("保存 Task 日程失败"))
                  }
                >
                  保存日程
                </button>
                <select
                  aria-label="增加 Task 输入"
                  value={inputNodeId}
                  onChange={(event) => setInputNodeId(event.target.value)}
                >
                  <option value="">选择输入</option>
                  {workspace?.nodes
                    .filter((node) => node.name)
                    .map((node) => (
                      <option key={node.node_id} value={node.node_id}>
                        {pathFor(node.node_id)}
                      </option>
                    ))}
                </select>
                <button
                  type="button"
                  disabled={!inputNodeId}
                  onClick={() => {
                    const recursive =
                      workspace?.nodes.find((node) => node.node_id === inputNodeId)?.kind ===
                      "directory";
                    void api
                      .grantResearchInput(task.task_id, inputNodeId, recursive)
                      .catch(() => setError("授权 Task 输入失败"));
                  }}
                >
                  授权读取输入
                </button>
                <select
                  aria-label="Task 输出类型"
                  value={task.output_operation}
                  onChange={(event) => {
                    const operation = event.target.value as "create_child" | "update_content";
                    const entry = workspace?.nodes.find(
                      (node) =>
                        node.kind === "file" &&
                        node.parent_id === task.output_directory_id &&
                        node.destination_id,
                    );
                    if (task.output_directory_id && (operation === "create_child" || entry)) {
                      void changeTarget(
                        task.output_directory_id,
                        operation,
                        operation === "update_content" ? (entry?.node_id ?? null) : null,
                      );
                    }
                  }}
                >
                  <option value="create_child">创建新日报</option>
                  <option value="update_content">更新固定版本条目</option>
                </select>
                <select
                  aria-label="更换 Task 输出目录"
                  value={task.output_directory_id ?? ""}
                  onChange={(event) => void changeTarget(event.target.value)}
                >
                  {workspace?.nodes
                    .filter((node) => node.kind === "directory")
                    .map((node) => (
                      <option key={node.node_id} value={node.node_id}>
                        {pathFor(node.node_id)}
                      </option>
                    ))}
                </select>
                {task.output_operation === "update_content" ? (
                  <select
                    aria-label="Task 更新条目"
                    value={task.output_entry_id ?? ""}
                    onChange={(event) =>
                      task.output_directory_id &&
                      void changeTarget(
                        task.output_directory_id,
                        "update_content",
                        event.target.value,
                      )
                    }
                  >
                    {workspace?.nodes
                      .filter(
                        (node) =>
                          node.kind === "file" &&
                          node.parent_id === task.output_directory_id &&
                          node.destination_id,
                      )
                      .map((node) => (
                        <option key={node.node_id} value={node.node_id}>
                          {pathFor(node.node_id)}
                        </option>
                      ))}
                  </select>
                ) : null}
                <button
                  type="button"
                  onClick={() =>
                    void api
                      .triggerResearchTask(task.task_id, crypto.randomUUID())
                      .then(() => loadRuns(task.task_id))
                      .catch(() => setError("启动 Task 失败"))
                  }
                >
                  执行
                </button>
              </>
            ) : null}
          </div>
        ))}
        {tasksBefore ? (
          <button type="button" onClick={() => void loadTasks(true)}>
            更多 Task
          </button>
        ) : null}
        {taskId ? (
          <div aria-label="Research Run 历史">
            {runs.map((run) => (
              <div key={run.run_id}>
                <span>
                  {run.status} · {run.run_id.slice(0, 8)}
                </span>
                {run.save_status ? (
                  <span>
                    {" "}
                    · 长期保存：{run.save_status}
                    {run.save_entry_id ? ` · entry ${run.save_entry_id.slice(0, 8)}` : ""}
                    {run.save_failure_code ? ` · ${run.save_failure_code}` : ""}
                  </span>
                ) : null}
                {run.failure_code ? <span> · {run.failure_code}</span> : null}
                {run.published_file ? (
                  <a href={run.published_file.download_url}>下载 {run.published_file.file_name}</a>
                ) : null}
                <button
                  type="button"
                  onClick={() =>
                    taskId &&
                    void api
                      .getResearchReport(taskId, run.run_id)
                      .then((value) =>
                        setReports((current) => ({
                          ...current,
                          [run.run_id]: value.report.report_markdown,
                        })),
                      )
                      .catch(() => setError("报告加载失败"))
                  }
                >
                  查看报告
                </button>
                {reports[run.run_id] ? (
                  <pre style={{ whiteSpace: "pre-wrap" }}>{reports[run.run_id]}</pre>
                ) : null}
                {run.published_file && onSaveFile ? (
                  <button type="button" onClick={() => onSaveFile(run.published_file!)}>
                    {run.save_status === "failed" ? "另存已发布报告" : "保存到 Workspace"}
                  </button>
                ) : null}
              </div>
            ))}
            {runsBefore ? (
              <button type="button" onClick={() => void loadRuns(taskId, true)}>
                更多 Run
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </details>
  );
}
