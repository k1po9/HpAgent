import { Field, TextField, Button } from "../ui";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
export function TaskLookup() {
  const generation = useWorks((s) => s.generation);
  const [runs, setRuns] = useState<Array<{ run_id: string; status: string }>>([]),
    [error, setError] = useState("");
  const [workError, setWorkError] = useState("");
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
            setWorkError("请输入工作编号。");
            setError("");
            return;
          }
          setWorkError("");
          setError("");
          setRuns([]);
          setQuery((old) => ({ workId, revision: (old?.revision ?? 0) + 1 }));
        }}
      >
        <Field label="工作编号" error={workError}>
          <TextField name="work" required onChange={() => setWorkError("")} />
        </Field>
        <Button variant="solid" type="submit">
          读取执行记录
        </Button>
      </form>
      {runs.map((run) => (
        <Button
          type="button"
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
        </Button>
      ))}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const value = new FormData(e.currentTarget).get("run");
          if (typeof value === "string" && value.trim())
            useShell.getState().openInspector({ kind: "run", objectId: value.trim() });
        }}
      >
        <Field>
          执行编号
          <TextField name="run" required />
        </Field>
        <Button variant="solid" type="submit">
          查询执行
        </Button>
      </form>
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
