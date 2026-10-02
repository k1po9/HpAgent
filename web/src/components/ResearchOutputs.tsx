import { useState } from "react";
import { api as transport } from "../api/client";
import { HpApi } from "../api/resources";

const api = new HpApi(transport);
type Work = Awaited<ReturnType<HpApi["listResearchWorks"]>>["items"][number];
type Run = Awaited<ReturnType<HpApi["listResearchRuns"]>>["items"][number];

export function ResearchOutputs({
  onSaveFile,
}: {
  onSaveFile?: (file: { file_id: string; file_name: string }) => void;
}) {
  const [works, setWorks] = useState<Work[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [reports, setReports] = useState<Record<string, string>>({});
  const [outputs, setOutputs] = useState<Record<string, Array<{ file_id: string; name: string }>>>(
    {},
  );
  const [error, setError] = useState<string | null>(null);

  async function loadWorks() {
    try {
      const page = await api.listResearchWorks();
      setWorks(page.items.filter((work) => work.requirement.capability_key === "research_report"));
      setError(null);
    } catch {
      setError("无法加载调查委托");
    }
  }

  async function loadRuns(workId: string) {
    try {
      setRuns((await api.listResearchRuns(workId)).items);
      setError(null);
    } catch {
      setError("无法加载调查执行");
    }
  }

  async function loadReport(runId: string) {
    try {
      const [report, files] = await Promise.all([
        api.getResearchReport(runId),
        api.listRunPublishedFiles(runId),
      ]);
      setReports((values) => ({ ...values, [runId]: report.report.report_markdown }));
      setOutputs((values) => ({ ...values, [runId]: files.files }));
      setError(null);
    } catch {
      setError("报告尚不可用");
    }
  }

  return (
    <details
      onToggle={(event) => {
        if (event.currentTarget.open) void loadWorks();
      }}
    >
      <summary>Research 输出</summary>
      {error ? <p role="alert">{error}</p> : null}
      {works.map((work) => (
        <div key={work.work_id}>
          <button type="button" onClick={() => void loadRuns(work.work_id)}>
            {work.title}
          </button>
          <small> {work.status}</small>
        </div>
      ))}
      {runs.map((run) => (
        <div key={run.run_id}>
          <button type="button" onClick={() => void loadReport(run.run_id)}>
            {run.run_id}
          </button>
          <small> {run.status}</small>
          {run.failure_code ? <p role="alert">{run.failure_code}</p> : null}
          {reports[run.run_id] ? (
            <pre style={{ whiteSpace: "pre-wrap" }}>{reports[run.run_id]}</pre>
          ) : null}
          {outputs[run.run_id]?.map((file) => (
            <div key={file.file_id}>
              <a href={`/api/v1/files/${file.file_id}/content`}>{file.name}</a>
              {onSaveFile ? (
                <button
                  type="button"
                  onClick={() =>
                    onSaveFile({
                      file_id: file.file_id,
                      file_name: file.name,
                    })
                  }
                >
                  保存
                </button>
              ) : null}
            </div>
          ))}
        </div>
      ))}
    </details>
  );
}
