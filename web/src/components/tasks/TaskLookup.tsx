import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
export function TaskLookup() {
  const generation = useWorks((s) => s.generation);
  const [runs, setRuns] = useState<Array<{ run_id: string; status: string }>>([]),
    [error, setError] = useState("");
  const [query, setQuery] = useState<{ workId: string; revision: number } | null>(null);
  useEffect(() => {
    if (!query) return;
    let valid = true;
    const abort = new AbortController();
    void api
      .request<{ items: typeof runs }>({
        method: "GET",
        path: `/api/v1/works/${encodeURIComponent(query.workId)}/runs`,
        signal: abort.signal,
      })
      .then((page) => {
        if (valid && generation === useWorks.getState().generation) setRuns(page.items);
      })
      .catch(() => {
        if (valid && generation === useWorks.getState().generation) setError("执行记录暂不可用。");
      });
    return () => {
      valid = false;
      abort.abort();
    };
  }, [query, generation]);
  return (
    <details className="hp-task-lookup">
      <summary>执行记录与诊断</summary>
      <p>高级查询：仅展示最近最多 100 次执行。任务详情中的成果与执行也提供此入口。</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const value = new FormData(e.currentTarget).get("work");
          const workId = typeof value === "string" ? value.trim() : "";
          if (!workId) {
            setError("请输入工作编号。");
            return;
          }
          setError("");
          setRuns([]);
          setQuery((old) => ({ workId, revision: (old?.revision ?? 0) + 1 }));
        }}
      >
        <label>
          工作编号
          <input name="work" required />
        </label>
        <button>读取执行记录</button>
      </form>
      {runs.map((run) => (
        <button
          key={run.run_id}
          onClick={() =>
            useShell.getState().openInspector({
              kind: "run",
              objectId: run.run_id,
              origin: query ? { workId: query.workId } : undefined,
            })
          }
        >
          {run.status} · {run.run_id}
        </button>
      ))}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const value = new FormData(e.currentTarget).get("run");
          if (typeof value === "string" && value.trim())
            useShell.getState().openInspector({ kind: "run", objectId: value.trim() });
        }}
      >
        <label>
          执行编号
          <input name="run" required />
        </label>
        <button>查询执行</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
